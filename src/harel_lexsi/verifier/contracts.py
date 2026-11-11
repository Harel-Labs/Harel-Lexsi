"""V3: load holdout contracts (spec/contract-format.md) into typed, frozen objects.

Unknown fields are rejected. Resources are logical names, bound to tables in one binding file.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

OPS = ("insert", "update", "delete", "emit")
CONTRACT_KEYS = {"id", "handler", "trap", "given", "input", "effects", "atomic", "closure", "expect_error"}
EFFECT_KEYS = {"id", "resource", "op", "count", "match", "change", "event"}


class ContractError(ValueError):
    """The holdout file is malformed. The message names the contract and the problem."""


@dataclass(frozen=True)
class Cardinality:
    lo: int
    hi: int | None  # None = unbounded

    def contains(self, n: int) -> bool:
        return n >= self.lo and (self.hi is None or n <= self.hi)


def parse_cardinality(value: Any) -> Cardinality:
    if isinstance(value, bool):
        raise ContractError(f"bad count: {value!r}")
    if isinstance(value, int) and value >= 0:
        return Cardinality(value, value)
    if isinstance(value, str):
        s = value.strip()
        if m := re.fullmatch(r"(\d+)\.\.(\d+)", s):
            lo, hi = int(m[1]), int(m[2])
            if lo <= hi:
                return Cardinality(lo, hi)
        elif m := re.fullmatch(r">=\s*(\d+)", s):
            return Cardinality(int(m[1]), None)
        elif s.isdigit():
            return Cardinality(int(s), int(s))
    raise ContractError(f"bad count: {value!r} (use 1, '0..1' or '>=1')")


@dataclass(frozen=True)
class Effect:
    id: str
    resource: str
    op: str
    count: Cardinality
    match: dict = field(default_factory=dict)
    change: dict = field(default_factory=dict)
    event: str | None = None


@dataclass(frozen=True)
class Contract:
    id: str
    handler: str
    effects: tuple[Effect, ...] = ()
    input: dict = field(default_factory=dict)
    given: str = ""
    trap: str | None = None
    atomic: tuple[str, ...] = ()
    closure: bool = False
    expect_error: str | None = None


@dataclass(frozen=True)
class Binding:
    """Logical resource -> table. Keys may be glob patterns (e.g. 'order*')."""
    mapping: dict[str, str]

    def table_for(self, resource: str) -> str | None:
        if resource in self.mapping:
            return self.mapping[resource]
        for pattern, table in self.mapping.items():
            if fnmatch.fnmatchcase(resource, pattern):
                return table
        return None

    def resource_for(self, table: str) -> str | None:
        for resource, t in self.mapping.items():
            if t == table:
                return resource
        return None


def load_binding(path: str | Path) -> Binding:
    data = yaml.safe_load(Path(path).read_text()) or {}
    unknown = set(data) - {"resources"}
    if unknown or not isinstance(data.get("resources"), dict):
        raise ContractError("binding file must have exactly one key: resources (a mapping)")
    return Binding({str(k): str(v) for k, v in data["resources"].items()})


def _reject_unknown(obj: dict, allowed: set[str], where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise ContractError(f"{where}: unknown field(s) {sorted(extra)}")


def _effect(raw: Any, cid: str) -> Effect:
    if not isinstance(raw, dict):
        raise ContractError(f"{cid}: each effect must be a mapping")
    _reject_unknown(raw, EFFECT_KEYS, f"{cid} effect {raw.get('id', '?')}")
    for key in ("id", "resource", "op"):
        if key not in raw:
            raise ContractError(f"{cid}: effect is missing '{key}'")
    where = f"{cid} effect {raw['id']}"
    if raw["op"] not in OPS:
        raise ContractError(f"{where}: op must be one of {OPS}, got {raw['op']!r}")
    if raw["op"] == "emit" and not raw.get("event"):
        raise ContractError(f"{where}: an emit effect needs an event")
    try:
        count = parse_cardinality(raw.get("count", 1))
    except ContractError as e:
        raise ContractError(f"{where}: {e}") from None
    return Effect(
        id=str(raw["id"]), resource=str(raw["resource"]), op=raw["op"], count=count,
        match=dict(raw.get("match") or {}), change=dict(raw.get("change") or {}),
        event=raw.get("event"),
    )


def _contract(raw: Any) -> Contract:
    if not isinstance(raw, dict):
        raise ContractError("each contract must be a mapping")
    cid = str(raw.get("id", "?"))
    _reject_unknown(raw, CONTRACT_KEYS, cid)
    for key in ("id", "handler"):
        if key not in raw:
            raise ContractError(f"{cid}: missing '{key}'")
    effects = tuple(_effect(e, cid) for e in (raw.get("effects") or []))
    ids = [e.id for e in effects]
    if len(set(ids)) != len(ids):
        raise ContractError(f"{cid}: duplicate effect ids")
    atomic = tuple(str(a) for a in (raw.get("atomic") or []))
    missing = [a for a in atomic if a not in ids]
    if missing:
        raise ContractError(f"{cid}: atomic names unknown effect(s) {missing}")
    if raw.get("expect_error") and effects:
        raise ContractError(f"{cid}: a failure-path contract (expect_error) must have no effects")
    return Contract(
        id=cid, handler=str(raw["handler"]), effects=effects, input=dict(raw.get("input") or {}),
        given=str(raw.get("given") or ""), trap=raw.get("trap"), atomic=atomic,
        closure=bool(raw.get("closure", False)), expect_error=raw.get("expect_error"),
    )


def parse_contracts(data: Any, binding: Binding | None = None) -> list[Contract]:
    """Accepts a list of contracts or {contracts: [...]} (None or empty means no contracts)."""
    if isinstance(data, dict):
        _reject_unknown(data, {"contracts"}, "holdout file")
        data = data.get("contracts")
    if data is None:
        return []
    if not isinstance(data, list):
        raise ContractError("holdout must be a list of contracts")
    contracts = [_contract(c) for c in data]
    ids = [c.id for c in contracts]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ContractError(f"duplicate contract ids: {dupes}")
    if binding is not None:
        for c in contracts:
            for e in c.effects:
                if binding.table_for(e.resource) is None:
                    raise ContractError(f"{c.id} effect {e.id}: resource {e.resource!r} has no binding")
    return contracts


def load_holdout(path: str | Path, binding: Binding | None = None) -> list[Contract]:
    return parse_contracts(yaml.safe_load(Path(path).read_text()), binding)
