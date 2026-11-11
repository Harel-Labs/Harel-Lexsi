from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # missing evidence; never counts as PASS


@dataclass(frozen=True)
class Contract:
    id: str
    required: frozenset[str] = field(default_factory=frozenset)   # effects that must be observed
    forbidden: frozenset[str] = field(default_factory=frozenset)  # effects that must never occur


@dataclass(frozen=True)
class VerdictResult:
    contract_id: str
    verdict: Verdict
    reason: str
