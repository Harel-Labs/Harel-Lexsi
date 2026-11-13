import json

from harel_lexsi.adapters.baseline import (
    EpisodeResult,
    malformed_calls,
    overall,
    score_episode,
    summarise,
    write_outputs,
)
from harel_lexsi.verifier.types import Settlement, Verdict, Violation

PASS = Settlement("c", Verdict.PASS, "ok")
FAIL = Settlement("c", Verdict.FAIL, "r", (Violation("closure_breach", 2, resource="inventory"),))
UNK = Settlement("c", Verdict.UNKNOWN, "no evidence")
SPLIT = {"heldout": ["implement-b"]}
PATH = "app/handlers/cart.py"


def task(tid, kind="implement", handler="add_to_cart"):
    return {"id": tid, "handler": handler, "kind": kind, "target": PATH}


def traj(*calls):
    return {"steps": [{"action": {"name": n, "arguments": a}} for n, a in calls]}


def seen(box):
    def scorer(files, handler):
        box.append((dict(files), handler))
        return [PASS]
    return scorer


def test_overall_unknown_is_never_a_pass():
    assert overall([PASS, PASS]) is Verdict.PASS
    assert overall([PASS, UNK]) is Verdict.UNKNOWN
    assert overall([PASS, FAIL, UNK]) is Verdict.FAIL
    assert overall([]) is Verdict.UNKNOWN


def test_patch_task_with_no_writes_is_scored_on_its_start_file():
    box = []
    r = score_episode(task("patch-a", "patch"), traj(("finish", {})), scorer=seen(box), split=SPLIT,
                      start_text="BUGGY")
    assert box == [({PATH: "BUGGY"}, "add_to_cart")]
    assert r.wrote_files is False


def test_agent_write_overrides_the_start_file():
    box = []
    score_episode(task("patch-a", "patch"), traj(("write_file", {"path": PATH, "content": "FIXED = 1\n"})),
                  scorer=seen(box), split=SPLIT, start_text="BUGGY = 1\n")
    text = box[0][0][PATH]
    assert "FIXED = 1" in text  # the agent's write wins; the start file is only used when it wrote nothing


def test_error_or_missing_trajectory_is_unknown_and_not_scored():
    box = []
    r = score_episode(task("implement-a"), None, scorer=seen(box), split=SPLIT, error="timeout")
    assert r.verdict == "unknown" and r.error == "timeout" and box == []


def test_failure_classes_and_split_are_recorded():
    r = score_episode(task("implement-b"), traj(("write_file", {"path": PATH, "content": "x"})),
                      scorer=lambda f, h: [FAIL], split=SPLIT, visible={"passed": 22, "failed": 0})
    assert r.verdict == "fail" and r.classes == ["undeclared_write"] and r.split == "heldout"
    assert r.visible_ok  # visible tests pass while the verifier fails: that is the gap


def test_malformed_calls_counts_unknown_tools_and_bad_arguments():
    t = traj(("read_file", {"path": "x"}), ("fly", {}), ("write_file", "not-a-dict"))
    assert malformed_calls(t) == 2


def test_summary_shows_the_gap_and_buckets():
    rs = [
        EpisodeResult("a", "h", "implement", "train", "fail", ["missing_write"], 22, 0, 4, True),
        EpisodeResult("b", "h", "implement", "train", "pass", [], 22, 0, 3, True),
        EpisodeResult("c", "h", "patch", "heldout", "unknown", [], 0, 0, 0, False, 1, "timeout"),
    ]
    s = summarise(rs)
    assert s["all"]["n"] == 3 and s["all"]["verified_success"] == 1
    assert s["train"]["visible_rate"] == 1.0 and s["train"]["verified_rate"] == 0.5
    assert s["train"]["gap"] == 0.5
    assert s["heldout"]["unknown"] == 1 and s["heldout"]["errors"] == 1
    assert s["all"]["failure_classes"] == {"missing_write": 1}


def test_outputs_are_written(tmp_path):
    rs = [EpisodeResult("a", "h", "implement", "train", "pass", [], 22, 0, 3, True)]
    write_outputs(tmp_path / "run", {"model": "m", "revision": "r"}, rs)
    d = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert d["meta"]["model"] == "m" and d["summary"]["all"]["verified_rate"] == 1.0
    assert "| a | train | pass" in (tmp_path / "run" / "summary.md").read_text()
    assert (tmp_path / "run" / "results.jsonl").read_text().count("\n") == 1


class FakeSandbox:
    def __init__(self):
        self.starts = []

    def reset(self, start=None, handler=None):
        self.starts.append(start)

    def run_visible_tests(self):
        return {"passed": 22, "failed": 0}


def test_run_baseline_keeps_going_after_a_crash_and_records_everything(tmp_path):
    from harel_lexsi.adapters.baseline import run_baseline

    tasks = [{**task("implement-a"), "prompt": "pa"}, {**task("patch-a", "patch"), "prompt": "pb"},
             {**task("implement-b"), "prompt": "pc"}]

    def rollout(prompt):
        if prompt == "pb":
            raise RuntimeError("model fell over")
        return traj(("write_file", {"path": PATH, "content": prompt}))

    sb = FakeSandbox()
    rs = run_baseline(tasks, split=SPLIT, sandbox=sb, rollout=rollout,
                      start_for=lambda p: (PATH, "BUGGY") if p == "pb" else None,
                      scorer=lambda f, h: [PASS], out=tmp_path, meta={"model": "m"})
    assert [r.verdict for r in rs] == ["pass", "unknown", "pass"]
    assert rs[1].error.startswith("RuntimeError")
    assert sb.starts == [None, (PATH, "BUGGY"), None]  # each episode reset first, patch gets its start
    lines = (tmp_path / "trajectories.jsonl").read_text().splitlines()
    assert [json.loads(line)["task_id"] for line in lines] == ["implement-a", "implement-b"]
    assert json.loads((tmp_path / "summary.json").read_text())["summary"]["all"]["n"] == 3


def test_malformed_calls_reads_multi_call_steps():
    traj = {"steps": [{"action": {"tool_calls": [
        {"name": "read_file", "arguments": {"path": "x"}}, {"name": "bogus", "arguments": {}}]}}]}
    assert malformed_calls(traj) == 1


def test_malformed_calls_openai_style_string_arguments():
    ok = {"type": "function", "function": {"name": "read_file", "arguments": '{"path": "x"}'}}
    bad = {"type": "function", "function": {"name": "read_file", "arguments": "{oops"}}
    assert malformed_calls({"steps": [{"action": {"tool_calls": [ok, bad]}}]}) == 1
