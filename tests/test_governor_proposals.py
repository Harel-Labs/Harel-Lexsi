"""G2 (part 2): propose() makes one candidate version and one frozen proposal, and promotes nothing."""
import json
import os
import stat

import pytest

from harel_lexsi.factory import governor as G
from harel_lexsi.factory.proposals import ProposalCorrupt, ProposalStore, UnknownProposal
from harel_lexsi.factory.versions import NoCurrentVersion, VersionStore

SUMMARY = {"train": {"n": 16, "failure_classes": {"broken_atomicity": 5, "missing_event": 3}},
           "heldout": {"n": 8, "failure_classes": {"missing_write": 8}}}


@pytest.fixture
def stores(tmp_path):
    versions = VersionStore(tmp_path / "versions")
    base = versions.create("You write shop handlers.", "- use transactions")
    versions.promote(base.id)
    return versions, ProposalStore(tmp_path / "proposals"), base


# ---------- one round ----------

def test_propose_makes_a_candidate_version_and_a_frozen_proposal(stores):
    versions, proposals, base = stores
    p = G.propose(versions, proposals, SUMMARY)
    assert (p.id, p.base_version, p.candidate_version) == ("p0001", base.id, "v0002")
    cand = versions.get(p.candidate_version)
    assert cand.parent == base.id
    assert cand.prompt == base.prompt                      # only memory changed
    assert cand.memory == base.memory + "\n- " + G.LESSONS["broken_atomicity"]
    assert "broken_atomicity" in cand.note and "5 of 16" in cand.note
    assert p.trigger == {"failure_class": "broken_atomicity", "episodes": 5, "of": 16, "split": "train"}
    assert p.change == {"field": "memory", "added_text": G.LESSONS["broken_atomicity"]}


def test_the_candidate_is_not_promoted(stores):
    versions, proposals, base = stores
    G.propose(versions, proposals, SUMMARY)
    assert versions.current() == base                      # only the A/B result may change this


def test_expected_effect_is_recorded_with_the_proposal(stores):
    versions, proposals, _ = stores
    p = G.propose(versions, proposals, SUMMARY)
    assert proposals.get(p.id).expected_effect == {
        "judged_on": "heldout",
        "expectations": [
            {"metric": "episodes_with_failure_class", "failure_class": "broken_atomicity", "direction": "decrease"},
            {"metric": "verified_success", "direction": "not_decrease"},
        ],
    }


def test_the_heldout_split_never_decides_the_trigger(stores):
    versions, proposals, _ = stores
    # held-out is dominated by missing_write (8), but the trigger comes from the training block
    assert G.propose(versions, proposals, SUMMARY).trigger["failure_class"] == "broken_atomicity"


def test_nothing_repeats_means_no_proposal_and_nothing_is_written(stores):
    versions, proposals, _ = stores
    quiet = {"train": {"n": 16, "failure_classes": {"missing_event": 2}}}
    assert G.propose(versions, proposals, quiet) is None
    assert len(versions.list()) == 1 and proposals.list() == []


def test_without_a_current_version_there_is_nothing_to_change(tmp_path):
    with pytest.raises(NoCurrentVersion):
        G.propose(VersionStore(tmp_path / "v"), ProposalStore(tmp_path / "p"), SUMMARY)


# ---------- one honest round, not a search ----------

def test_asking_again_returns_the_same_proposal_and_creates_nothing(stores):
    versions, proposals, _ = stores
    first = G.propose(versions, proposals, SUMMARY)
    again = G.propose(versions, proposals, SUMMARY)
    assert again == first
    assert len(versions.list()) == 2 and len(proposals.list()) == 1


def test_a_new_base_version_allows_a_new_round(stores):
    versions, proposals, _ = stores
    first = G.propose(versions, proposals, SUMMARY)
    versions.promote(first.candidate_version)               # the A/B (G3) decided to keep it
    second = G.propose(versions, proposals, SUMMARY)
    assert second.id == "p0002" and second.base_version == first.candidate_version
    assert versions.get(second.candidate_version).memory.count("- " + G.LESSONS["broken_atomicity"]) == 2


def test_a_different_failure_class_is_a_different_round(stores):
    versions, proposals, _ = stores
    G.propose(versions, proposals, SUMMARY)
    other = {"train": {"n": 16, "failure_classes": {"missing_write": 4}}}
    assert G.propose(versions, proposals, other).trigger["failure_class"] == "missing_write"


# ---------- apply_change ----------

def test_apply_change_appends_to_an_empty_memory_without_a_blank_line(tmp_path):
    base = VersionStore(tmp_path).create("prompt", "")
    assert G.apply_change(base, G.Change("memory", "Check your work.")) == ("prompt", "- Check your work.")


def test_apply_change_can_extend_the_prompt_and_leaves_memory_alone(tmp_path):
    base = VersionStore(tmp_path).create("prompt", "mem")
    assert G.apply_change(base, G.Change("prompt", "Be careful.")) == ("prompt\nBe careful.", "mem")


def test_apply_change_runs_the_guardrails(tmp_path):
    base = VersionStore(tmp_path).create("prompt", "mem")
    with pytest.raises(G.GuardrailError):
        G.apply_change(base, G.Change("verifier", "x"))
    with pytest.raises(G.GuardrailError):
        G.apply_change(base, G.Change("memory", "Read the holdout."))


# ---------- the proposal store ----------

def test_proposals_are_sequential_read_only_and_never_reused(stores):
    _, proposals, _ = stores
    a = proposals.create(base_version="v0001", candidate_version="v0002", trigger={"failure_class": "x"},
                         change={}, expected_effect={})
    assert a.id == "p0001"
    assert not os.stat(proposals.root / "p0001.json").st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    (proposals.root / "p0002.json").write_text("something already here")
    b = proposals.create(base_version="v0001", candidate_version="v0003", trigger={"failure_class": "y"},
                         change={}, expected_effect={})
    assert b.id == "p0003" and (proposals.root / "p0002.json").read_text() == "something already here"


def test_an_edit_behind_our_back_is_detected(stores):
    versions, proposals, _ = stores
    p = G.propose(versions, proposals, SUMMARY)
    path = proposals.root / f"{p.id}.json"
    os.chmod(path, 0o644)
    raw = json.loads(path.read_text())
    raw["expected_effect"]["expectations"][0]["direction"] = "increase"   # rewriting the prediction afterwards
    path.write_text(json.dumps(raw))
    with pytest.raises(ProposalCorrupt):
        proposals.get(p.id)
    with pytest.raises(ProposalCorrupt):
        proposals.list()


@pytest.mark.parametrize("bad", ["p9999", "../p0001", "p1", "", "p0001.json", "v0001"])
def test_unknown_and_malformed_ids_are_rejected(stores, bad):
    _, proposals, _ = stores
    with pytest.raises(UnknownProposal):
        proposals.get(bad)


def test_find_matches_base_version_and_class_only(stores):
    versions, proposals, base = stores
    p = G.propose(versions, proposals, SUMMARY)
    assert proposals.find(base.id, "broken_atomicity") == p
    assert proposals.find(base.id, "missing_write") is None
    assert proposals.find("v0099", "broken_atomicity") is None


def test_a_proposal_survives_a_new_store_object(stores):
    versions, proposals, _ = stores
    p = G.propose(versions, proposals, SUMMARY)
    assert ProposalStore(proposals.root).get(p.id) == p
