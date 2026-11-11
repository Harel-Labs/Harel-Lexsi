"""Replay one contract against a handlers module and capture what that call changed.

Reset the shop DB, apply the contract's `given` fixture (spec/fixtures/<given>.sql, if such a file
exists; otherwise `given` is prose and the seed state is used), then call the handler with the
contract's input between `harel.step` markers and read that window from the WAL.
"""
from __future__ import annotations

import importlib.util
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .capture import Capture, CaptureResult
from .contracts import Contract

ROOT = Path(__file__).resolve().parents[3]
SHOP = ROOT / "shop"
FIXTURES = ROOT / "spec" / "fixtures"


@dataclass
class Scenario:
    capture: CaptureResult
    error: str | None  # error code the handler raised; "crash:<Type>" for anything else


def _shop_db() -> ModuleType:
    spec = importlib.util.spec_from_file_location("shop_tools_db", SHOP / "tools" / "db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mark(conn, step: int, tool: str, phase: str) -> None:
    payload = json.dumps({"episode": "replay", "step": step, "tool": tool, "phase": phase})
    conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)", (payload,))


def replay(contract: Contract, handlers: ModuleType, *, step: int = 1,
           fixtures_dir: Path = FIXTURES) -> Scenario:
    db = _shop_db()
    conn = db.connect()
    try:
        db.reset(conn)
        if re.fullmatch(r"\w+", contract.given or ""):
            fixture = fixtures_dir / f"{contract.given}.sql"
            if fixture.is_file():
                conn.execute(fixture.read_text())
        with Capture(db.DSN, slot=f"harel_{uuid.uuid4().hex[:8]}") as cap:
            tool = f"scenario:{contract.id}"
            _mark(conn, step, tool, "start")
            error = None
            try:
                getattr(handlers, contract.handler)(conn, **contract.input)
            except Exception as e:  # noqa: BLE001  any failure is evidence, not a crash of the verifier
                error = getattr(e, "code", None) or f"crash:{type(e).__name__}"
            _mark(conn, step, tool, "end")
            result = cap.read()
    finally:
        conn.close()
    return Scenario(result, error)
