"""A2: how the V7 mutants are shown to the judge, and how approvals are counted. No database, no network."""
import ast
import json
import sys

import pytest

from harel_lexsi.adapters import mutant_judge as MJ
from harel_lexsi.verifier.gate import TRAPS
from harel_lexsi.verifier.replay import ROOT, SHOP

sys.path.insert(0, str(SHOP))

from reference import mutants as M  # noqa: E402

TASKS = {t["id"]: t for t in map(json.loads, (ROOT / "tasks" / "tasks.jsonl").read_text().splitlines())}
ITEMS = MJ.build_items(TASKS, TRAPS)
BY_ID = {i.id: i for i in ITEMS}


# ---------- natural code ----------

def test_presentable_deletes_a_skip_statement_and_keeps_the_rest():
    src = "def f(conn):\n    a = 1\n    _skip(\n        'x',\n        (a,),\n    )\n    return a\n"
    assert MJ.presentable(src) == "def f(conn):\n    a = 1\n    return a\n"


def test_presentable_leaves_a_pass_when_the_block_would_be_empty():
    out = MJ.presentable("def f(conn, c):\n    if c:\n        _skip(conn, 'e')\n    return 1\n")
    assert "_skip" not in out and "        pass\n" in out
    ast.parse(out)


def test_presentable_does_not_touch_code_without_the_placeholder():
    src = "def f(conn):\n    return conn.execute('SELECT 1').fetchone()\n"
    assert MJ.presentable(src) == src


@pytest.mark.parametrize("m", M.MUTANTS, ids=lambda m: m.id)
def test_every_mutant_reads_as_ordinary_code_and_differs_from_the_reference(m):
    item = BY_ID[m.id]
    tree = ast.parse(item.code)
    assert "_skip" not in item.code
    assert [n.name for n in tree.body if isinstance(n, ast.FunctionDef)] == [m.handler]
    assert item.code != BY_ID[f"reference-{m.handler}"].code


# ---------- the item set ----------

def test_the_set_is_17_mutants_2_traps_and_the_correct_version_of_each_handler():
    kinds = [i.kind for i in ITEMS]
    assert (kinds.count("mutant"), kinds.count("trap"), kinds.count("control")) == (17, 2, 6)
    assert {i.handler for i in ITEMS if i.kind == "control"} == {i.handler for i in ITEMS if i.broken}
    assert all(i.target.startswith("app/handlers/") for i in ITEMS)


def test_a_trap_shows_the_function_it_wraps():
    trap = BY_ID["checkout_no_event"]
    assert "def _checkout(conn" in trap.code and "def checkout(conn" in trap.code
    assert "from reference" not in trap.code  # no import line that names the reference
    ast.parse(trap.code)


def test_the_judge_is_never_shown_the_mutants_note_its_id_or_the_word_mutant():
    helpers = MJ.HELPERS.read_text()
    for m in M.MUTANTS:
        item = BY_ID[m.id]
        shown = item.code + MJ.judge_task(TASKS[f"implement-{m.handler}"]["prompt"], helpers)
        assert m.why not in shown and m.id not in shown
        assert "mutant" not in shown.lower() and "_skip" not in shown


def test_the_task_text_is_the_builders_prompt_plus_the_helpers():
    text = MJ.judge_task("Implement checkout", "def emit(conn, event, payload): ...")
    assert text.startswith("Implement checkout") and "def emit(conn, event, payload)" in text


# ---------- what the judge reads ----------

def test_trajectory_carries_the_code_and_the_test_result_only_when_asked():
    item = BY_ID["checkout-stale-discount"]
    with_tests = MJ.as_trajectory(item, "task", {"passed": 5, "failed": 0})
    code_only = MJ.as_trajectory(item, "task", None)
    assert with_tests.steps[0].action["tool_calls"][0]["arguments"]["content"] == item.code
    assert "5 passed, 0 failed" in with_tests.steps[0].observation
    assert "passed" not in code_only.steps[0].observation


# ---------- counting ----------

def row(kind, w, c, vf="fail", vh="fail", vp=1, vfail=0):
    return {"id": "x", "kind": kind, "handler": "h", "judge_with_tests": w, "judge_code_only": c, "verifier_fixtures": vf,
            "verifier_hidden": vh, "visible_passed": vp, "visible_failed": vfail}


def test_only_a_score_of_one_is_an_approval_and_unknown_is_neither():
    assert MJ.approved(1.0) is True and MJ.approved(0.5) is False and MJ.approved(None) is None
    assert MJ.approved(0.5, MJ.LOOSE_AT) is True
    c = MJ.count([row("mutant", 1.0, 0.0), row("mutant", 0.5, 0.5), row("mutant", None, 0.0)], "judge_with_tests")
    assert c == {"n": 3, "approved": 1, "rejected": 1, "unknown": 1}


def test_summary_keeps_broken_and_control_and_the_two_rewards_apart():
    rows = [row("mutant", 1.0, 0.5), row("mutant", 0.0, 0.0, vh="pass"), row("trap", 0.0, 0.0, vf="unknown"),
            row("control", 1.0, 1.0, vf="pass", vh="pass"), row("control", 0.5, 1.0, vf="pass", vh="pass")]
    s = MJ.summarise(rows)
    assert s["broken"]["judge_with_tests"] == {"n": 3, "approved": 1, "rejected": 2, "unknown": 0}
    assert s["broken"]["judge_with_tests_loose_line"]["approved"] == 1 and s["broken"]["judge_code_only_loose_line"]["approved"] == 1
    assert s["broken"]["verifier_hidden_contracts"] == {"n": 3, "approved": 1, "rejected": 2, "unknown": 0}
    assert s["broken"]["verifier_fixtures"] == {"n": 3, "approved": 0, "rejected": 2, "unknown": 1}
    assert s["mutants"]["judge_with_tests"]["n"] == 2 and s["traps"]["judge_with_tests"]["n"] == 1
    assert s["control"]["judge_with_tests"] == {"n": 2, "approved": 1, "rejected": 1, "unknown": 0}
    text = MJ.render_markdown({"judge": "m"}, s, [{**r, "judge_with_tests_why": "because"} for r in rows])
    assert "| **LLM judge, code + visible tests, score 1.0 (headline)** | 1 of 3 |" in text and "1 UNKNOWN" in text
