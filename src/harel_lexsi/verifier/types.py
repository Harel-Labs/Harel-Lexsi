"""Shared result types for the verifier. Model-free."""
from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # missing evidence; never counts as PASS


@dataclass(frozen=True)
class VerdictResult:
    contract_id: str
    verdict: Verdict
    reason: str


class FailureClass(str, Enum):
    UNDECLARED_WRITE = "undeclared_write"
    MISSING_WRITE = "missing_write"
    MISSING_EVENT = "missing_event"
    BROKEN_ATOMICITY = "broken_atomicity"
    WRONG_CARDINALITY = "wrong_cardinality"
    WRONG_FINAL_STATE = "wrong_final_state"


@dataclass(frozen=True)
class Violation:
    """What settlement found wrong, before classification. `detail` is internal: it may quote
    contract text and must never be shown to the builder agent."""
    kind: str            # closure_breach | effect_absent | atomic_partial | count_out_of_range
                         # | value_mismatch | error_mismatch
    step: int
    effect_id: str | None = None
    effect_op: str | None = None
    resource: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class Finding:
    failure_class: FailureClass
    step: int
    resource: str | None = None
    detail: str = ""     # internal only


@dataclass(frozen=True)
class Settlement:
    contract_id: str
    verdict: Verdict
    reason: str
    violations: tuple[Violation, ...] = ()

    def as_result(self) -> VerdictResult:
        return VerdictResult(self.contract_id, self.verdict, self.reason)
