"""V4 settlement, V5 classes, V8 failure line: on real traps and on hand-made captures."""
import sys
from pathlib import Path

import pytest

from harel_lexsi.verifier import (
    CaptureResult,
    FailureClass,
    RowChange,
    Step,
    Verdict,
    classify,
    failure_lines,
    load_binding,
    load_holdout,
    replay,
    settle,
)
from harel_lexsi.verifier.replay import SHOP

ROOT = Path(__file__).parents[1]
BINDING = load_binding(ROOT / "spec" / "binding.yaml")
CONTRACTS = {c.id: c for c in load_holdout(ROOT / "tests" / "fixtures" / "shop_contracts.yaml", BINDING)}
sys.path.insert(0, str(SHOP))


def handlers(name):
    import importlib
    return importlib.import_module(name)


def run(cid, module):
    c = CONTRACTS[cid]
    s = replay(c, handlers(module))
    return settle(c, s.capture, s.error, BINDING)


# ---------- real handlers and traps (needs Postgres) ----------

@pytest.fixture(autouse=False)
def needs_db():
    import psycopg
    try:
        psycopg.connect("postgresql://shop:shop@localhost:5433/shop", connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")


@pytest.mark.parametrize("cid", ["F-01", "F-02", "F-03"])
def test_reference_handlers_pass(needs_db, cid):
    s = run(cid, "reference.handlers")
    assert s.verdict is Verdict.PASS, s


def test_trap_reserve_on_add_is_an_undeclared_write_to_inventory(needs_db):
    s = run("F-01", "reference.traps.reserve_on_add")
    assert s.verdict is Verdict.FAIL
    f = classify(s)
    assert [(x.failure_class, x.resource, x.step) for x in f] == [
        (FailureClass.UNDECLARED_WRITE, "inventory", 1)]
    assert failure_lines(f, visible_resources={"inventory"}) == [
        "step 1 wrote to inventory, which this task must not touch"]


def test_trap_checkout_without_event_is_a_missing_event(needs_db):
    s = run("F-02", "reference.traps.checkout_no_event")
    assert s.verdict is Verdict.FAIL
    assert [x.failure_class for x in classify(s)] == [FailureClass.MISSING_EVENT]


def test_unimplemented_handler_is_a_fail_not_a_pass(needs_db):
    s = run("F-01", "app.handlers")  # stubs raise NotImplementedError
    assert s.verdict is Verdict.FAIL


# ---------- hand-made captures ----------

def ch(table, op, before=None, after=None):
    return RowChange(table, op, before, after)


def cap(*changes, complete=True):
    return CaptureResult(steps=[Step(3, "scenario", list(changes))], complete=complete)


F01_OK = [ch("carts", "insert", None, {"id": 1, "user_id": 1, "status": "open"}),
          ch("cart_items", "insert", None, {"cart_id": 1, "product_id": 1, "qty": 2})]


def test_three_verdicts_on_fixtures():
    c = CONTRACTS["F-01"]
    assert settle(c, cap(*F01_OK), None, BINDING).verdict is Verdict.PASS
    assert settle(c, cap(F01_OK[0]), None, BINDING).verdict is Verdict.FAIL
    assert settle(c, CaptureResult(), None, BINDING).verdict is Verdict.UNKNOWN
    assert settle(c, cap(*F01_OK, complete=False), None, BINDING).verdict is Verdict.UNKNOWN


def test_unknown_never_counts_as_pass():
    from harel_lexsi.adapters.rewards import verdict_to_reward
    s = settle(CONTRACTS["F-01"], CaptureResult(), None, BINDING)
    assert s.verdict is Verdict.UNKNOWN and verdict_to_reward(s.as_result()) == 0.0


def test_half_commit_is_broken_atomicity():
    s = settle(CONTRACTS["F-01"], cap(F01_OK[0]), None, BINDING)  # cart created, line never written
    assert [x.failure_class for x in classify(s)] == [FailureClass.BROKEN_ATOMICITY]
    assert classify(s)[0].step == 3


def test_nothing_written_is_a_missing_write():
    s = settle(CONTRACTS["F-01"], cap(), None, BINDING)
    assert {x.failure_class for x in classify(s)} == {FailureClass.MISSING_WRITE}


def test_wrong_number_of_rows_is_wrong_cardinality():
    twice = [*F01_OK, ch("cart_items", "insert", None, {"cart_id": 1, "product_id": 1, "qty": 2})]
    s = settle(CONTRACTS["F-01"], cap(*twice), None, BINDING)
    assert [x.failure_class for x in classify(s)] == [FailureClass.WRONG_CARDINALITY]


def test_wrong_after_value_is_wrong_final_state():
    bad = [F01_OK[0], ch("cart_items", "insert", None, {"cart_id": 1, "product_id": 1, "qty": 9})]
    s = settle(CONTRACTS["F-01"], cap(*bad), None, BINDING)
    assert [x.failure_class for x in classify(s)] == [FailureClass.WRONG_FINAL_STATE]


def test_failure_path_that_writes_is_a_fail():
    c = CONTRACTS["F-03"]
    assert settle(c, cap(), "out_of_stock", BINDING).verdict is Verdict.PASS
    assert settle(c, cap(), None, BINDING).verdict is Verdict.FAIL          # error not raised
    assert settle(c, cap(F01_OK[0]), "out_of_stock", BINDING).verdict is Verdict.FAIL  # wrote anyway


# ---------- V8: no contract leak ----------

def test_failure_line_never_contains_contract_text():
    import yaml

    from harel_lexsi.verifier import parse_contracts
    raw = yaml.safe_load("""
    - id: ZZ-SENTINEL-77
      handler: sentinel_handler
      given: sentinelstate with secret words
      input: { sentinel_in: sentinelvalue }
      expect_error: sentinel_error
      effects: []
      closure: true
    - id: ZZ-SENTINEL-78
      handler: sentinel_handler
      effects:
        - { id: sentinel_e1, resource: sentinelres, op: update, count: 2, match: { sentinel_f: sentinelvalue }, change: { sentinel_f: "+1" } }
        - { id: sentinel_e2, resource: sentinelres, op: insert, count: 1 }
        - { id: sentinel_e3, resource: sentinelres, op: emit, count: 1, event: sentinel.event }
      atomic: [sentinel_e1, sentinel_e2, sentinel_e3]
      closure: true
    """)
    contracts = parse_contracts(raw)
    binding = load_binding.__globals__["Binding"]({"sentinelres": "sentinel_table"})
    stray = ch("sentinel_table", "insert", None, {"sentinel_f": "sentinelvalue"})
    captures = [cap(stray), cap(ch("sentinel_table", "update", {"sentinel_f": 1}, {"sentinel_f": 9})), cap()]
    lines = []
    for c in contracts:
        for cp in captures:
            for err in (None, "sentinel_error", "crash:SentinelError"):
                s = settle(c, cp, err, binding)
                lines += failure_lines(classify(s), visible_resources={"inventory"})
    assert lines, "expected some FAIL lines to check"
    for line in lines:
        assert "sentinel" not in line.lower() and "zz-" not in line.lower() and "secret" not in line.lower()
        assert line.startswith("step 3 ")
