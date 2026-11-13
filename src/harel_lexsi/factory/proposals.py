"""G2: proposal records. A proposal is written, frozen and hashed BEFORE the A/B run that will judge it.

Like factory versions (G1), each proposal is its own file, created exclusively and read-only, with a SHA-256
over its content: an id is never reused and an edit made behind our back is detected on read. G3 asks for a
proposal by id and refuses to run without one, so the expected effect cannot be written after the result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"p\d{4,}")


class ProposalError(Exception):
    """Base class for proposal problems."""


class UnknownProposal(ProposalError):
    pass


class ProposalCorrupt(ProposalError):
    """A stored proposal no longer matches its own hash."""


@dataclass(frozen=True)
class Proposal:
    id: str
    base_version: str        # the factory version the change starts from (current when proposed)
    candidate_version: str   # the version that carries the change; NOT promoted until the A/B decides
    trigger: dict            # failure_class, episodes, of, split
    change: dict             # field, added_text
    expected_effect: dict    # written first: what the A/B must show for the change to count as working
    created: str             # UTC, ISO 8601
    sha256: str


def _digest(fields: dict) -> str:
    body = json.dumps({k: v for k, v in fields.items() if k != "sha256"}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class ProposalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, proposal_id: str) -> Path:
        if not isinstance(proposal_id, str) or not _ID.fullmatch(proposal_id):
            raise UnknownProposal(f"not a proposal id: {proposal_id!r}")
        return self.root / f"{proposal_id}.json"

    def _ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("p*.json") if _ID.fullmatch(p.stem))

    def get(self, proposal_id: str) -> Proposal:
        """The proposal, intact. G3 calls this and must not run if it raises."""
        path = self._path(proposal_id)
        if not path.is_file():
            raise UnknownProposal(f"no such proposal: {proposal_id}")
        raw = json.loads(path.read_text())
        if raw.get("id") != proposal_id or raw.get("sha256") != _digest(raw):
            raise ProposalCorrupt(f"{proposal_id} does not match its recorded hash")
        return Proposal(**raw)

    def list(self) -> list[Proposal]:
        return [self.get(i) for i in self._ids()]

    def find(self, base_version: str, failure_class: str) -> Proposal | None:
        """The proposal already made for this base version and failure class, if any."""
        for p in self.list():
            if p.base_version == base_version and p.trigger.get("failure_class") == failure_class:
                return p
        return None

    def create(self, *, base_version: str, candidate_version: str, trigger: dict, change: dict,
               expected_effect: dict) -> Proposal:
        n = max((int(i[1:]) for i in self._ids()), default=0) + 1
        while True:
            proposal_id = f"p{n:04d}"
            fields = {"id": proposal_id, "base_version": base_version, "candidate_version": candidate_version,
                      "trigger": trigger, "change": change, "expected_effect": expected_effect,
                      "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            fields["sha256"] = _digest(fields)
            try:  # exclusive + read-only: an existing id can never be written again
                fd = os.open(self._path(proposal_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            except FileExistsError:
                n += 1
                continue
            with os.fdopen(fd, "w") as f:
                json.dump(fields, f, indent=2, sort_keys=True)
                f.write("\n")
            return Proposal(**fields)
