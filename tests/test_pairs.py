"""T8 pair building: verifier verdicts in, preference pairs out. A fake scorer stands in for the verifier."""
import json

import pytest

from harel_lexsi.adapters import pairs as P
from harel_lexsi.adapters.pairs import Candidate

TASK = {"id": "implement-clear_cart", "handler": "clear_cart", "prompt": "Implement clear_cart", "target": "app/handlers/cart.py"}
TASKS = {TASK["id"]: TASK, "implement-get_order": {"id": "implement-get_order", "handler": "get_order", "prompt": "p2",
                                                    "target": "app/handlers/orders.py"}}


def cand(code, verdict, *, classes=(), visible=False, seen=1, task="implement-clear_cart"):
    return Candidate(task, P.normalise(code), verdict, list(classes), visible, seen, P.fingerprint(task, code))


def messages(task):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": task["prompt"]}]


# ---------- normalisation and candidates ----------

def test_whitespace_differences_are_the_same_answer():
    assert P.normalise("def f():  \r\n    return 1\r\n\r\n") == "def f():\n    return 1\n"
    assert P.fingerprint("t", "def f():\n    return 1\n") == P.fingerprint("t", "def f():   \n    return 1")
    assert P.fingerprint("a", "x") != P.fingerprint("b", "x")


def test_collect_candidates_dedups_scores_once_and_marks_invalid_without_the_verifier():
    calls = []

    def scorer(task, code):
        calls.append(code)
        return ("pass", []) if "good" in code else ("fail", ["wrong_final_state"])

    attempts = [
        {"content": "def f():\n    good = 1\n", "accepted": True, "tests": {"passed": 1, "failed": 0}},
        {"content": "def f():\n    good = 1\n\n", "accepted": True, "tests": {"passed": 1, "failed": 0}},  # same answer
        {"content": "def f():\n    bad = 1\n", "accepted": True, "tests": {"passed": 0, "failed": 1}},
        {"content": "def f(:\n", "accepted": False, "refusal": "not valid Python (line 1)"},
    ]
    out = {c.code: c for c in P.collect_candidates(TASK, attempts, scorer)}
    assert len(out) == 3 and len(calls) == 2  # the refused text never reached the scorer
    good = out["def f():\n    good = 1\n"]
    assert (good.verdict, good.seen, good.visible_ok) == ("pass", 2, True)
    assert out["def f():\n    bad = 1\n"].visible_ok is False
    bad_text = out["def f(:\n"]
    assert (bad_text.verdict, bad_text.classes) == ("fail", [P.INVALID])


# ---------- pairs ----------

def test_pairs_need_a_pass_and_a_fail_on_the_same_task():
    cs = [cand("a", "pass"), cand("b", "fail"), cand("c", "fail", task="implement-get_order")]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    assert [p["task_id"] for p in pairs] == ["implement-clear_cart"]  # get_order has no pass
    assert pairs[0]["chosen"][0]["content"].startswith("```python") and pairs[0]["prompt"][1]["content"] == "Implement clear_cart"


def test_unknown_is_never_used_on_either_side():
    cs = [cand("a", "pass"), cand("u", "unknown"), cand("b", "fail")]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    used = {p["chosen_fp"] for p in pairs} | {p["rejected_fp"] for p in pairs}
    assert P.fingerprint(TASK["id"], "u") not in used and len(pairs) == 1


def test_hard_negatives_come_first_then_real_failures_then_invalid_python():
    cs = [cand("p", "pass"), cand("syntax", "fail", classes=[P.INVALID]), cand("wrong", "fail", classes=["missing_write"]),
          cand("looks_ok", "fail", classes=["undeclared_write"], visible=True)]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    assert [p["rejected_fp"] for p in pairs] == [P.fingerprint(TASK["id"], c) for c in ("looks_ok", "wrong", "syntax")]
    assert [p["hard_negative"] for p in pairs] == [True, False, False]


