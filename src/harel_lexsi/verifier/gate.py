"""V7: the Day 1 gate. Does the verifier have teeth?

    python -m harel_lexsi.verifier.gate [--spec spec/spec.md] [--contracts FILE ...] [--log PATH]

Three kinds of handlers are replayed against the contracts and settled:

  reference  correct handlers: every contract must PASS
  trap       S4 traps that slip past the visible tests: must be caught with the expected class
  mutant     V6 mutants (reference code with one thing broken): same

A trap or mutant is *killed* when some contract for its handler FAILs and the failure classes found
across the failing contracts include the expected ones. UNKNOWN is never a pass and never a kill: any
UNKNOWN fails the gate. Exit code: 0 gate passed, 1 gate failed, 2 could not run (no database, no
contracts). A missing database is never a skip.

The default contracts are test fixtures. --spec uses the contracts in the spec's own Holdout section
instead (no full lint needed, so it works while the holdout is half written); --contracts adds YAML files.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import yaml

from harel_lexsi.spec.split import holdout_yaml, split_spec

from .classify import classify
from .contracts import (
    Binding,
    Contract,
    ContractError,
    load_binding,
    load_holdout,
    parse_contracts,
)
from .replay import ROOT, SHOP, _shop_db, replay
from .settle import settle as _settle
from .types import Settlement, Verdict

DEFAULT_CONTRACTS = (ROOT / "tests" / "fixtures" / "shop_contracts.yaml",
                     ROOT / "tests" / "fixtures" / "mutant_contracts.yaml")
DEFAULT_BINDING = ROOT / "spec" / "binding.yaml"

# S4 traps (shop/reference/traps/*): module, handler it replaces, class the verifier must report.
TRAPS = (
    ("reserve_on_add", "add_to_cart", frozenset({"undeclared_write"})),
    ("checkout_no_event", "checkout", frozenset({"missing_event"})),
)

KNOWN_GAPS = (
    "Settlement counts each write but not the net result: a handler that writes and then undoes the "
    "write in the same step still PASSes (xfail test `test_write_then_undo_should_fail`).",
)

SettleFn = Callable[[Contract, Any, str | None, Binding], Settlement]


class GateError(Exception):
    """The gate could not run at all."""


@dataclass(frozen=True)
class Case:
    kind: str                       # reference | trap | mutant
    name: str
    handler: str                    # the handler under test
    handlers: Any                   # module-like object exposing that handler
    expect: frozenset[str] = frozenset()
    contract: Contract | None = None  # reference cases: the one contract; others use the whole pool


@dataclass(frozen=True)
class Outcome:
    case: Case
    verdict: str                    # pass | fail | unknown (fail = killed, for traps and mutants)
    classes: frozenset[str]
    ok: bool
    note: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    contracts: int = 0

    def of(self, kind: str) -> list[Outcome]:
        return [o for o in self.outcomes if o.case.kind == kind]

    @property
    def kill_rate(self) -> float:
        attacks = self.of("trap") + self.of("mutant")
        return sum(o.ok for o in attacks) / len(attacks) if attacks else 0.0

    @property
    def unknown_rate(self) -> float:
        return (sum(o.verdict == "unknown" for o in self.outcomes) / len(self.outcomes)
                if self.outcomes else 0.0)

    @property
    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def ok(self) -> bool:
        return bool(self.of("reference") and (self.of("trap") or self.of("mutant"))
                    and not self.failures and self.unknown_rate == 0.0)

    def render(self) -> str:
        def n(kind):
            xs = self.of(kind)
            return sum(o.ok for o in xs), len(xs)
        rp, rt = n("reference"); tp, tt = n("trap"); mp, mt = n("mutant")
        attacks = tt + mt
        unknown = sum(o.verdict == "unknown" for o in self.outcomes)
        lines = [
            "# Day 1 gate (V7): the verifier has teeth", "",
            f"**Result: {'PASS' if self.ok else 'FAIL'}**", "",
            f"- contracts in the pool: {self.contracts}",
            f"- reference handlers passing: {rp}/{rt}",
            f"- traps caught with the expected class: {tp}/{tt}",
            f"- mutants caught with the expected class: {mp}/{mt}",
            f"- kill rate: {self.kill_rate:.1%} ({tp + mp}/{attacks})",
            f"- UNKNOWN rate: {self.unknown_rate:.1%} ({unknown}/{len(self.outcomes)})", "",
            "| kind | case | handler | verdict | classes found | ok | note |",
            "|---|---|---|---|---|---|---|",
        ]
        for o in self.outcomes:
            lines.append(f"| {o.case.kind} | {o.case.name} | {o.case.handler} | {o.verdict} | "
                         f"{', '.join(sorted(o.classes)) or '-'} | {'yes' if o.ok else '**NO**'} | {o.note} |")
        lines += ["", "## Known open items", *[f"- {g}" for g in KNOWN_GAPS], ""]
        return "\n".join(lines)


def load_spec_contracts(path: Path, binding: Binding) -> list[Contract]:
    """The contracts in the spec's Holdout section (its first ```yaml block that defines `contracts:`)."""
    parts = split_spec(Path(path).read_text())
    if "Holdout" in parts.missing:
        raise GateError(f"{path} has no '## Holdout' section")
    block = holdout_yaml(parts.holdout)
    try:
        return parse_contracts(yaml.safe_load(block) if block else None, binding)
    except (ContractError, yaml.YAMLError) as e:
        raise GateError(f"holdout in {path}: {e}") from None


