"""V1 + V2: read row changes and outbox events from the WAL and attribute each to one step.

A step is the span between a `harel.step` start marker and its end marker (see
adapters/shop_harness.py and verifier/replay.py). The slot is temporary, so it disappears with
its session even if the process crashes, and cannot pin WAL.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row

MARKER_PREFIX = "harel.step"
_COL = re.compile(r"(\w+)\[([^\]]+)\]:")
_INT = {"integer", "bigint", "smallint"}


@dataclass(frozen=True)
class RowChange:
    table: str
    op: str                       # insert | update | delete
    before: dict | None           # needs REPLICA IDENTITY FULL for updates and deletes
    after: dict | None


@dataclass(frozen=True)
class Event:
    name: str
    payload: Any


@dataclass
class Step:
    step: int
    tool: str
    changes: list[RowChange] = field(default_factory=list)

    @property
    def events(self) -> list[Event]:
        """V2: outbox inserts in this step, attributed through the same markers."""
        return [Event(c.after["event"], c.after.get("payload")) for c in self.changes
                if c.table == "outbox" and c.op == "insert" and c.after]


@dataclass
class CaptureResult:
    steps: list[Step] = field(default_factory=list)
    unattributed: list[RowChange] = field(default_factory=list)
    complete: bool = True         # False if a line could not be parsed or a marker never closed


def _coerce(type_name: str, raw: str | None) -> Any:
    if raw is None:
        return None
    if type_name in _INT:
        return int(raw)
    if type_name == "boolean":
        return raw in ("t", "true")
    if type_name in ("json", "jsonb"):
        return json.loads(raw)
    return raw


def parse_tuple(text: str) -> dict:
    """Parse `name[type]:value ...` as printed by test_decoding."""
    out: dict[str, Any] = {}
    pos = 0
    while True:
        m = _COL.search(text, pos)
        if not m:
            return out
        name, type_name = m.group(1), m.group(2)
        i = m.end()
        if i < len(text) and text[i] == "'":
            j, buf = i + 1, []
            while j < len(text):
                if text[j] == "'":
                    if text[j + 1 : j + 2] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            out[name] = _coerce(type_name, "".join(buf))
            pos = j + 1
        else:
            j = text.find(" ", i)
            j = len(text) if j == -1 else j
            token = text[i:j]
            out[name] = None if token == "null" else _coerce(type_name, token)
            pos = j


_ROW = re.compile(r"^table (?:\w+)\.(\w+): (INSERT|UPDATE|DELETE): (.*)$")


def parse_row(line: str) -> RowChange | None:
    m = _ROW.match(line)
    if not m:
        return None
    table, op, rest = m.group(1), m.group(2).lower(), m.group(3)
    if op == "insert":
        return RowChange(table, op, None, parse_tuple(rest))
    if op == "delete":
        return RowChange(table, op, parse_tuple(rest.removeprefix("old-key: ")), None)
    if rest.startswith("old-key: ") and " new-tuple: " in rest:
        old, new = rest.removeprefix("old-key: ").split(" new-tuple: ", 1)
        return RowChange(table, op, parse_tuple(old), parse_tuple(new))
    return RowChange(table, op, None, parse_tuple(rest.removeprefix("new-tuple: ")))


_MSG = re.compile(r"^message: transactional: \d prefix: (\S+), sz: \d+ content:(.*)$")


def attribute(lines: list[str]) -> CaptureResult:
    """Walk decoded lines in commit order and assign each row change to the open step."""
    res = CaptureResult()
    open_step: Step | None = None
    for line in lines:
        if line.startswith(("BEGIN", "COMMIT")):
            continue
        if m := _MSG.match(line):
            if m.group(1) != MARKER_PREFIX:
                continue
            try:
                info = json.loads(m.group(2))
            except json.JSONDecodeError:
                res.complete = False
                continue
            if info.get("phase") == "start":
                if open_step is not None:
                    res.complete = False
                open_step = Step(int(info["step"]), str(info["tool"]))
            elif info.get("phase") == "end" and open_step is not None:
                res.steps.append(open_step)
                open_step = None
            continue
        change = parse_row(line)
        if change is None:
            res.complete = False if line.startswith("table ") else res.complete
            continue
        (open_step.changes if open_step is not None else res.unattributed).append(change)
    if open_step is not None:
        res.complete = False
    return res


class Capture:
    """Own session holding a temporary logical slot. Create it before the activity you want."""

    def __init__(self, dsn: str, slot: str = "harel_capture"):
        self.slot = slot
        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self._conn.execute(
            "SELECT pg_create_logical_replication_slot(%s, 'test_decoding', true)", (slot,)
        )

    def read(self) -> CaptureResult:
        rows = self._conn.execute(
            "SELECT data FROM pg_logical_slot_get_changes(%s, NULL, NULL, "
            "'include-xids', '0', 'skip-empty-xacts', '1')", (self.slot,)
        ).fetchall()
        return attribute([r["data"] for r in rows])

    def close(self) -> None:
        self._conn.close()  # a temporary slot is dropped with its session

    def __enter__(self) -> Capture:  # noqa: PYI034
        return self

    def __exit__(self, *exc) -> None:
        self.close()
