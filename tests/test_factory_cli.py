"""G2 (part 3): the command line drives init -> propose -> show on a baseline summary.json."""
import json

import pytest

from harel_lexsi.factory import governor as G
from harel_lexsi.factory.__main__ import main
from harel_lexsi.factory.proposals import ProposalStore
from harel_lexsi.factory.versions import VersionStore

SUMMARY = {"train": {"n": 16, "failure_classes": {"missing_event": 4}},
           "heldout": {"n": 8, "failure_classes": {"missing_event": 8}}}


@pytest.fixture
def env(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("You write shop handlers.")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"meta": {"model": "x"}, "summary": SUMMARY}))  # the baseline's own file shape

    def run(*args):
        return main(["--versions", str(tmp_path / "v"), "--proposals", str(tmp_path / "p"), *map(str, args)])

    return tmp_path, prompt, summary, run


def test_the_whole_flow(env, capsys):
    tmp, prompt, summary, run = env
    assert run("init", "--prompt-file", prompt) == 0
    assert run("propose", "--summary", summary) == 0
    out = capsys.readouterr().out
    assert "p0001" in out and "missing_event" in out and "not promoted" in out
    versions, proposals = VersionStore(tmp / "v"), ProposalStore(tmp / "p")
    assert versions.current().id == "v0001" and len(versions.list()) == 2
    assert versions.get("v0002").memory == "- " + G.LESSONS["missing_event"]
    assert run("show", "p0001") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["trigger"]["failure_class"] == "missing_event" and shown["base_version"] == "v0001"
    assert proposals.get("p0001").expected_effect["judged_on"] == "heldout"


def test_asking_twice_makes_no_second_proposal(env):
    tmp, prompt, summary, run = env
    run("init", "--prompt-file", prompt)
    assert run("propose", "--summary", summary) == 0
    assert run("propose", "--summary", summary) == 0
    assert len(ProposalStore(tmp / "p").list()) == 1 and len(VersionStore(tmp / "v").list()) == 2


def test_a_plain_summary_without_the_wrapper_also_works(env):
    tmp, prompt, _, run = env
    plain = tmp / "plain.json"
    plain.write_text(json.dumps(SUMMARY))
    run("init", "--prompt-file", prompt)
    assert run("propose", "--summary", plain) == 0


def test_nothing_repeating_enough_exits_1_and_says_why(env, capsys):
    tmp, prompt, _, run = env
    quiet = tmp / "quiet.json"
    quiet.write_text(json.dumps({"summary": {"train": {"n": 16, "failure_classes": {"missing_event": 1}}}}))
    run("init", "--prompt-file", prompt)
    assert run("propose", "--summary", quiet) == 1
    assert "no proposal" in capsys.readouterr().out
    assert ProposalStore(tmp / "p").list() == []


def test_a_summary_with_no_training_episodes_explains_the_fix(env, capsys):
    tmp, prompt, _, run = env
    heldout_only = tmp / "ho.json"
    heldout_only.write_text(json.dumps({"summary": {"train": {"n": 0, "failure_classes": {}},
                                                    "heldout": {"n": 8, "failure_classes": {"missing_event": 8}}}}))
    run("init", "--prompt-file", prompt)
    assert run("propose", "--summary", heldout_only) == 1   # held-out failures never trigger anything
    assert "--split all" in capsys.readouterr().out


def test_could_not_run_cases_exit_2(env, capsys):
    tmp, prompt, summary, run = env
    assert run("propose", "--summary", summary) == 2                       # no current version yet
    assert "COULD NOT RUN" in capsys.readouterr().err
    run("init", "--prompt-file", prompt)
    assert run("propose", "--summary", tmp / "missing.json") == 2          # no such file
    bad = tmp / "bad.json"
    bad.write_text("{not json")
    assert run("propose", "--summary", bad) == 2
    assert run("show", "p0042") == 2                                       # unknown proposal
    assert run("init", "--prompt-file", prompt) == 2                       # versions are never overwritten