def check_database() -> None:
    try:
        _shop_db().connect().close()
    except psycopg.OperationalError as e:
        raise GateError(f"Postgres is not reachable ({e}). Run: cd shop && docker compose up -d") from None


def build_cases(contracts: Sequence[Contract]) -> list[Case]:
    if str(SHOP) not in sys.path:
        sys.path.insert(0, str(SHOP))
    from reference import mutants  # noqa: PLC0415  (needs SHOP on sys.path)

    reference = importlib.import_module("reference.handlers")
    cases = [Case("reference", c.id, c.handler, reference, contract=c) for c in contracts]
    for module, handler, expect in TRAPS:
        cases.append(Case("trap", module, handler,
                          importlib.import_module(f"reference.traps.{module}"), expect))
    for m in mutants.MUTANTS:
        cases.append(Case("mutant", m.id, m.handler, mutants.as_handlers(m), m.expect))
    return cases


def _classes(s: Settlement) -> frozenset[str]:
    return frozenset(f.failure_class.value for f in classify(s))


def run_gate(contracts: Sequence[Contract], binding: Binding, *, settle: SettleFn = _settle,
             cases: Sequence[Case] | None = None) -> Report:
    report = Report(contracts=len(contracts))
    for case in cases if cases is not None else build_cases(contracts):
        pool = [case.contract] if case.kind == "reference" else [c for c in contracts if c.handler == case.handler]
        if not pool:
            report.outcomes.append(Outcome(case, "pass", frozenset(), False,
                                           "no contract covers this handler"))
            continue
        settled = []
        for c in pool:
            s = replay(c, case.handlers)
            settled.append(settle(c, s.capture, s.error, binding))
        unknown = any(s.verdict is Verdict.UNKNOWN for s in settled)
        failed = [s for s in settled if s.verdict is Verdict.FAIL]
        found = frozenset().union(*(_classes(s) for s in failed)) if failed else frozenset()
        if case.kind == "reference":
            verdict = "unknown" if unknown else ("fail" if failed else "pass")
            report.outcomes.append(Outcome(case, verdict, found, verdict == "pass",
                                           "" if verdict == "pass" else settled[0].reason))
            continue
        if unknown:
            report.outcomes.append(Outcome(case, "unknown", found, False, "missing evidence"))
        elif not failed:
            report.outcomes.append(Outcome(case, "pass", found, False, "NOT CAUGHT: every contract passed"))
        elif not case.expect <= found:
            report.outcomes.append(Outcome(case, "fail", found, False,
                                           f"caught, but expected {sorted(case.expect)}"))
        else:
            report.outcomes.append(Outcome(case, "fail", found, True))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.verifier.gate")
    p.add_argument("--spec", type=Path, help="use the contracts in this spec's Holdout section")
    p.add_argument("--contracts", nargs="+", type=Path,
                   help="YAML files of contracts (default: the test fixtures, unless --spec is given)")
    p.add_argument("--binding", type=Path, default=DEFAULT_BINDING)
    p.add_argument("--log", type=Path, help="also write the report here (markdown)")
    a = p.parse_args(argv)
    try:
        check_database()
        binding = load_binding(a.binding)
        files = a.contracts if a.contracts is not None else ([] if a.spec else list(DEFAULT_CONTRACTS))
        contracts = [c for f in files for c in load_holdout(f, binding)]
        if a.spec:
            contracts += load_spec_contracts(a.spec, binding)
        if not contracts:
            raise GateError("no contracts to run the gate against"
                            + (f" ({a.spec} has none in its Holdout section yet)" if a.spec else ""))
    except GateError as e:
        print(f"GATE COULD NOT RUN: {e}", file=sys.stderr)
        return 2
    report = run_gate(contracts, binding)
    text = report.render()
    print(text)
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        a.log.write_text(text)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
