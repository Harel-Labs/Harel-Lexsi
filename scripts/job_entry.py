"""Runs INSIDE a Hugging Face Job (see scripts/hf_job.py). Not meant to be run on a laptop.

`run <script> [args]` starts any script of the repo; the modes below are the T3 ones.

The launcher mounts the staged code at /code and starts this file with `hf jobs uv run`-style dependencies
(torch, trl, peft, ...). It then
  1. installs AgentTune (and AuditKIT, for the test suite) from the mounted source, editable;
  2. starts Postgres and runs one of:
       tests   the repo's test suite (needs Postgres, like the shop tests do)
       check   scripts/train_t3.py --check: a reference handler written through the harness must verify as PASS
       train   scripts/train_t3.py: the GRPO run

    job_entry.py tests
    job_entry.py check
    job_entry.py train --max-steps 10 --num-generations 4 ...
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MOUNT = Path(os.environ.get("CODE_DIR", "/code"))  # the staged code: a read-only volume
CODE = Path("/work")                                # a writable copy: editable installs write into it


def sh(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def install_libs(mode: str = "tests") -> None:
    shutil.rmtree(CODE, ignore_errors=True)
    shutil.copytree(MOUNT, CODE)
    py = sys.executable
    installer = [shutil.which("uv"), "pip", "install", "--python", py] if shutil.which("uv") else [py, "-m", "pip", "install"]
    for lib in ("AgentTune", "AuditKIT") if mode == "tests" else ("AgentTune",):  # AuditKIT only for the test suite
        if (CODE / lib).is_dir():
            sh(*installer, "-e", str(CODE / lib))
    # Installed, not just on PYTHONPATH: the verifier's scorer runs in a subprocess with a clean environment and
    # must import harel_lexsi from site-packages. Without this every reward is UNKNOWN (0.0).
    sh(*installer, "--no-deps", "-e", str(CODE / "Harel-Lexsi"))
    os.environ["PYTHONPATH"] = os.pathsep.join([str(CODE / "Harel-Lexsi" / "src"), os.environ.get("PYTHONPATH", "")])


def main() -> int:
    mode, rest = (sys.argv[1] if len(sys.argv) > 1 else "check"), sys.argv[2:]
    repo = CODE / "Harel-Lexsi"
    print(f"[job] mode={mode} mount={MOUNT} python={sys.version.split()[0]}", flush=True)
    install_libs(mode)
    os.chdir(repo)
    sys.path.insert(0, str(repo / "scripts"))
    sys.path.insert(0, str(repo / "src"))
    try:
        import torch
        print(f"[job] torch {torch.__version__} cuda={torch.cuda.is_available()} "
              f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[job] torch not importable: {e}", flush=True)
    if mode == "tests":
        import train_t3
        train_t3.start_postgres()
        return subprocess.call([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], cwd=repo)
    # A fresh interpreter: libraries installed a moment ago are not importable in this already-running process.
    if mode == "run":  # any script of the repo: job_entry.py run scripts/t8_collect.py --pilot
        script, rest = rest[0].removesuffix(".py") + ".py", rest[1:]  # sent without ".py": see hf_job.py
        if not (repo / script).is_file():
            print(f"[job] no such script in the repo: {script}", flush=True)
            return 2
        return subprocess.call([sys.executable, str(repo / script), *rest], cwd=repo)
    cmd = [sys.executable, str(repo / "scripts" / "train_t3.py"), "--postgres", *(["--check"] if mode == "check" else []), *rest]
    return subprocess.call(cmd, cwd=repo)


if __name__ == "__main__":
    raise SystemExit(main())
