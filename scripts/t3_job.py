"""Submit a T3 job to Hugging Face Jobs. This only uploads files and submits; the compute runs on HF.

    uv run python scripts/t3_job.py tests            # the test suite, with Postgres, on a CPU flavor (cents)
    uv run python scripts/t3_job.py check            # Postgres + verifier check on a CPU flavor (cents)
    uv run python scripts/t3_job.py train --yes      # the GRPO run on one L40S ($1.80/h)

Needs `HF_JOBS_TOKEN` in .env: a token that may run Jobs for the user and the namespace (a fine-grained token
with the Jobs permission, or a write token), and a positive credit balance in that namespace. `HF_TOKEN`
(read-only is enough) is passed to the job as a secret so it can download the gated Tiny Aya weights. Nothing
from .env is uploaded: the staged copy leaves out .env, .git, .venv and runs/.

`train` costs money, so it will not submit without --yes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBS = {"AgentTune": Path("/Users/deadbytes/Documents/AgentTune"), "AuditKIT": Path("/Users/deadbytes/Documents/AuditKIT")}
SKIP = shutil.ignore_patterns(".git", ".venv", "runs", "__pycache__", "*.pyc", ".env", ".DS_Store", ".pytest_cache",
                              ".ruff_cache", "*.egg-info", "node_modules", "*.safetensors", "*.bin")

# The versions the baseline and the tests ran with, so the job behaves like the Mac did.
DEPENDENCIES = ["torch==2.11.0", "transformers==5.22.2", "trl==1.7.1", "peft==0.21.2", "accelerate==1.15.0",
                "datasets==5.2.0", "psycopg[binary]==3.3.8", "pyyaml", "pytest", "openai", "litellm==1.109.0"]
FLAVORS = {"tests": "cpu-upgrade", "check": "cpu-upgrade", "train": "l40sx1"}
TIMEOUTS = {"tests": "30m", "check": "30m", "train": "90m"}


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def stage(dest: Path) -> None:
    shutil.copytree(ROOT, dest / "Harel-Lexsi", ignore=SKIP)
    for name, src in LIBS.items():
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=SKIP)
    assert not list(dest.rglob(".env")), "a .env slipped into the staged copy"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["tests", "check", "train"])
    p.add_argument("--flavor", default=None)
    p.add_argument("--namespace", default="Lexsi", help="whose credits and Jobs list: the org, or your user name")
    p.add_argument("--timeout", default=None)
    p.add_argument("--yes", action="store_true", help="confirm a job that costs money (train)")
    p.add_argument("--dry-run", action="store_true", help="stage and print what would be submitted, upload nothing")
    p.add_argument("train_args", nargs="*", help="after --: arguments for scripts/train_t3.py")
    a = p.parse_args()
    load_dotenv()
    flavor = a.flavor or FLAVORS[a.mode]
    timeout = a.timeout or TIMEOUTS[a.mode]
    if a.mode == "train" and not a.yes and not a.dry_run:
        print(f"This submits a training job on `{flavor}` (up to {timeout}) under `{a.namespace}`. Re-run with --yes.")
        return 2

    with tempfile.TemporaryDirectory(prefix="t3-stage-") as tmp:
        stage(Path(tmp))
        size = sum(f.stat().st_size for f in Path(tmp).rglob("*") if f.is_file()) / 1e6
        print(f"staged {size:.1f} MB: {sorted(x.name for x in Path(tmp).iterdir())}")
        if a.dry_run:
            print(f"would run `{a.mode}` on {flavor} (timeout {timeout}) in namespace {a.namespace}")
            return 0
        from huggingface_hub import HfApi

        token = os.environ.get("HF_JOBS_TOKEN")
        if not token:
            print("HF_JOBS_TOKEN is not set. Add a token that may run Jobs to .env (see this file's docstring).")
            return 1
        api = HfApi(token=token)
        volume = api.sync_job_volume(tmp, "/code")
        job = api.run_uv_job(
            str(ROOT / "scripts" / "job_entry.py"), script_args=[a.mode, *a.train_args],
            dependencies=DEPENDENCIES, flavor=flavor, timeout=timeout, namespace=a.namespace, volumes=[volume],
            secrets={"HF_TOKEN": os.environ["HF_TOKEN"]}, env={"CODE_DIR": "/code", "PYTHONUNBUFFERED": "1"},
            labels={"project": "harel-lexsi", "step": f"t3-{a.mode}"})
    print(f"submitted: {job.url}\nid: {job.id}\nlogs:   hf jobs logs {job.id} --namespace {a.namespace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
