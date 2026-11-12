"""T4: feed AgentTune's training buffer from Harel verdicts.

A training example exists only for a *failure plus a repair that verifies*: the failed attempt must
settle FAIL (UNKNOWN is not a failure we can teach from), and the repair must settle all-PASS. Anything
else is dropped, never buffered. The example is a preference pair (chosen = verified repair, rejected =
the failed attempt) so it feeds the DPO path directly; `root_cause` is the Harel failure class.

Two ways in, matching the ticket:
- `ClosedLoopHooks.offer(failed, repair)` calls the buffer directly.
- `audit_record(...)` writes the schema `FailureDetector.scan_audit_log` requires
  (trajectory_id, stage_name, state_snapshot), for runs that go through the audit log.

Nothing here shows contract text to the builder: messages carry V8 failure lines only.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from harel_lexsi.verifier.classify import classify
from harel_lexsi.verifier.failure_line import failure_lines
from harel_lexsi.verifier.types import Finding, Settlement, Verdict

from .settlement_reward import final_files

Scorer = Callable[[Mapping[str, str], str], Sequence[Settlement]]
Message = dict[str, str]


@dataclass(frozen=True)
class Attempt:
    trajectory_id: str
    handler: str
    prompt: list[Message]
    completion: list[Message]
    trajectory: Any  # AgentTune Trajectory or its dict form; `final_files` reads the write_file calls


class Outcome(str, Enum):
    BUFFERED = "buffered"
    DROPPED_NOT_A_FAILURE = "dropped_not_a_failure"  # failed attempt did not settle FAIL
    DROPPED_UNVERIFIED = "dropped_unverified"        # repair did not settle all-PASS


@dataclass(frozen=True)
class HookResult:
    outcome: Outcome
    findings: tuple[Finding, ...] = ()
    reason: str = ""


def _verdict(settlements: Sequence[Settlement]) -> Verdict:
    if any(s.verdict is Verdict.FAIL for s in settlements):
        return Verdict.FAIL
    if settlements and all(s.verdict is Verdict.PASS for s in settlements):
        return Verdict.PASS
    return Verdict.UNKNOWN  # empty or any UNKNOWN: never a pass


def findings_of(settlements: Sequence[Settlement]) -> list[Finding]:
    return [f for s in settlements for f in classify(s)]


def _first(findings: Sequence[Finding]) -> Finding | None:
    return min(findings, key=lambda f: f.step) if findings else None


def audit_record(attempt: Attempt, settlements: Sequence[Settlement],
                 visible_resources: Sequence[str] = ()) -> dict[str, Any] | None:
    """One audit-log line for a failed attempt, or None if it did not fail.

    `error_details` holds V8 failure lines only; `state_snapshot` holds ids and classes, no contract text.
    """
    findings = findings_of(settlements)
    first = _first(findings)
    if first is None:
        return None
    return {
        "trajectory_id": attempt.trajectory_id,
        "stage_name": f"step {first.step}",
        "stage_type": "tool_call",
        "status": "error",
        "error_details": "; ".join(failure_lines(findings, visible_resources)),
        "state_snapshot": {
            "handler": attempt.handler,
            "failure_class": first.failure_class.value,
            "step": first.step,
        },
    }


@dataclass
class ClosedLoopHooks:
    """Detect, classify, verify the repair, and fill `buffer` (an AgentTune `TrainingBuffer`)."""

    buffer: Any
    scorer: Scorer
    visible_resources: Sequence[str] = ()
    stats: dict[str, int] = field(default_factory=lambda: {o.value: 0 for o in Outcome})

    def settle(self, attempt: Attempt) -> list[Settlement]:
        return list(self.scorer(final_files(attempt.trajectory), attempt.handler))

    def offer(self, failed: Attempt, repair: Attempt,
              failed_settlements: Sequence[Settlement] | None = None) -> HookResult:
        before = list(failed_settlements) if failed_settlements is not None else self.settle(failed)
        if _verdict(before) is not Verdict.FAIL:
            return self._done(HookResult(Outcome.DROPPED_NOT_A_FAILURE,
                                         reason="failed attempt did not settle FAIL"))
        findings = findings_of(before)
        after = self.settle(repair)
        if _verdict(after) is not Verdict.PASS:
            return self._done(HookResult(Outcome.DROPPED_UNVERIFIED, tuple(findings),
                                         "repair did not verify"))
        from agenttune.decide.closed_loop.contracts import TrainingExample

        first = _first(findings)
        self.buffer.add(TrainingExample(
            trajectory_id=failed.trajectory_id,
            original_failure_type=first.failure_class.value,
            root_cause=first.failure_class.value,
            prompt=failed.prompt,
            chosen=repair.completion,
            rejected=failed.completion,
        ))
        return self._done(HookResult(Outcome.BUFFERED, tuple(findings)))

    def _done(self, r: HookResult) -> HookResult:
        self.stats[r.outcome.value] += 1
        return r
