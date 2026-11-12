"""V8: the one line L1 is told for a FAIL. Class and step only, never contract content.

Lines are built from fixed templates. The only thing that is not fixed is a resource name, used for
an undeclared write, and only when that name is one L1 can already see (e.g. a table in schema.sql).
"""
from __future__ import annotations

from collections.abc import Iterable

from .types import FailureClass, Finding

_TEMPLATES = {
    FailureClass.MISSING_WRITE: "step {step} did not make a change this task requires",
    FailureClass.MISSING_EVENT: "step {step} did not publish an event this task requires",
    FailureClass.BROKEN_ATOMICITY: "step {step} left the work half done",
    FailureClass.WRONG_CARDINALITY: "step {step} changed the wrong number of records",
    FailureClass.WRONG_FINAL_STATE: "step {step} left the wrong final state",
}


def failure_line(finding: Finding, visible_resources: Iterable[str] = ()) -> str:
    if finding.failure_class is FailureClass.UNDECLARED_WRITE:
        if finding.resource and finding.resource in set(visible_resources):
            return f"step {finding.step} wrote to {finding.resource}, which this task must not touch"
        return f"step {finding.step} changed something this task must not touch"
    return _TEMPLATES[finding.failure_class].format(step=finding.step)


def failure_lines(findings: Iterable[Finding], visible_resources: Iterable[str] = ()) -> list[str]:
    seen: list[str] = []
    for f in findings:
        line = failure_line(f, visible_resources)
        if line not in seen:
            seen.append(line)
    return seen
