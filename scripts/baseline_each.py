"""Run the T2 baseline one task per process, then merge into one set of outputs.

A single long process on the Mac's MPS backend runs out of memory after a few episodes (the unified
memory is shared with everything else). A fresh process per task releases it all. Finished tasks are
skipped on a rerun, so a crash or a stop costs at most one episode.

    uv run python scripts/baseline_each.py --split all --out runs/baseline-tiny-aya-global-all-20261113

Extra flags are passed through to scripts/baseline.py (e.g. --max-steps, --max-new-tokens, --model).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harel_lexsi.adapters.baseline import EpisodeResult, write_outputs
from harel_lexsi.tasks.check import load_tasks


def _part_ok(part: Path) -> bool:
    f = part / "results.jsonl"
    if not f.is_file() or not f.read_text().strip():
        return False
    return not json.loads(f.read_text().splitlines()[0]).get("error")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["train", "heldout", "all"], default="all")
    p.add_argument("--out", required=True)
    p.add_argument("--retries", type=int, default=1, help="extra tries for a task that errored")
    a, passthrough = p.parse_known_args()

    out = Path(a.out)
    split = json.loads((ROOT / "tasks" / "split.json").read_text())
    tasks = load_tasks(ROOT / "tasks")
    held = set(split["heldout"])
    if a.split != "all":
        tasks = [t for t in tasks if (t["id"] in held) == (a.split == "heldout")]

    for t in tasks:
        part = out / "parts" / t["id"]
        tries = 0
        while not _part_ok(part) and tries <= a.retries:
            tries += 1
            print(f"[{t['id']}] try {tries}", flush=True)
            subprocess.run([sys.executable, str(ROOT / "scripts" / "baseline.py"), "--only", t["id"],
                            "--split", "all", "--out", str(part), *passthrough], cwd=ROOT, check=False)

    results, meta = [], {}
    with (out / "trajectories.jsonl").open("w") as traj_out:
        for t in tasks:
            part = out / "parts" / t["id"]
            if not (part / "results.jsonl").is_file():
                continue
            if not meta:
                meta = json.loads((part / "summary.json").read_text())["meta"]
            results += [EpisodeResult(**json.loads(line)) for line in (part / "results.jsonl").read_text().splitlines()]
            if (part / "trajectories.jsonl").is_file():
                traj_out.write((part / "trajectories.jsonl").read_text())
    meta = {**meta, "split": a.split, "tasks": len(tasks), "run": "one process per task"}
    write_outputs(out, meta, results)
    print(f"{sum(r.verdict == 'pass' for r in results)}/{len(results)} verified; results in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
