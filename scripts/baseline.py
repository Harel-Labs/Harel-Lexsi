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
import gc
import hashlib
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
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.9,
                   help="sampling temperature; 0 = greedy (a small model then repeats its failing code)")
    p.add_argument("--max-length", type=int, default=8192, help="context window (Tiny Aya: 8K)")
    p.add_argument("--backend", choices=["local", "api"], default="local",
                   help="local: load the model here (T2 as specified). api: hosted endpoint, native tool calling")
    p.add_argument("--api-model", default="openai/CohereLabs/tiny-aya-global", help="litellm model name")
    p.add_argument("--api-base-url", default="https://router.huggingface.co/v1")
    p.add_argument("--api-key-env", default="HF_INF_TOKEN", help="env var holding the API key")
    p.add_argument("--factory-version", default=None)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    load_dotenv()

    from agenttune.agentic import create_rollout_fn

    from harel_lexsi.adapters.baseline import run_baseline
    from harel_lexsi.adapters.builder_prompt import (
        TOOL_RESULT,
        TOOLS_FALLBACK,
        system_prompt,
    )
    from harel_lexsi.adapters.episode_coach import EpisodeCoach
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

    sandbox = ShopSandbox(factory_version=a.factory_version)
    if a.backend == "api":
        from transformers import AutoTokenizer

        from harel_lexsi.adapters.hosted_engine import make_hosted_engine

        device, revision, torch = f"hosted {a.api_base_url}", None, None
        engine = make_hosted_engine(api_model=a.api_model, api_key=os.environ[a.api_key_env],
                                    base_url=a.api_base_url, tokenizer=AutoTokenizer.from_pretrained(a.model),
                                    target_path=sandbox.target_path, is_done=lambda: sandbox.green)
        engine_args = {"rollout_engine": engine}
        gen_args = {"max_new_tokens": a.max_new_tokens, "temperature": a.temperature}
    else:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float32 if device == "cpu" else torch.bfloat16
        tokenizer = AutoTokenizer.from_pretrained(a.model)
        model = AutoModelForCausalLM.from_pretrained(a.model, dtype=dtype).to(device).eval()
        revision = getattr(model.config, "_commit_hash", None)
        engine_args = {"model": model, "tokenizer": tokenizer, "rollout_backend": "transformers"}
        gen_args = {"max_new_tokens": a.max_new_tokens, "max_length": a.max_length,
                    "do_sample": a.temperature > 0}
        if a.temperature > 0:
            gen_args["temperature"] = a.temperature

    captured: list = []
    coach = EpisodeCoach(sandbox)
    rollout_fn = create_rollout_fn(
        **engine_args, tools=list(make_tools(sandbox).values()), max_steps=a.max_steps,
        on_trajectory_end=captured.append,
        pre_step_hook=coach.pre_step, post_step_hook=coach.post_step,  # tests up front, compact repairs
        system_prompt=system_prompt(), tools_fallback_prompt=TOOLS_FALLBACK, tool_result_format=TOOL_RESULT,  # conventions, shared rules, schema: see builder_prompt
        context_length=a.max_length - a.max_new_tokens - 64,  # stop cleanly before the window overflows
    )

    def free_memory() -> None:
        gc.collect()
        if device == "mps":
            torch.mps.empty_cache()
        elif device == "cuda":
            torch.cuda.empty_cache()

    def rollout(prompt: str):
        captured.clear()
        try:
            rollout_fn(prompt, **gen_args)
        finally:
            free_memory()  # the KV cache of one episode must not pile up under the next (MPS OOM)
        if not captured:
            raise RuntimeError("rollout produced no trajectory")
        return captured[-1]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.out or ROOT / "runs" / f"baseline-{a.model.split('/')[-1]}-{a.split}-{stamp}"
    spec = ROOT / "spec" / "spec.md"  # the holdout contracts; score_files without a spec uses test fixtures
    scorer = lambda files, handler: score_files(files, handler, spec=spec)
    meta = {"model": a.model, "spec_sha256": hashlib.sha256(spec.read_bytes()).hexdigest(), "revision": revision, "split": a.split, "tasks": len(tasks),
            "split_sha256": split["split_sha256"], "factory_version": a.factory_version,
            "device": device, "backend": a.backend, "api_model": a.api_model if a.backend == "api" else None, "max_steps": a.max_steps, "max_new_tokens": a.max_new_tokens,
            "temperature": a.temperature, "date": dt.date.today().isoformat()}
    try:
        results = run_baseline(tasks, split=split, sandbox=sandbox, rollout=rollout,
                               start_for=patch_start_for, scorer=scorer, out=out, meta=meta)
    finally:
        sandbox.close()
    v = sum(r.verdict == "pass" for r in results)
    print(f"{v}/{len(results)} verified; results in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
