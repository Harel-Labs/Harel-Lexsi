"""V4: settle one contract against captured effects: PASS, FAIL or UNKNOWN. Model-free."""
from __future__ import annotations

import re
from typing import Any

from .capture import CaptureResult, RowChange
from .contracts import Binding, Contract, Effect
from .types import Settlement, Verdict, Violation


class _Unknown(Exception):
    pass


def _resolve(value: Any, contract: Contract) -> Any:
    if isinstance(value, str) and value.startswith("$given."):
        raise _Unknown("contract uses $given, which settlement cannot resolve yet")
    if isinstance(value, str) and value.startswith("$input."):
        key = value[len("$input."):]
        if key not in contract.input:
            raise _Unknown(f"contract refers to missing input {key!r}")
        return contract.input[key]
    return value


def _eq(a: Any, b: Any) -> bool:
    return a == b or (a is not None and b is not None and str(a) == str(b))


def _fields_match(rows: list[dict | None], match: dict, contract: Contract) -> bool:
    return all(any(r is not None and k in r and _eq(r[k], _resolve(v, contract)) for r in rows)
               for k, v in match.items())


def _is_candidate(c: RowChange, e: Effect, table: str, contract: Contract) -> bool:
    if c.table != table:
        return False
    if e.op == "emit":
        row = c.after or {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return (c.op == "insert" and row.get("event") == e.event
                and _fields_match([payload], e.match, contract))
    if c.op != e.op:
        return False
    return _fields_match([c.before, c.after], e.match, contract)


def _change_ok(c: RowChange, e: Effect, contract: Contract) -> bool:
    for field, spec in e.change.items():
        after = (c.after or {}).get(field)
        before = (c.before or {}).get(field)  # null columns are omitted from the old row
        if isinstance(spec, str) and re.fullmatch(r"[+-]\d+", spec):
            if after is None or not isinstance(after, int) or after - (before or 0) != int(spec):
                return False
        elif not _eq(after, _resolve(spec, contract)):
            return False
    return True


def _net(changes: list[RowChange]) -> list[RowChange]:
    """Cancel a row inserted and then deleted inside the same step: its net effect is nothing.
    Null columns are omitted from old rows, so compare without them."""
    strip = lambda row: {k: v for k, v in (row or {}).items() if v is not None}
    out = list(changes)
    for i, c in enumerate(changes):
        if c.op != "delete":
            continue
        for j, earlier in enumerate(changes[:i]):
            if (earlier.op == "insert" and changes[j] in out and earlier.table == c.table
                    and strip(earlier.after) == strip(c.before)):
                out.remove(earlier)
                out.remove(c)
                break
    return out


def settle(contract: Contract, capture: CaptureResult, error: str | None, binding: Binding) -> Settlement:
    cid = contract.id
    if not capture.steps:
        return Settlement(cid, Verdict.UNKNOWN, "no step markers found in the capture")
    if not capture.complete:
        return Settlement(cid, Verdict.UNKNOWN, "capture is incomplete")
    step = capture.steps[0]
    changes = _net(step.changes)
    n = step.step
    viol: list[Violation] = []
    present: dict[str, bool] = {}  # did the handler touch what this effect is about at all
    claimed: set[int] = set()

    try:
        for e in contract.effects:
            table = binding.table_for(e.resource)
            if table is None:
                raise _Unknown(f"resource {e.resource!r} has no binding")
            cands = [i for i, c in enumerate(changes)
                     if i not in claimed and _is_candidate(c, e, table, contract)]
            good = [i for i in cands if _change_ok(changes[i], e, contract)]
            claimed.update(cands)
            present[e.id] = bool(cands)
            if cands and not good:
                viol.append(Violation("value_mismatch", n, e.id, e.op, e.resource, "wrong after-value"))
            elif not e.count.contains(len(good)):
                kind = "effect_absent" if not good else "count_out_of_range"
                viol.append(Violation(kind, n, e.id, e.op, e.resource, f"found {len(good)}"))
    except _Unknown as u:
        return Settlement(cid, Verdict.UNKNOWN, str(u))

    if contract.closure:
        for i, c in enumerate(changes):
            if i not in claimed:
                res = binding.resource_for(c.table) or c.table
                viol.append(Violation("closure_breach", n, None, None, res, f"{c.op} on {c.table}"))

    # Atomic set: judged over its writes. A missing event is reported on its own (missing_event).
    ops = {e.id: e.op for e in contract.effects}
    writes = [a for a in contract.atomic if ops[a] != "emit"]
    done = [a for a in writes if present[a]]
    if writes and 0 < len(done) < len(writes):
        viol = [v for v in viol if not (v.kind == "effect_absent" and v.effect_id in writes)]
        viol.append(Violation("atomic_partial", n, None, None, None, f"{len(done)}/{len(writes)} writes"))

    if contract.expect_error:
        if error != contract.expect_error:
            viol.append(Violation("error_mismatch", n, detail=f"expected {contract.expect_error}, got {error}"))
    elif error is not None:
        viol.append(Violation("error_mismatch", n, detail=f"unexpected error {error}"))

    if viol:
        return Settlement(cid, Verdict.FAIL, f"{len(viol)} violation(s)", tuple(viol))
    return Settlement(cid, Verdict.PASS, "every required effect present, nothing undeclared")
