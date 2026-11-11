from .types import Contract, Verdict, VerdictResult


def verify(contract: Contract, observed: frozenset[str] | None) -> VerdictResult:
    """Compare observed effects (DB rows, events) with a contract.

    observed=None means no evidence was captured -> UNKNOWN.
    """
    if observed is None:
        return VerdictResult(contract.id, Verdict.UNKNOWN, "no evidence captured")
    touched = contract.forbidden & observed
    if touched:
        return VerdictResult(contract.id, Verdict.FAIL, f"forbidden effect: {sorted(touched)}")
    missing = contract.required - observed
    if missing:
        return VerdictResult(contract.id, Verdict.FAIL, f"missing effect: {sorted(missing)}")
    return VerdictResult(contract.id, Verdict.PASS, "all required effects present, none forbidden")
