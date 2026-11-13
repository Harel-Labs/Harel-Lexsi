"""V5: give every violation of a FAIL one failure class and the step that caused it."""
from __future__ import annotations

from .types import FailureClass, Finding, Settlement, Verdict, Violation


def classify_violation(v: Violation) -> FailureClass:
    if v.kind == "load_error":
        return FailureClass.DOES_NOT_LOAD
    if v.kind == "closure_breach":
        return FailureClass.UNDECLARED_WRITE
    if v.kind == "effect_absent":
        return FailureClass.MISSING_EVENT if v.effect_op == "emit" else FailureClass.MISSING_WRITE
    if v.kind == "atomic_partial":
        return FailureClass.BROKEN_ATOMICITY
    if v.kind == "count_out_of_range":
        return FailureClass.WRONG_CARDINALITY
    return FailureClass.WRONG_FINAL_STATE  # value_mismatch, error_mismatch


def classify(s: Settlement) -> list[Finding]:
    if s.verdict is not Verdict.FAIL:
        return []
    return [Finding(classify_violation(v), v.step, v.resource, v.detail) for v in s.violations]
