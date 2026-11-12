"""G1: factory version records.

A factory version is one frozen record of what L1 was given: its prompt and its memory. Every run is
pinned to one version, and the version id travels on every `harel.step` marker, so any result can be
traced to the exact factory that produced it.

Versions are archived, never overwritten:
- each version is its own file, created exclusively and read-only, so an existing id cannot be reused;
- the file carries a SHA-256 over its own content, so an edit made behind our back is detected on read.

`CURRENT` is only a pointer to the version new runs use. It is the one thing that changes; versions do not.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"v\d{4,}")
_FROM_CURRENT = object()


class VersionError(Exception):
    """Base class for factory-version problems."""


class UnknownVersion(VersionError):
    pass


class NoCurrentVersion(VersionError):
    pass


class VersionCorrupt(VersionError):
    """A stored version no longer matches its own hash."""


@dataclass(frozen=True)
class FactoryVersion:
    id: str
    prompt: str
    memory: str
    parent: str | None
    note: str          # why this version exists (the trigger for the change)
    created: str       # UTC, ISO 8601
    sha256: str        # over every other field


def _digest(fields: dict) -> str:
    body = json.dumps({k: v for k, v in fields.items() if k != "sha256"}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class VersionStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- reading -----
    def _path(self, version_id: str) -> Path:
        if not isinstance(version_id, str) or not _ID.fullmatch(version_id):
            raise UnknownVersion(f"not a version id: {version_id!r}")
        return self.root / f"{version_id}.json"

    def _ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("v*.json") if _ID.fullmatch(p.stem))

    def get(self, version_id: str) -> FactoryVersion:
        path = self._path(version_id)
        if not path.is_file():
            raise UnknownVersion(f"no such factory version: {version_id}")
        raw = json.loads(path.read_text())
        if raw.get("id") != version_id or raw.get("sha256") != _digest(raw):
            raise VersionCorrupt(f"{version_id} does not match its recorded hash")
        return FactoryVersion(**raw)

    def list(self) -> list[FactoryVersion]:
        return [self.get(i) for i in self._ids()]

    def current(self) -> FactoryVersion:
        pointer = self.root / "CURRENT"
        if not pointer.is_file():
            raise NoCurrentVersion("no current factory version has been promoted yet")
        return self.get(pointer.read_text().strip())

    # ----- writing -----
    def create(self, prompt: str, memory: str = "", *, parent=_FROM_CURRENT, note: str = "") -> FactoryVersion:
        """Archive a new version. `parent` defaults to the current version (None if there is none)."""
        if parent is _FROM_CURRENT:
            try:
                parent = self.current().id
            except NoCurrentVersion:
                parent = None
        if parent is not None:
            self.get(parent)  # must exist and be intact
        ids = self._ids()
        n = max((int(i[1:]) for i in ids), default=0) + 1
        while True:
            version_id = f"v{n:04d}"
            fields = {"id": version_id, "prompt": prompt, "memory": memory, "parent": parent,
                      "note": note, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            fields["sha256"] = _digest(fields)
            try:  # exclusive + read-only: an existing id can never be written again
                fd = os.open(self._path(version_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            except FileExistsError:
                n += 1
                continue
            with os.fdopen(fd, "w") as f:
                json.dump(fields, f, indent=2, sort_keys=True)
                f.write("\n")
            return FactoryVersion(**fields)

    def promote(self, version_id: str) -> FactoryVersion:
        """Make an existing version the one new runs use."""
        version = self.get(version_id)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".current-")
        with os.fdopen(fd, "w") as f:
            f.write(version.id + "\n")
        os.replace(tmp, self.root / "CURRENT")
        return version

    def pin(self, version_id: str | None = None) -> FactoryVersion:
        """The version a run is pinned to: the one named, or the current one. Always an intact record."""
        return self.get(version_id) if version_id is not None else self.current()
