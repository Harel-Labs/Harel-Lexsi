"""A1 judge: strict parsing, UNKNOWN instead of 0.0, a fixed prompt without verifier material.

No network: the judge's `_call` is replaced.
"""
import json
from pathlib import Path

import pytest

from harel_lexsi.adapters import judge as J
from harel_lexsi.adapters.judge import (
    HostedJudge,
    JudgeResult,
    parse_strict,
    score_run,
    trajectory_from_record,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------- the prompt is fixed and builder-visible only ----------

def test_rubric_is_the_saved_template_with_the_shared_rules():
    text = J.rubric()
    assert J.PROMPT_FILE.read_text().split("{rules}")[0] in text
    assert "All handlers share these rules" in text
    assert "{rules}" not in text and "{{" not in text


def test_rubric_hash_is_stable_and_changes_with_the_text():
    assert J.rubric_sha256() == J.rubric_sha256(J.rubric())
    assert J.rubric_sha256("a") != J.rubric_sha256("b")


def test_rubric_never_contains_holdout_or_verifier_material():
    spec = (ROOT / "spec" / "spec.md").read_text()
    text = J.rubric()
    assert "## Holdout" not in text and "contracts:" not in text
    for line in spec[spec.index("## Holdout"):].splitlines():
        if line.strip().startswith("- { id:"):
            assert line.strip() not in text
    for word in ("verifier", "settle", "reference handler", "holdout"):
        assert word not in text.lower()


# ---------- strict parsing: anything else is UNKNOWN ----------

@pytest.mark.parametrize("reply, score", [
    ('{"score": 0.8, "explanation": "ok"}', 0.8),
    ('```json\n{"score": 1, "explanation": "all good"}\n```', 1.0),
    ('{"score": 0, "explanation": "broken"}', 0.0),
])
def test_valid_replies_score(reply, score):
    r = parse_strict(reply)
    assert r.score == score and r.error == ""


@pytest.mark.parametrize("reply", [
    "I would give this a 1.", "score: 0.9", "", '{"explanation": "no score"}',
    '{"score": 7, "explanation": "wrong scale"}', '{"score": -0.2}', '{"score": "high"}',
    '{"score": true}', '{"score": NaN}', "[1, 0]",
])
def test_anything_else_is_unknown_not_a_zero(reply):
    r = parse_strict(reply)
    assert r.score is None and r.approved is None and r.error


def test_approval_threshold_is_fixed():
    assert JudgeResult(0.5).approved is True and JudgeResult(0.49).approved is False
    assert J.APPROVE_AT == 0.5


# ---------- a failed call is UNKNOWN ----------

def make_judge(replies):
    j = HostedJudge(api_key="k", system_prompt="rubric", retries=1, backoff=0)
    it = iter(replies)

    def call(messages):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    j._call = call
    return j


def test_failed_call_is_unknown_never_zero():
    r = make_judge([RuntimeError("503")]).judge("task", "traj")
    assert r.score is None and "judge call failed" in r.error and r.approved is None


def test_reward_fn_gives_a_trainer_a_number_for_unknown():
    j = make_judge([RuntimeError("503"), '{"score": 0.7, "explanation": "x"}'])
    fn = j.reward_fn()
    assert fn(type("T", (), {"task": "t"})()) == 0.0 and fn(type("T", (), {"task": "t"})()) == 0.7


def test_judge_sees_the_task_and_the_trajectory_steps():
    seen = []
    j = HostedJudge(api_key="k", system_prompt="rubric")
    j._call = lambda messages: seen.append(messages) or '{"score": 1, "explanation": "x"}'
    rec = {"task": "Implement f", "steps": [{"step_number": 0, "state": "s", "observation": "obs-1",
                                              "action": {"tool_calls": [{"name": "write_file", "arguments": {}}]}}]}
    j.judge(rec["task"], trajectory_from_record(rec))
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert system == "rubric" and "Implement f" in user and "write_file" in user and "obs-1" in user


# ---------- scoring a run ----------

def test_score_run_saves_scores_beside_the_verifier_verdicts(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    results = [{"task_id": "a", "split": "train", "verdict": "fail", "visible_passed": 1, "visible_failed": 0},
               {"task_id": "b", "split": "heldout", "verdict": "pass", "visible_passed": 1, "visible_failed": 0},
               {"task_id": "c", "split": "train", "verdict": "fail", "visible_passed": 0, "visible_failed": 2}]
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    (run / "trajectories.jsonl").write_text("".join(
        json.dumps({"task_id": r["task_id"], "task": f"task {r['task_id']}", "steps": []}) + "\n" for r in results))
    j = make_judge(['{"score": 0.9, "explanation": "looks right"}', '{"score": 1, "explanation": "right"}',
                    RuntimeError("down")])
    rows = score_run(run, j)
    assert [r["judge_approved"] for r in rows] == [True, True, None]
    s = json.loads((run / "judge_summary.json").read_text())
    assert s["summary"]["all"] == {
        "n": 3, "judge_unknown": 1, "judge_approved": 2, "approved_and_verified": 1,
        "approved_but_failed_verifier": 1, "rejected_but_verified": 0, "verifier_pass": 1, "verifier_fail": 2,
        "visible_ok": 2}
    assert s["summary"]["by_judge_score"] == {
        "UNKNOWN": {"n": 1, "verifier_pass": 0, "verifier_fail": 1, "verifier_unknown": 0},
        "1.00": {"n": 1, "verifier_pass": 1, "verifier_fail": 0, "verifier_unknown": 0},
        "0.90": {"n": 1, "verifier_pass": 0, "verifier_fail": 1, "verifier_unknown": 0}}
    assert s["meta"]["rubric_sha256"] == J.rubric_sha256("rubric") and s["meta"]["approve_at"] == 0.5
    assert (run / "judge_scores.jsonl").is_file() and "two numbers" in (run / "judge_summary.md").read_text().lower()
