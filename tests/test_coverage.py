"""V9: every holdout effect must be planned by some task, and the report reveals nothing about scenarios."""
import json

import yaml

from harel_lexsi.verifier import coverage as V
from harel_lexsi.verifier import gate as G
from harel_lexsi.verifier import load_binding, load_holdout, parse_contracts

BINDING = load_binding(G.DEFAULT_BINDING)
TASKS = [json.loads(line) for line in V.DEFAULT_TASKS.read_text().splitlines() if line.strip()]
FIXTURE_CONTRACTS = [c for f in G.DEFAULT_CONTRACTS for c in load_holdout(f, BINDING)]


def contracts(text):
    return parse_contracts(yaml.safe_load(text), BINDING)


def test_the_test_fixture_contracts_are_all_planned_by_the_task_set():
    assert V.uncovered(FIXTURE_CONTRACTS, TASKS, BINDING) == {}


def test_an_effect_no_task_plans_is_reported_with_its_count():
    cs = contracts("""
    - id: U-1
      handler: cancel_order
      effects:
        - { id: e1, resource: coupons, op: update, count: 1 }
        - { id: e2, resource: coupons, op: update, count: 1 }
        - { id: e3, resource: orders, op: update, count: 1 }
    """)
    assert V.uncovered(cs, TASKS, BINDING) == {"coupons:update": 2}


def test_removing_a_task_plan_opens_a_gap():
    without_events = [{**t, "planned_effects": [e for e in t["planned_effects"] if e != "outbox:emit"]}
                      for t in TASKS]
    gaps = V.uncovered(FIXTURE_CONTRACTS, without_events, BINDING)
    wanted = sum(1 for c in FIXTURE_CONTRACTS for e in c.effects if e.op == "emit")
    assert gaps == {"outbox:emit": wanted} and wanted > 0


def test_read_only_and_failure_path_contracts_need_no_plan():
    cs = contracts("""
    - { id: R-1, handler: list_orders, input: { user_id: 1 }, effects: [], closure: true }
    - { id: R-2, handler: checkout, input: { user_id: 1 }, expect_error: empty_cart, effects: [], closure: true }
    """)
    assert V.uncovered(cs, [], BINDING) == {}


def test_a_resource_pattern_is_compared_through_the_binding():
    binding = type(BINDING)({"order*": "orders", **BINDING.mapping})
    cs = parse_contracts(yaml.safe_load("""
    - { id: G-1, handler: cancel_order, effects: [ { id: e1, resource: order-main, op: update, count: 1 } ] }
    """), binding)
    assert V.uncovered(cs, TASKS, binding) == {}


def test_the_report_reveals_no_scenario_text(capsys, tmp_path):
    secret = tmp_path / "secret.yaml"
    secret.write_text("""
contracts:
  - id: ZZ-SECRET-9
    handler: cancel_order
    given: sentinelstate with secret words
    input: { user_id: 777123, order_id: 888456 }
    effects:
      - { id: sentinel_e1, resource: coupons, op: update, count: 1, match: { code: SENTINELCODE }, change: { active: false } }
    atomic: [sentinel_e1]
    closure: true
""")
    assert V.main(["--contracts", str(secret)]) == 1
    out = capsys.readouterr().out
    assert "1 on coupons:update" in out
    for leak in ("ZZ-SECRET", "sentinel", "SENTINEL", "777123", "888456", "secret", "cancel_order"):
        assert leak not in out


def test_cli_passes_on_the_fixtures_and_fails_when_a_task_plan_is_missing(tmp_path, capsys):
    files = [str(f) for f in G.DEFAULT_CONTRACTS]
    assert V.main(["--contracts", *files]) == 0
    assert "OK" in capsys.readouterr().out
    trimmed = tmp_path / "tasks.jsonl"
    trimmed.write_text("".join(json.dumps({**t, "planned_effects": []}) + "\n" for t in TASKS))
    assert V.main(["--contracts", *files, "--tasks", str(trimmed)]) == 1


def test_cli_cannot_run_without_contracts_or_tasks(tmp_path, capsys):
    assert V.main([]) == 2
    real = (G.ROOT / "spec" / "spec.md").read_text()
    head, _, _ = real.partition("### Contracts")
    empty = tmp_path / "spec.md"
    empty.write_text(head + "### Contracts\n\n```yaml\ncontracts: []\n```\n")
    assert V.main(["--spec", str(empty)]) == 2  # a spec with no holdout contracts
    assert "no contracts" in capsys.readouterr().err
    assert V.main(["--contracts", *map(str, G.DEFAULT_CONTRACTS), "--tasks", str(tmp_path / "nope.jsonl")]) == 2
