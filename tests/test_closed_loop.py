import json

import pytest

from harel_lexsi.adapters.closed_loop import (
    Attempt,
    ClosedLoopHooks,
    Outcome,
    audit_record,
    findings_of,
)
from harel_lexsi.verifier.types import Settlement, Verdict, Violation

agenttune = pytest.importorskip("agenttune.decide.closed_loop.retraining_trigger")
TrainingBuffer = agenttune.TrainingBuffer

SECRET = "CONTRACT-SECRET-TEXT"
FAIL = Settlement("c1", Verdict.FAIL, "r", (Violation("closure_breach", 3, resource="inventory", detail=SECRET),))
PASS = Settlement("c1", Verdict.PASS, "ok")
UNKNOWN = Settlement("c1", Verdict.UNKNOWN, "no evidence")


def attempt(tid, content):
    traj = {"steps": [{"action": {"name": "write_file",
                                  "arguments": {"path": "app/handlers/cart.py", "content": content}}}]}
    return Attempt(tid, "add_to_cart", [{"role": "user", "content": "fix cart"}],
                   [{"role": "assistant", "content": content}], traj)


def hooks(by_content):
    return ClosedLoopHooks(TrainingBuffer(), lambda files, handler: by_content[files["app/handlers/cart.py"]])


def test_verified_repair_is_buffered_as_preference_pair():
    h = hooks({"bad": [FAIL], "good": [PASS]})
    r = h.offer(attempt("t1", "bad"), attempt("t2", "good"))
    assert r.outcome is Outcome.BUFFERED
    (ex,) = h.buffer.drain()
    assert ex.trajectory_id == "t1" and ex.root_cause == "undeclared_write"
    assert ex.chosen[0]["content"] == "good" and ex.rejected[0]["content"] == "bad"
    assert ex.has_preference_pair()


@pytest.mark.parametrize("repair", [[FAIL], [UNKNOWN], [PASS, UNKNOWN], []])
def test_unverified_repair_is_dropped(repair):
    h = hooks({"bad": [FAIL], "r": repair})
    r = h.offer(attempt("t1", "bad"), attempt("t2", "r"))
    assert r.outcome is Outcome.DROPPED_UNVERIFIED
    assert len(h.buffer) == 0


@pytest.mark.parametrize("failed", [[PASS], [UNKNOWN], []])
def test_non_failure_is_not_buffered(failed):
    h = hooks({"a": failed, "good": [PASS]})
    assert h.offer(attempt("t1", "a"), attempt("t2", "good")).outcome is Outcome.DROPPED_NOT_A_FAILURE
    assert len(h.buffer) == 0


def test_stats_count_every_outcome():
    h = hooks({"bad": [FAIL], "good": [PASS], "worse": [FAIL]})
    h.offer(attempt("t1", "bad"), attempt("t2", "good"))
    h.offer(attempt("t1", "bad"), attempt("t3", "worse"))
    assert h.stats == {"buffered": 1, "dropped_not_a_failure": 0, "dropped_unverified": 1}


def test_audit_record_has_scan_schema_and_no_contract_text(tmp_path):
    rec = audit_record(attempt("t1", "bad"), [FAIL], visible_resources=["inventory"])
    assert rec["trajectory_id"] == "t1" and rec["stage_name"] == "step 3"
    assert isinstance(rec["state_snapshot"], dict)
    assert SECRET not in json.dumps(rec)
    assert audit_record(attempt("t1", "ok"), [PASS]) is None
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps(rec) + "\n")
    from agenttune.decide.closed_loop.failure_detector import FailureDetector

    (f,) = list(FailureDetector().scan_audit_log(str(log)))
    assert f.trajectory_id == "t1" and f.failed_stage_name == "step 3" and f.failure_type == "tool_crash"


def test_findings_empty_for_pass():
    assert findings_of([PASS]) == []
