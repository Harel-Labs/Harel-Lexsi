"""G1: the factory version rides on every harel.step marker and comes out of capture on every Step."""
import json
import uuid

import psycopg
import pytest

from harel_lexsi.verifier.capture import Capture, attribute, marker_payload

DSN = "postgresql://shop:shop@localhost:5433/shop"


def line(payload: str) -> str:
    return f"message: transactional: 1 prefix: harel.step, sz: {len(payload)} content:{payload}"


ROW = "table public.cart_items: INSERT: cart_id[integer]:1 product_id[integer]:1 qty[integer]:2"


def test_unpinned_marker_is_exactly_what_it_was_before():
    assert marker_payload("ep1", 3, "write_file", "start") == (
        '{"episode": "ep1", "step": 3, "tool": "write_file", "phase": "start"}')


def test_pinned_marker_carries_the_version():
    info = json.loads(marker_payload("ep1", 3, "write_file", "end", "v0007"))
    assert info == {"episode": "ep1", "step": 3, "tool": "write_file", "phase": "end",
                    "factory_version": "v0007"}


def test_capture_puts_the_version_on_every_step():
    lines = []
    for n, tool in enumerate(["read_file", "write_file", "finish"], start=1):
        lines += [line(marker_payload("ep1", n, tool, "start", "v0002")), ROW,
                  line(marker_payload("ep1", n, tool, "end", "v0002"))]
    res = attribute(lines)
    assert res.complete and len(res.steps) == 3
    assert [s.factory_version for s in res.steps] == ["v0002"] * 3


def test_unpinned_steps_have_no_version():
    res = attribute([line(marker_payload("ep1", 1, "read_file", "start")), ROW,
                     line(marker_payload("ep1", 1, "read_file", "end"))])
    assert res.steps[0].factory_version is None and res.complete


def test_version_survives_the_real_wal():
    """Emit markers into a real Postgres, read them back through Capture."""
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        with Capture(DSN, slot=f"g1_{uuid.uuid4().hex[:8]}") as cap:
            for n, tool in enumerate(["read_file", "finish"], start=1):
                conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)",
                             (marker_payload("ep9", n, tool, "start", "v0003"),))
                conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)",
                             (marker_payload("ep9", n, tool, "end", "v0003"),))
            res = cap.read()
    finally:
        conn.close()
    assert res.complete
    assert [(s.step, s.tool, s.factory_version) for s in res.steps] == [
        (1, "read_file", "v0003"), (2, "finish", "v0003")]
