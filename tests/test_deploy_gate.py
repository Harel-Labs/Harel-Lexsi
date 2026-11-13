import json

import pytest

from harel_lexsi.factory.deploy_gate import (
    Thresholds,
    decide,
    load_thresholds,
    write_decision,
)

T = Thresholds(min_heldout_gain=0.10, min_heldout_n=6, max_mutation_regression=0.0,
               min_mutation_n=6, require_significance=False)
H = {"n": 8, "base": 0.25, "new": 0.50, "significant": True}
M = {"n": 17, "base": 0.80, "new": 0.80}


def test_deploys_when_heldout_rises_and_mutation_set_holds():
    d = decide(H, M, T)
    assert d.deploy and "did not regress" in d.reasons[-1]


@pytest.mark.parametrize("new", [0.25, 0.30, 0.10])
def test_keeps_old_model_when_heldout_gain_is_too_small_or_negative(new):
    assert not decide({**H, "new": new}, M, T).deploy


def test_keeps_old_model_on_any_mutation_regression():
    d = decide(H, {**M, "new": 0.75}, T)
    assert not d.deploy and any("regressed" in r for r in d.reasons)


def test_missing_evidence_is_never_a_deploy():
    assert not decide(None, M, T).deploy
    assert not decide(H, None, T).deploy


def test_small_samples_do_not_count():
    assert not decide({**H, "n": 3}, M, T).deploy
    assert not decide(H, {**M, "n": 3}, T).deploy


def test_significance_can_be_required():
    strict = Thresholds(0.10, 6, 0.0, 6, True)
    assert not decide({**H, "significant": False}, M, strict).deploy
    assert not decide({**H, "significant": None}, M, strict).deploy
    assert decide(H, M, strict).deploy


def test_committed_thresholds_load_and_decision_records_their_hash(tmp_path):
    t, sha = load_thresholds()
    assert t.min_heldout_gain > 0 and len(sha) == 64
    d = decide(H, M, t, sha)
    write_decision(tmp_path, d)
    saved = json.loads((tmp_path / "gate_decision.json").read_text())
    assert saved["decision"] == "deploy" and saved["thresholds_sha256"] == sha
    assert "n=8" in (tmp_path / "gate_decision.md").read_text()