def test_per_task_cap_and_invalid_share():
    cs = [cand("p", "pass")] + [cand(f"w{i}", "fail", classes=["missing_write"]) for i in range(30)] \
        + [cand(f"s{i}", "fail", classes=[P.INVALID]) for i in range(30)]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages, per_task_cap=20, max_invalid_share=0.25)
    assert len(pairs) == 20
    assert sum(P.INVALID in p["rejected_classes"] for p in pairs) == 0  # 20 real failures fill the cap first
    few = P.build_pairs([cand("p", "pass")] + [cand(f"s{i}", "fail", classes=[P.INVALID]) for i in range(30)],
                        TASKS, messages_for=messages, per_task_cap=20, max_invalid_share=0.25)
    assert len(few) == 5  # ceil(20 * 0.25): invalid text alone is not allowed to fill a task


def test_chosen_answers_rotate_through_the_passes():
    cs = [cand("p1", "pass"), cand("p2", "pass")] + [cand(f"w{i}", "fail") for i in range(4)]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    assert {p["chosen_fp"] for p in pairs} == {P.fingerprint(TASK["id"], "p1"), P.fingerprint(TASK["id"], "p2")}


def test_pairs_are_deterministic():
    cs = [cand("p", "pass")] + [cand(f"w{i}", "fail") for i in range(6)]
    a = P.build_pairs(cs, TASKS, messages_for=messages)
    b = P.build_pairs(list(reversed(cs)), TASKS, messages_for=messages)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_held_out_task_can_never_reach_the_pairs():
    with pytest.raises(ValueError, match="held-out"):
        P.build_pairs([cand("p", "pass")], TASKS, messages_for=messages, heldout={TASK["id"]})
    with pytest.raises(AssertionError):
        P.assert_train_only([{"task_id": TASK["id"]}], {TASK["id"]})
    P.assert_train_only([{"task_id": "x"}], {TASK["id"]})


# ---------- split, coverage, go/no-go ----------

def test_split_holds_back_a_share_per_task_and_never_loses_a_pair():
    cs = [cand("p", "pass")] + [cand(f"w{i}", "fail") for i in range(20)]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    train, held = P.split_pairs(pairs, 0.1)
    assert len(held) == 2 and len(train) + len(held) == 20
    assert not {p["rejected_fp"] for p in train} & {p["rejected_fp"] for p in held}
    small_train, small_held = P.split_pairs(pairs[:4], 0.1)
    assert len(small_train) == 4 and small_held == []  # too few to hold any back


def test_coverage_and_the_go_no_go_check():
    cs = [cand("p", "pass"), cand("w", "fail", classes=["missing_write"]), cand("u", "unknown"),
          cand("x", "fail", task="implement-get_order")]
    pairs = P.build_pairs(cs, TASKS, messages_for=messages)
    report = P.coverage(cs, pairs, TASKS)
    assert report["tasks"] == 2 and report["tasks_with_a_pass"] == 1 and report["tasks_with_pairs"] == 1
    assert report["per_task"]["implement-get_order"]["pass"] == 0 and report["pairs"] == 1
    ok, reasons = P.go_no_go(report)
    assert not ok and len(reasons) == 2
    assert P.go_no_go({"tasks_with_pairs": 5, "pairs": 90})[0]


# ---------- the prompt the model sees ----------

def test_inference_messages_are_the_system_message_agenttune_builds_plus_the_first_turn():
    from harel_lexsi.adapters.builder_prompt import TOOLS_FALLBACK, system_prompt
    from harel_lexsi.adapters.train_rollout import load_tasks, training_prompt

    task = next(t for t in load_tasks() if t["id"] == "implement-clear_cart")
    system, user = P.inference_messages(task)
    assert system["role"] == "system" and user == {"role": "user", "content": training_prompt(task)}
    assert system["content"].startswith(system_prompt() + "\n\nYou can call these tools:\n[")
    for name in ("read_file", "write_file", "run_visible_tests", "finish"):
        assert f'"name": "{name}"' in system["content"]
    assert system["content"].endswith(TOOLS_FALLBACK.format(tools="X").split("X", 1)[1])  # the template's braces are escaped
