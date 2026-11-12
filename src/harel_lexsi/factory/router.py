"""T7: L2 routes, never judges. Rule-based routing of a task's settlements.

  all PASS                         -> MERGE
  any FAIL, retries left           -> RETRY, telling L1 the failure line(s) (V8) and nothing else
  any FAIL, no retries left        -> REJECT
  UNKNOWN (and no FAIL), or nothing settled -> ESCALATE to a human; it never merges

No model is consulted anywhere on this path: the only input is the verifier's settlements. A FAIL
takes priority over an UNKNOWN because a FAIL is definite and gives L1 something to act on.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from harel_lexsi.verifier.classify import classify
from harel_lexsi.verifier.failure_line import failure_lines
from harel_lexsi.verifier.types import Settlement, Verdict

DEFAULT_MAX_RETRIES = 2


class Action(str, Enum):
    MERGE = "merge"
    RETRY = "retry"
    REJECT = "reject"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Decision:
    action: Action
    message: str = ""   # what L1 is told. Only set for RETRY; never contains contract text.
    reason: str = ""    # for logs: counts only


def route(
    settlements: Sequence[Settlement],
    attempt: int,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    visible_resources: Iterable[str] = (),
) -> Decision:
    """`attempt` is how many retries L1 has already used (0 on its first submission)."""
    if attempt < 0 or max_retries < 0:
        raise ValueError("attempt and max_retries must be >= 0")
    if not settlements:
        return Decision(Action.ESCALATE, reason="nothing was settled")

    failed = [s for s in settlements if s.verdict is Verdict.FAIL]
    unknown = [s for s in settlements if s.verdict is Verdict.UNKNOWN]

    if failed:
        if attempt >= max_retries:
            return Decision(Action.REJECT, reason=f"{len(failed)} failing after {attempt} retries")
        findings = [f for s in failed for f in classify(s)]
        message = "\n".join(failure_lines(findings, visible_resources))
        return Decision(Action.RETRY, message, f"{len(failed)} failing, retry {attempt + 1} of {max_retries}")
    if unknown:
        return Decision(Action.ESCALATE, reason=f"{len(unknown)} unknown; UNKNOWN never merges")
    return Decision(Action.MERGE, reason=f"all {len(settlements)} passed")
