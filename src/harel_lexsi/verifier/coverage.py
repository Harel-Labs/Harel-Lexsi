"""V9: plan-coverage check. Is every effect the holdout requires planned by at least one task?

    python -m harel_lexsi.verifier.coverage --spec spec/spec.md [--contracts FILE ...] [--tasks tasks/tasks.jsonl]

A task's `planned_effects` ("resource:op") are written from the Build section only. If a holdout contract
requires an effect that no task plans for, the builder is never given a task in which that effect is part of
the job, so the gap is in the task set, not in the agent.

The report names only resource:op and a count. It never prints a contract id, an input, a starting state or a
matched value, so reading it reveals nothing about the scenarios (the holdout stays hidden from the agent).

Exit code: 0 every holdout effect is planned, 1 some are not, 2 could not run (no contracts, no tasks).
Read-only contracts and failure-path contracts have no effects, so there is nothing for a task to plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from .contracts import Binding, Contract, load_binding, load_holdout
from .gate import DEFAULT_BINDING, GateError, load_spec_contracts
from .replay import ROOT

DEFAULT_TASKS = ROOT / "tasks" / "tasks.jsonl"


def _key(binding: Binding, resource: str, op: str) -> str:
    return f"{binding.table_for(resource) or resource}:{op}"


def holdout_effects(contracts: Iterable[Contract], binding: Binding) -> Counter:
    """How many holdout effects there are per resource:op (resources resolved to their tables)."""
    return Counter(_key(binding, e.resource, e.op) for c in contracts for e in c.effects)


def planned_effects(tasks: Iterable[dict], binding: Binding) -> set[str]:
    out = set()
    for t in tasks:
        for effect in t.get("planned_effects", []):
            resource, op = effect.split(":")
            out.add(_key(binding, resource, op))
    return out


def uncovered(contracts: Iterable[Contract], tasks: Iterable[dict], binding: Binding) -> dict[str, int]:
    """resource:op -> number of holdout effects that no task plans for."""
    planned = planned_effects(tasks, binding)
    return {k: n for k, n in sorted(holdout_effects(contracts, binding).items()) if k not in planned}


def render(gaps: dict[str, int], total: int) -> str:
    if not gaps:
        return f"OK: every holdout effect ({total}) is planned by at least one task"
    lines = [f"FAIL: {sum(gaps.values())} of {total} holdout effects are not planned by any task:"]
    lines += [f"  {n} on {k}" for k, n in gaps.items()]
    lines.append("Add a task that plans these, or correct a task's planned_effects (from the Build section).")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.verifier.coverage")
    p.add_argument("--spec", type=Path, help="use the contracts in this spec's Holdout section")
    p.add_argument("--contracts", nargs="+", type=Path, help="YAML files of contracts")
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--binding", type=Path, default=DEFAULT_BINDING)
    a = p.parse_args(argv)
    try:
        if not a.spec and not a.contracts:
            raise GateError("give --spec or --contracts")
        binding = load_binding(a.binding)
        contracts = [c for f in (a.contracts or []) for c in load_holdout(f, binding)]
        if a.spec:
            contracts += load_spec_contracts(a.spec, binding)
        if not contracts:
            raise GateError("no contracts to check coverage of")
        if not a.tasks.is_file():
            raise GateError(f"no task file at {a.tasks} (run: python -m harel_lexsi.tasks build)")
        tasks = [json.loads(line) for line in a.tasks.read_text().splitlines() if line.strip()]
    except GateError as e:
        print(f"COULD NOT RUN: {e}", file=sys.stderr)
        return 2
    gaps = uncovered(contracts, tasks, binding)
    print(render(gaps, sum(holdout_effects(contracts, binding).values())))
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
