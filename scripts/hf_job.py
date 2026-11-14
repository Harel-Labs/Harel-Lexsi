"""Submit work to Hugging Face Jobs. This only uploads files and submits; the compute runs on HF.

    uv run python scripts/hf_job.py tests                         # the test suite with Postgres, CPU (cents)
    uv run python scripts/hf_job.py check                         # Postgres + verifier check, CPU (cents)
    uv run python scripts/hf_job.py run scripts/t8_collect.py -- --pilot          # any repo script, CPU
    uv run python scripts/hf_job.py run scripts/train_t8.py --gpu --yes -- ...    # on the GPU flavor
    uv run python scripts/hf_job.py train --yes -- --max-steps 3                  # the T3 GRPO run

Needs `HF_JOB_TOKEN` in .env (a token with `job.write`) and a positive credit balance in its namespace.
`HF_TOKEN` (read-only is enough) is passed as a secret so the job can download the gated Tiny Aya weights;
extra secrets are passed by name with --secret (for example HF_INF_TOKEN for hosted inference). Nothing from
.env is uploaded: the staged copy leaves out .env, .git, .venv and runs/.

Every job gets the persistent folder `/out` (bucket `<user>/jobs-artifacts`, subfolder `harel-out`, read-write),
so one job's outputs (samples, pairs, an adapter) are another job's inputs. GPU jobs cost real money, so they
are not submitted without --yes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBS = {"AgentTune": ROOT.parent / "AgentTune", "AuditKIT": ROOT.parent / "AuditKIT"}
SKIP = shutil.ignore_patterns(".git", ".venv", "runs", "__pycache__", "*.pyc", ".env", ".DS_Store", ".pytest_cache",
                              ".ruff_cache", "*.egg-info", "node_modules", "*.safetensors", "*.bin")

# The versions the baseline and the tests ran with, so a job behaves like the development machine did.
DEPENDENCIES = ["torch==2.11.0", "transformers==5.22.2", "trl==1.7.1", "peft==0.21.2", "accelerate==1.15.0",
                "datasets==5.2.0", "psycopg[binary]==3.3.8", "pyyaml", "pytest", "openai", "litellm==1.109.0"]
CPU, GPU = "cpu-upgrade", "a100-large"
OUT_FOLDER = "harel-out"


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["tests", "check", "train", "run"])
    p.add_argument("script", nargs="?", help="for `run`: a script path inside the repo, e.g. scripts/t8_collect.py")
    p.add_argument("--gpu", action="store_true", help=f"use the GPU flavor ({GPU}); needs --yes")
    p.add_argument("--flavor", default=None, help="override the flavor")
    p.add_argument("--namespace", default=None, help="whose credits and Jobs list; default: the token's own user")
    p.add_argument("--timeout", default=None)
    p.add_argument("--secret", action="append", default=[], help="name of an env var to pass as a job secret")
    p.add_argument("--yes", action="store_true", help="confirm a job on a GPU flavor")
    p.add_argument("--dry-run", action="store_true", help="stage and print what would be submitted, upload nothing")
    p.add_argument("job_args", nargs="*", help="after --: arguments for the script")
    a = p.parse_args(argv)
    load_dotenv()
    if a.mode == "run" and not a.script:
        p.error("`run` needs a script path")
    on_gpu = a.gpu or a.mode == "train" or (a.flavor is not None and not a.flavor.startswith("cpu"))
    flavor = a.flavor or (GPU if on_gpu else CPU)
    timeout = a.timeout or ("2h" if on_gpu else "1h")
    if on_gpu and not a.yes and not a.dry_run:
        print(f"This submits a job on `{flavor}` (up to {timeout}), which costs money. Re-run with --yes.")
        return 2
    # HF's uv runner uploads any argument that is a real local file and rewrites it to /data/<name> (the script then runs
    # outside the repo: wrong ROOT, no sibling scripts), and rejects a *.py argument that is not a local file. So the
    # script goes without its ".py"; job_entry adds it back.
    entry_args = [a.mode, *([a.script.removesuffix(".py")] if a.mode == "run" else []), *a.job_args]

    with tempfile.TemporaryDirectory(prefix="hf-job-") as tmp:
        code, marker = Path(tmp) / "code", Path(tmp) / "out"
        code.mkdir()
        marker.mkdir()
        (marker / "README.txt").write_text("Outputs of Harel-Lexsi jobs. Written by scripts/hf_job.py.\n")
        stage(code)
        size = sum(f.stat().st_size for f in code.rglob("*") if f.is_file()) / 1e6
        print(f"staged {size:.1f} MB: {sorted(x.name for x in code.iterdir())}; job: {' '.join(entry_args)} on {flavor}")
        if a.dry_run:
            return 0
        from huggingface_hub import HfApi

        token = os.environ.get("HF_JOB_TOKEN") or os.environ.get("HF_JOBS_TOKEN")
        if not token:
            print("HF_JOB_TOKEN is not set. Add a token that may run Jobs to .env (see this file's docstring).")
            return 1
        api = HfApi(token=token)
        namespace = a.namespace or api.whoami()["name"]
        missing = [s for s in a.secret if not os.environ.get(s)]
        if missing:
            print(f"secret(s) not set in the environment or .env: {missing}")
            return 1
        import subprocess
        git = lambda *c: subprocess.run(["git", *c], cwd=ROOT, capture_output=True, text=True).stdout.strip()  # noqa: E731
        sha = git("rev-parse", "HEAD") + ("+uncommitted" if git("status", "--porcelain", "--untracked-files=no") else "")
        volumes = [api.sync_job_volume(str(code), "/code"),
                   api.sync_job_volume(str(marker), "/out", remote_name=OUT_FOLDER, read_only=False)]  # never deletes
        job = api.run_uv_job(
            str(ROOT / "scripts" / "job_entry.py"), script_args=entry_args, dependencies=DEPENDENCIES,
            flavor=flavor, timeout=timeout, namespace=namespace, volumes=volumes,
            secrets={"HF_TOKEN": os.environ["HF_TOKEN"], **{s: os.environ[s] for s in a.secret}},
            env={"CODE_DIR": "/code", "OUT_DIR": "/out", "PYTHONUNBUFFERED": "1", "GIT_SHA": sha,
                 "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"},
            labels={"project": "harel-lexsi", "step": (a.script or a.mode).replace("/", "-").replace(".py", "")[:40]})
    print(f"submitted: {job.url}\nid: {job.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
