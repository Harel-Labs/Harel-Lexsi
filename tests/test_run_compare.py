import json

import pytest

pytest.importorskip("auditkit")
agentic = pytest.importorskip("agenttune.agentic")

from harel_lexsi.adapters.baseline import EpisodeResult, write_outputs
from harel_lexsi.adapters.run_compare import (
    compare_folders,
    compare_runs,
    load_run,
    tool_metrics,
)

PATH = "app/handlers/cart.py"


def step(i, name, args, obs="{'ok': 1}"):
    return agentic.Step(i, "s", {"name": name, "arguments": args}, obs)


def traj(*steps):
    return agentic.Trajectory(task="t", steps=list(steps))


def make_run(folder, rows):
    """rows: (task_id, split, verdict, Trajectory)."""
    folder.mkdir(parents=True)
    results = [EpisodeResult(t, "h", "implement", sp, v, [], 22, 0, len(tr.steps), True)
               for t, sp, v, tr in rows]
    write_outputs(folder, {"model": "m"}, results)
    (folder / "trajectories.jsonl").write_text(
        "".join(json.dumps({"task_id": t, **tr.to_dict()}) + "\n" for t, _, _, tr in rows))
    return folder


GOOD = traj(step(1, "read_file", {"path": PATH}), step(2, "write_file", {"path": PATH, "content": "x"}),
            step(3, "finish", {}))


def test_load_run_attaches_the_harel_verdict_to_each_episode(tmp_path):
    f = make_run(tmp_path / "base", [("a", "train", "pass", GOOD), ("b", "heldout", "fail", GOOD)])
    run = load_run(f)
    assert set(run.episodes) == {"a", "b"}
    assert run.episodes["b"].metadata["harel"] == {"task_id": "b", "verdict": "fail", "classes": [],
                                                  "split": "heldout"}
    assert run.meta["model"] == "m"


def test_write_file_reaches_the_episode_as_name_and_arguments(tmp_path):
    run = load_run(make_run(tmp_path / "r", [("a", "train", "pass", GOOD)]))
    calls = [e.payload for e in run.episodes["a"].tool_call_events()]
    assert [c.get("name") for c in calls] == ["read_file", "write_file", "finish"]
    assert calls[1]["arguments"]["path"] == PATH


def test_tool_metrics_counts_wrong_and_redundant_calls():
    t = traj(
        step(1, "read_file", {"path": PATH}),
        step(2, "read_file", {"path": PATH}),                      # redundant
        step(3, "fly", {}),                                       # wrong: unknown tool
        step(4, "write_file", {"path": "tools/db.py", "content": "x"}, "{'error': 'refused'}"),  # wrong
        step(5, "read_file", {"path": PATH}),                      # not redundant: a write came between
    ).to_dict()
    assert tool_metrics(t) == {"steps": 5, "wrong": 2, "redundant": 1}


def test_compare_uses_only_heldout_tasks_paired_by_id(tmp_path):
    ids = [f"h{i}" for i in range(8)]
    base = make_run(tmp_path / "base", [("train1", "train", "pass", GOOD)] +
                    [(i, "heldout", "fail", GOOD) for i in ids])
    new = make_run(tmp_path / "new", [("train1", "train", "fail", GOOD)] +
                   [(i, "heldout", "pass", GOOD) for i in ids])
    c = compare_runs(load_run(base, "base"), load_run(new, "retrained"))
    assert c["n"] == 8 and "train1" not in c["tasks"]       # held-out only
    assert c["stats"]["mean_baseline"] == 0.0 and c["stats"]["mean_candidate"] == 1.0
    assert c["stats"]["significant"] is True


def test_unknown_counts_as_not_verified(tmp_path):
    base = make_run(tmp_path / "b", [("h1", "heldout", "unknown", GOOD), ("h2", "heldout", "fail", GOOD)])
    new = make_run(tmp_path / "n", [("h1", "heldout", "unknown", GOOD), ("h2", "heldout", "pass", GOOD)])
    c = compare_runs(load_run(base, "b"), load_run(new, "n"))
    assert c["stats"]["mean_baseline"] == 0.0 and c["stats"]["mean_candidate"] == 0.5


def test_report_prints_sample_size_beside_every_number(tmp_path):
    ids = [f"h{i}" for i in range(4)]
    base = make_run(tmp_path / "b", [(i, "heldout", "fail", GOOD) for i in ids])
    new = make_run(tmp_path / "n", [(i, "heldout", "pass", GOOD) for i in ids])
    _, md = compare_folders(base, new)
    assert "n=4" in md and "| 4 |" in md


def test_too_few_paired_tasks_say_so(tmp_path):
    base = make_run(tmp_path / "b", [("h1", "heldout", "fail", GOOD)])
    new = make_run(tmp_path / "n", [("h1", "heldout", "pass", GOOD)])
    _, md = compare_folders(base, new)
    assert "Not enough paired tasks" in md and "n=1" in md
