"""A1: score a baseline run's trajectories with the LLM-judge reward and save the scores.

    uv run python scripts/judge_run.py runs/hosted-tiny-aya-v3-t09-20261114
    uv run python scripts/judge_run.py RUN --model CohereLabs/c4ai-command-a-03-2025 --out DIR

Needs HF_INF_TOKEN (Inference Providers) in .env. Writes judge_scores.jsonl, judge_summary.json and
judge_summary.md next to the run (or into --out).
"""
from __future__ import annotations

import argparse
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
    p.add_argument("run", type=Path, help="a baseline run folder (results.jsonl + trajectories.jsonl)")
    p.add_argument("--model", default=None, help="judge model on the HF router")
    p.add_argument("--base-url", default=None)
    p.add_argument("--api-key-env", default="HF_INF_TOKEN")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    load_dotenv()

    from harel_lexsi.adapters.judge import (
        DEFAULT_MODEL,
        HF_ROUTER,
        HostedJudge,
        score_run,
    )

    judge = HostedJudge(model=a.model or DEFAULT_MODEL, base_url=a.base_url or HF_ROUTER,
                        api_key=os.environ[a.api_key_env])
    rows = score_run(a.run, judge, a.out)
    s = sum(r["judge_approved"] is True for r in rows)
    u = sum(r["judge_approved"] is None for r in rows)
    print(f"judge approved {s}/{len(rows)} (UNKNOWN {u}); scores in {a.out or a.run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
