"""T2 baseline: run the untrained builder on the S5 tasks and score it with the Harel verifier.

    uv run python scripts/baseline.py --limit 2                 # smoke: first two tasks
    uv run python scripts/baseline.py --split train             # the real runs
    uv run python scripts/baseline.py --split heldout

Needs Postgres (cd shop && docker compose up -d), the frozen tasks/split.json, and HF_TOKEN (read from the
environment or .env) for the gated model. Episodes run one at a time on the shared database.
Output: <out>/trajectories.jsonl, results.jsonl, summary.json, summary.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="CohereLabs/tiny-aya-global")
    p.add_argument("--split", choices=["train", "heldout", "all"], default="all")
    p.add_argument("--limit", type=int, default=0, help="run only the first N selected tasks")
    p.add_argument("--only", default="", help="comma-separated task ids")
    p.add_argument("--max-steps", type=int, default=20)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--max-length", type=int, default=8192, help="context window (Tiny Aya: 8K)")
    p.add_argument("--factory-version", default=None)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    load_dotenv()

    import torch
    from agenttune.agentic import create_rollout_fn
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from harel_lexsi.adapters.baseline import run_baseline
    from harel_lexsi.adapters.shop_harness import (
        ShopSandbox,
        make_tools,
        patch_start_for,
    )
    from harel_lexsi.tasks.check import load_tasks
    from harel_lexsi.verifier.score import score_files

    split = json.loads((ROOT / "tasks" / "split.json").read_text())  # missing file = not frozen: stop
    tasks = load_tasks(ROOT / "tasks")
    if a.split != "all":
        tasks = [t for t in tasks if t["id"] in set(split[a.split])]
    if a.only:
        tasks = [t for t in tasks if t["id"] in set(a.only.split(","))]
    if a.limit:
        tasks = tasks[: a.limit]

    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float32 if device == "cpu" else torch.bfloat16
    tokenizer = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=dtype).to(device).eval()
    revision = getattr(model.config, "_commit_hash", None)

    sandbox = ShopSandbox(factory_version=a.factory_version)
    captured: list = []
    rollout_fn = create_rollout_fn(
        model=model, tokenizer=tokenizer, rollout_backend="transformers",
        tools=list(make_tools(sandbox).values()), max_steps=a.max_steps,
        on_trajectory_end=captured.append,
    )

    def rollout(prompt: str):
        captured.clear()
        rollout_fn(prompt, max_new_tokens=a.max_new_tokens, max_length=a.max_length, do_sample=False)
        if not captured:
            raise RuntimeError("rollout produced no trajectory")
        return captured[-1]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.out or ROOT / "runs" / f"baseline-{a.model.split('/')[-1]}-{a.split}-{stamp}"
    meta = {"model": a.model, "revision": revision, "split": a.split, "tasks": len(tasks),
            "split_sha256": split["split_sha256"], "factory_version": a.factory_version,
            "device": device, "max_steps": a.max_steps, "max_new_tokens": a.max_new_tokens,
            "do_sample": False, "date": dt.date.today().isoformat()}
    try:
        results = run_baseline(tasks, split=split, sandbox=sandbox, rollout=rollout,
                               start_for=patch_start_for, scorer=score_files, out=out, meta=meta)
    finally:
        sandbox.close()
    v = sum(r.verdict == "pass" for r in results)
    print(f"{v}/{len(results)} verified; results in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
