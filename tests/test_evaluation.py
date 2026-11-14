"""T8 evaluation arithmetic: per-task rates, set means, the paired comparison and the gate's inputs."""
import pytest

from harel_lexsi.adapters import evaluation as E
from harel_lexsi.factory.deploy_gate import decide, load_thresholds


def row(model, task, verdict, *, vp=1, vf=0, writes=2, refused=0):
    return {"model": model, "task_id": task, "verdict": verdict, "visible_passed": vp, "visible_failed": vf,
            "writes": writes, "refused": refused}


def episodes(model, task, passes, total, **kw):
    return [row(model, task, "pass" if i < passes else "fail", **kw) for i in range(total)]


def test_a_task_scores_the_share_of_its_samples_the_verifier_passes():
    rows = episodes("base", "a", 2, 5) + [row("base", "a", "unknown")] * 0
    r = E.task_rates(rows)["base"]["a"]
    assert (r["n"], r["verified"], r["rate"]) == (5, 2, 0.4)


def test_unknown_counts_as_not_passing():
    r = E.task_rates([row("m", "a", "unknown"), row("m", "a", "pass")])["m"]["a"]
    assert r["rate"] == 0.5


def test_visible_ok_needs_tests_that_ran_and_all_passed():
    assert E.visible_ok(row("m", "a", "fail", vp=3, vf=0))
    assert not E.visible_ok(row("m", "a", "fail", vp=0, vf=0))
    assert not E.visible_ok(row("m", "a", "pass", vp=2, vf=1))


def test_a_set_is_the_mean_over_tasks_not_over_episodes():
    rows = episodes("m", "a", 1, 1) + episodes("m", "b", 0, 9)  # a: 100% on 1 sample; b: 0% on 9
    rates = E.task_rates(rows)["m"]
    assert E.set_rate(rates, ["a", "b"]) == 0.5  # each task counts once
    assert E.set_rate(rates, ["zzz"]) is None


def test_compare_pairs_only_the_tasks_both_models_ran():
    rows = (episodes("base", "a", 1, 5) + episodes("trained", "a", 3, 5) + episodes("base", "b", 0, 5) +
            episodes("trained", "b", 0, 5) + episodes("trained", "only_new", 5, 5))
    c = E.compare(rows, heldout_ids=["a", "b", "only_new"], mutation_ids=["a"], n_resamples=200)
    assert c["heldout"]["n"] == 2 and c["mutation"]["n"] == 1
    assert c["heldout"]["base"] == pytest.approx(0.1) and c["heldout"]["new"] == pytest.approx(0.3)
    assert c["heldout"]["delta"] == pytest.approx(0.2)
    assert c["mutation"]["p_value"] is None  # one task cannot be resampled


def test_gate_inputs_feed_the_deployment_gate():
    rows = []
    for t in "abcdefgh":  # 8 held-out tasks: base 20% -> trained 60%
        rows += episodes("base", t, 1, 5) + episodes("trained", t, 3, 5)
    for t in ("p1", "p2", "p3", "p4", "p5", "p6"):  # mutation set, n = 6 (the gate's minimum), unchanged
        rows += episodes("base", t, 2, 5) + episodes("trained", t, 2, 5)
    c = E.compare(rows, heldout_ids=list("abcdefgh"), mutation_ids=["p1", "p2", "p3", "p4", "p5", "p6"], n_resamples=200)
    heldout, mutation = E.gate_inputs(c)
    th, sha = load_thresholds()
    assert decide(heldout, mutation, th, sha).deploy
    rows += episodes("base", "p7", 5, 5) + episodes("trained", "p7", 0, 5)  # one bug-fix task got much worse
    worse = E.compare(rows, heldout_ids=list("abcdefgh"), mutation_ids=["p1", "p2", "p3", "p4", "p5", "p6", "p7"], n_resamples=200)
    d = decide(*E.gate_inputs(worse), th, sha)
    assert not d.deploy and any("regressed" in r for r in d.reasons)


def test_no_paired_tasks_is_no_evidence_so_the_gate_keeps_the_old_model():
    c = E.compare(episodes("base", "a", 1, 5), heldout_ids=["a"], mutation_ids=["a"], n_resamples=100)
    heldout, mutation = E.gate_inputs(c)
    assert heldout is None and mutation is None
    th, sha = load_thresholds()
    assert not decide(heldout, mutation, th, sha).deploy


def test_the_report_names_n_beside_every_number():
    rows = episodes("base", "a", 1, 5) + episodes("trained", "a", 2, 5) + episodes("base", "b", 0, 5) + episodes("trained", "b", 1, 5)
    c = E.compare(rows, heldout_ids=["a", "b"], mutation_ids=["a", "b"], n_resamples=200)
    text = E.render_markdown(c, "# DeploymentGate: KEEP THE OLD MODEL\n")
    assert "| held-out, verified success | 2 |" in text and "KEEP THE OLD MODEL" in text
