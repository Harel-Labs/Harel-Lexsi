"""T3: a first short GRPO run of Tiny Aya on the shop tasks, rewarded by the Harel verifier.

    # on the Mac, a pipeline test with a tiny model (not a quality run):
    uv run python scripts/train_t3.py --model Qwen/Qwen2.5-0.5B-Instruct --max-steps 1 --num-generations 2 \
        --batch-size 2 --out runs/t3-dry
    # in an HF Job (see scripts/t3_job.py): Postgres is started inside the container
    python scripts/train_t3.py --postgres --max-steps 10 --out /out/t3

    python scripts/train_t3.py --postgres --check     # container check: scores a known-good handler, no model

Each episode: the model sees the task, the tables, the visible tests and the current code, writes the function
as a ```python block, gets the visible-test result back, and may repair it for up to --max-steps-per-turn
turns. The reward is `harel_settlement_reward`: the verifier's verdict on the final files, in a subprocess.
LoRA (r=16, alpha=32, all-linear), no vLLM.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SPEC = ROOT / "spec" / "spec.md"


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def sh(cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, check=True, text=True, **kw)


def start_postgres(port: int = 5433) -> None:
    """Install and start a throwaway Postgres with logical decoding, as the shop's docker-compose does.

    For a Job container (Debian, root). The data directory is in /tmp and fsync is off: it is disposable.
    The verifier reads row changes with the `test_decoding` plugin, which ships with the server package."""
    if shutil.which("psql") is None:
        sh("apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql")
    bindir = sorted(Path("/usr/lib/postgresql").glob("*/bin"))[-1]
    data = Path("/tmp/pgdata")
    shutil.rmtree(data, ignore_errors=True)
    sh(f"mkdir -p {data} && chown postgres:postgres {data}")
    sh(f"su postgres -c '{bindir}/initdb -D {data} -A trust' > /dev/null")
    opts = (f"-p {port} -c listen_addresses=localhost -c wal_level=logical -c max_replication_slots=10 "
            f"-c max_wal_senders=10 -c fsync=off -c synchronous_commit=off")
    sh(f"su postgres -c \"{bindir}/pg_ctl -D {data} -o '{opts}' -l /tmp/pg.log -w start\" > /dev/null")
    psql = f"su postgres -c \"{bindir}/psql -p {port} -qAt -c \\\"%s\\\"\""
    sh(psql % "CREATE ROLE shop SUPERUSER LOGIN PASSWORD 'shop'")
    sh(psql % "CREATE DATABASE shop OWNER shop")
    os.environ.setdefault("SHOP_DSN", f"postgresql://shop:shop@localhost:{port}/shop")
    print(f"[t3] postgres up on :{port}", flush=True)


def check() -> int:
    """Container check, no model: a reference handler written through the harness must verify as PASS."""
    from harel_lexsi.adapters.settlement_reward import final_files
    from harel_lexsi.adapters.shop_harness import ShopSandbox, make_tools
    from harel_lexsi.verifier.score import score_files

    sb = ShopSandbox()
    sb.reset(handler="clear_cart")
    ref = (ROOT / "shop" / "reference" / "handlers" / "cart.py").read_text()
    from harel_lexsi.adapters.module_merge import merge_module
    from harel_lexsi.adapters.train_rollout import load_tasks, training_prompt

    tasks = load_tasks()
    task = next(t for t in tasks if t["id"] == "implement-clear_cart")
    sb.reset(handler=task["handler"])
    out = make_tools(sb)["write_file"]("app/handlers/cart.py", merge_module("", ref))
    print("[t3] write_file ->", json.dumps(out)[:300])
    traj = {"task": training_prompt(task), "steps": [{"action": {"tool_calls": [{"name": "write_file", "arguments": {
        "path": "app/handlers/cart.py", "content": ref}}]}}]}
    verdicts = [s.verdict.value for s in score_files(final_files(traj), task["handler"], spec=SPEC)]
    print(f"[t3] verifier on the reference clear_cart: {verdicts}", flush=True)
    sb.close()
    ok = bool(verdicts) and set(verdicts) == {"pass"} and not out["tests"]["failed"]
    print("[t3] CHECK", "OK" if ok else "FAILED", flush=True)
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="CohereLabs/tiny-aya-global")
    p.add_argument("--out", type=Path, default=ROOT / "runs" / "t3")
    p.add_argument("--postgres", action="store_true", help="start a Postgres inside this container first")
    p.add_argument("--check", action="store_true", help="check the container (Postgres + verifier), no training")
    p.add_argument("--split", choices=["train", "all"], default="train")
    p.add_argument("--max-steps", type=int, default=10)
    p.add_argument("--num-generations", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=4, help="completions per device per step (multiple of --num-generations)")
    p.add_argument("--grad-accum", type=int, default=1)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--max-completion-length", type=int, default=768, help="tokens per turn")
    p.add_argument("--max-steps-per-turn", type=int, default=3, help="repair turns per episode")
    p.add_argument("--context-length", type=int, default=7168)
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    load_dotenv()
    if a.postgres:
        start_postgres()
    if a.check:
        return check()

    import torch
    from agenttune.agentic import create_rollout_fn
    from agenttune.agentic.trajectory.dataset import trajectory_writer
    from agenttune.core.backend_factory import create_agentic_trainer
    from datasets import Dataset

    from harel_lexsi.adapters.builder_prompt import TOOL_RESULT, TOOLS_FALLBACK, system_prompt
    from harel_lexsi.adapters.settlement_reward import make_harel_settlement_reward
    from harel_lexsi.adapters.shop_harness import ShopSandbox, make_tools
    from harel_lexsi.adapters.train_rollout import EpisodeReset, install_block_writes, load_tasks, training_rows

    split = json.loads((ROOT / "tasks" / "split.json").read_text())
    held = set(split["heldout"])
    tasks = [t for t in load_tasks() if a.split == "all" or t["id"] not in held]  # never train on held-out
    assert not (a.split == "train" and any(t["id"] in held for t in tasks))
    print(f"[t3] {len(tasks)} training tasks, model {a.model}, split sha {split['split_sha256'][:12]}", flush=True)

    a.out.mkdir(parents=True, exist_ok=True)
    sandbox = ShopSandbox()
    install_block_writes(sandbox)
    rollout_fn = create_rollout_fn(
        tools=list(make_tools(sandbox).values()), max_steps=a.max_steps_per_turn, system_prompt=system_prompt(),
        tools_fallback_prompt=TOOLS_FALLBACK, tool_result_format=TOOL_RESULT, pre_step_hook=EpisodeReset(sandbox),
        context_length=a.context_length, on_trajectory_end=trajectory_writer(a.out / "trajectories.jsonl"))
    reward = make_harel_settlement_reward(spec=SPEC)

    ds = Dataset.from_dict(training_rows(tasks, repeat=max(1, a.max_steps * a.batch_size // a.num_generations // len(tasks) + 1)))
    ds = ds.shuffle(seed=a.seed)
    cuda = torch.cuda.is_available()
    t0 = time.time()
    trainer = create_agentic_trainer(
        algorithm="grpo", model=a.model, train_dataset=ds, tools=list(make_tools(sandbox).values()),
        rollout_func=rollout_fn, reward_funcs=[reward], output_dir=str(a.out), max_steps=a.max_steps,
        per_device_train_batch_size=a.batch_size, gradient_accumulation_steps=a.grad_accum,
        num_generations=a.num_generations, max_completion_length=a.max_completion_length,
        learning_rate=a.lr, temperature=a.temperature, use_vllm=False, report_to="none", logging_steps=1,
        bf16=cuda, gradient_checkpointing=cuda, seed=a.seed, save_strategy="no",
        peft_config={"r": 16, "lora_alpha": 32, "target_modules": "all-linear", "task_type": "CAUSAL_LM"},
    )
    print(f"[t3] trainer built in {time.time() - t0:.0f}s; training {a.max_steps} steps", flush=True)
    t1 = time.time()
    result = trainer.train()
    wall = time.time() - t1
    inner = getattr(trainer, "trainer", trainer)
    history = [h for h in getattr(getattr(inner, "state", None), "log_history", []) if "loss" in h or "reward" in h]
    summary = {"model": a.model, "steps": a.max_steps, "train_seconds": round(wall, 1),
               "seconds_per_step": round(wall / max(a.max_steps, 1), 1), "gpu": torch.cuda.get_device_name(0) if cuda else "none",
               "history": history, "result": str(result)[:500]}
    (a.out / "t3_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    rewards = [h.get("reward") for h in history if h.get("reward") is not None]
    print(f"[t3] DONE {a.max_steps} steps in {wall:.0f}s ({summary['seconds_per_step']} s/step); reward by step: {rewards}", flush=True)
    try:
        inner.save_model(str(a.out / "adapter"))
    except Exception as e:  # noqa: BLE001
        print(f"[t3] could not save the adapter: {e}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
