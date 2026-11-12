"""python -m harel_lexsi.tasks build | freeze --heldout a,b,c | check [--spec spec/spec.md] [--allow-unfrozen]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from harel_lexsi.verifier.gate import GateError

from .build import TASKS_DIR, write_tasks
from .check import check_tasks, freeze


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.tasks")
    p.add_argument("--dir", type=Path, default=TASKS_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="regenerate tasks.jsonl and the start files (refused once frozen)")
    f = sub.add_parser("freeze", help="write split.json BEFORE the first baseline run")
    f.add_argument("--heldout", required=True, help="comma-separated handlers to hold out")
    c = sub.add_parser("check", help="verify the task set and the frozen split")
    c.add_argument("--spec", type=Path, help="also check holdout coverage and that patch bugs are caught (needs Postgres)")
    c.add_argument("--allow-unfrozen", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "build":
            tasks = write_tasks(a.dir)
            print(f"wrote {len(tasks)} tasks to {a.dir}/tasks.jsonl")
            return 0
        if a.cmd == "freeze":
            split = freeze(a.dir, [h.strip() for h in a.heldout.split(",") if h.strip()])
            print(f"frozen: {len(split['train'])} train, {len(split['heldout'])} held out "
                  f"({', '.join(split['heldout_handlers'])}); split_sha256={split['split_sha256']}")
            return 0
        errors = check_tasks(a.dir, spec=a.spec, allow_unfrozen=a.allow_unfrozen)
    except (RuntimeError, ValueError, GateError, psycopg.OperationalError) as e:
        print(f"COULD NOT RUN: {e}", file=sys.stderr)
        return 2
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    if not errors:
        print("OK: task set is sound")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
