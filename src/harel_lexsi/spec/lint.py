from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from harel_lexsi.verifier.contracts import Contract, ContractError, parse_contracts

from .split import SpecParts, holdout_yaml, parse_resources, split_spec


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    parts: SpecParts | None = None
    holdout_text: str = ""
    contracts: list[Contract] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def lint_spec(text: str) -> LintResult:
    res = LintResult()
    res.parts = parts = split_spec(text)
    for name in parts.missing:
        res.errors.append(f"missing section: ## {name}")
    if parts.missing:
        return res

    resources = parse_resources(parts.resources)
    names = {r.name for r in resources}
    if not resources:
        res.errors.append("Resources section has no resource table rows")

    res.holdout_text = holdout_yaml(parts.holdout)
    try:
        res.contracts = parse_contracts(yaml.safe_load(res.holdout_text) if res.holdout_text else None)
    except (ContractError, yaml.YAMLError) as e:
        res.errors.append(f"holdout: {e}")
        return res

    touched = {e.resource for c in res.contracts for e in c.effects}
    for r in sorted(touched - names):
        res.errors.append(f"holdout names resource {r!r} that is not in Resources")
    for r in resources:
        if r.contested and r.name not in touched:
            res.errors.append(f"contested resource {r.name!r} has no contract touching it")

    for c in res.contracts:
        if re.search(rf"(?<![\w-]){re.escape(c.id)}(?![\w-])", parts.build):
            res.errors.append(f"Build section quotes holdout contract id {c.id}")
        if c.given and len(c.given) > 8 and c.given in parts.build:
            res.errors.append(f"Build section quotes the starting state of contract {c.id}")
    return res
