"""V1 + V2: parse test_decoding output, attribute changes to steps, read the real WAL."""
import json
import os

import psycopg
import pytest

from harel_lexsi.verifier.capture import Capture, attribute, parse_row, parse_tuple

DSN = os.environ.get("SHOP_DSN", "postgresql://shop:shop@localhost:5433/shop")


def marker(step, phase, tool="t"):
    body = json.dumps({"episode": "e", "step": step, "tool": tool, "phase": phase})
    return f"message: transactional: 1 prefix: harel.step, sz: {len(body)} content:{body}"


def test_parse_tuple_handles_quotes_nulls_json_and_types():
    t = parse_tuple("id[integer]:7 name[text]:'it''s ok' note[text]:null ok[boolean]:true "
                    "payload[jsonb]:'{\"a\": [1, 2]}' at[timestamp with time zone]:'2026-01-01 00:00:00+00'")
    assert t == {"id": 7, "name": "it's ok", "note": None, "ok": True,
                 "payload": {"a": [1, 2]}, "at": "2026-01-01 00:00:00+00"}


def test_parse_row_insert_update_delete():
    ins = parse_row("table public.carts: INSERT: id[integer]:1 status[text]:'open'")
    upd = parse_row("table public.inventory: UPDATE: old-key: product_id[integer]:1 on_hand[integer]:50 "
                    "new-tuple: product_id[integer]:1 on_hand[integer]:48")
    dele = parse_row("table public.cart_items: DELETE: cart_id[integer]:1 product_id[integer]:1 qty[integer]:2")
    assert (ins.op, ins.before, ins.after) == ("insert", None, {"id": 1, "status": "open"})
    assert (upd.before["on_hand"], upd.after["on_hand"]) == (50, 48)
    assert (dele.op, dele.before["qty"], dele.after) == ("delete", 2, None)
    assert parse_row("BEGIN") is None


def test_every_change_belongs_to_exactly_one_step():
    lines = [
        "BEGIN", "table public.carts: INSERT: id[integer]:9", "COMMIT",       # before any step
        marker(1, "start"), "BEGIN", "table public.carts: INSERT: id[integer]:1", "COMMIT", marker(1, "end"),
        marker(2, "start", "other"), "table public.orders: INSERT: id[integer]:2", marker(2, "end", "other"),
    ]
    r = attribute(lines)
    assert r.complete
    assert [(s.step, s.tool, [c.table for c in s.changes]) for s in r.steps] == [
        (1, "t", ["carts"]), (2, "other", ["orders"])]
    assert [c.table for c in r.unattributed] == ["carts"]


def test_unclosed_step_marks_capture_incomplete():
    assert not attribute([marker(1, "start"), "table public.carts: INSERT: id[integer]:1"]).complete


def test_events_come_from_outbox_inserts_in_the_same_step():
    r = attribute([marker(1, "start"),
                   "table public.outbox: INSERT: id[bigint]:1 event[text]:'order.placed' payload[jsonb]:'{\"order_id\": 3}'",
                   marker(1, "end")])
    ev = r.steps[0].events
    assert [(e.name, e.payload) for e in ev] == [("order.placed", {"order_id": 3})]


@pytest.fixture
def db():
    from harel_lexsi.verifier.replay import _shop_db
    try:
        mod = _shop_db()
        conn = mod.connect()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")
    mod.migrate(conn)
    yield mod, conn
    conn.close()


def test_real_wal_changes_have_before_and_after_values(db):
    mod, conn = db
    import sys
    sys.path.insert(0, str(mod.ROOT))
    from reference import handlers as h
    mod.reset(conn)
    with Capture(DSN, slot="t_capture") as cap:
        conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)",
                     (json.dumps({"episode": "e", "step": 1, "tool": "x", "phase": "start"}),))
        h.add_to_cart(conn, 1, 1, 2)
        h.checkout(conn, 1)
        conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)",
                     (json.dumps({"episode": "e", "step": 1, "tool": "x", "phase": "end"}),))
        r = cap.read()
    step = r.steps[0]
    inv = next(c for c in step.changes if c.table == "inventory")
    assert (inv.before["on_hand"], inv.after["on_hand"]) == (50, 48)
    assert [e.name for e in step.events] == ["order.placed"] and r.complete and not r.unattributed


def test_slot_does_not_survive_a_dropped_session(db):
    _, conn = db
    cap = Capture(DSN, slot="t_crash")
    assert conn.execute("SELECT count(*) AS n FROM pg_replication_slots WHERE slot_name='t_crash'").fetchone()["n"] == 1
    cap._conn.close()  # simulate the process dying without cleanup
    import time
    for _ in range(20):
        n = conn.execute("SELECT count(*) AS n FROM pg_replication_slots WHERE slot_name='t_crash'").fetchone()["n"]
        if n == 0:
            break
        time.sleep(0.1)
    assert n == 0
