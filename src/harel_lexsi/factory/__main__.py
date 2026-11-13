"""Factory governance commands.

    python -m harel_lexsi.factory init --prompt-file P [--memory-file M]     first version, becomes current
    python -m harel_lexsi.factory propose --summary runs/<run>/summary.json  G2: one change + expected effect
    python -m harel_lexsi.factory show p0001                                 print a frozen proposal

Records live in factory/versions/ and factory/proposals/ (override with --versions / --proposals).
Exit codes for `propose`: 0 a proposal exists (new, or the one already made), 1 nothing repeated enough to
act on, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from harel_lexsi.verifier.replay import ROOT

from .governor import DEFAULT_MIN_COUNT, propose
from .proposals import ProposalError, ProposalStore
from .versions import VersionError, VersionStore


def _summary(path: Path) -> dict:
    data = json.loads(path.read_text())
    return data["summary"] if isinstance(data, dict) and "summary" in data else data  # baseline summary.json


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.factory")
    p.add_argument("--versions", type=Path, default=ROOT / "factory" / "versions")
    p.add_argument("--proposals", type=Path, default=ROOT / "factory" / "proposals")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="archive the first factory version and make it current")
    i.add_argument("--prompt-file", type=Path, required=True)
    i.add_argument("--memory-file", type=Path)
    q = sub.add_parser("propose", help="G2: propose one change from the baseline's repeated failure class")
    q.add_argument("--summary", type=Path, required=True, help="a baseline summary.json")
    q.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    s = sub.add_parser("show", help="print a frozen proposal")
    s.add_argument("proposal_id")
    a = p.parse_args(argv)

    try:
        versions, proposals = VersionStore(a.versions), ProposalStore(a.proposals)
        if a.cmd == "init":
            if versions.list():
                print("COULD NOT RUN: factory versions already exist; they are never overwritten", file=sys.stderr)
                return 2
            v = versions.create(a.prompt_file.read_text(), a.memory_file.read_text() if a.memory_file else "",
                                parent=None, note="initial factory")
            versions.promote(v.id)
            print(f"{v.id} archived and current")
            return 0
        if a.cmd == "show":
            print(json.dumps(asdict(proposals.get(a.proposal_id)), indent=2, sort_keys=True))
            return 0
        summary = _summary(a.summary)
        made = propose(versions, proposals, summary, min_count=a.min_count)
    except (ProposalError, VersionError, OSError, ValueError, KeyError) as e:
        print(f"COULD NOT RUN: {e}", file=sys.stderr)
        return 2
    if made is None:
        n = (summary.get("train") or {}).get("n", 0)
        hint = ("" if n else " (the summary has no training episodes: run the baseline with --split all or train)")
        print(f"no proposal: no failure class repeated at least {a.min_count} times in training{hint}")
        return 1
    print(f"{made.id}: base {made.base_version} -> candidate {made.candidate_version}, "
          f"trigger {made.trigger['failure_class']} ({made.trigger['episodes']} of {made.trigger['of']} "
          f"training episodes). Expected effect is recorded; the candidate is not promoted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
