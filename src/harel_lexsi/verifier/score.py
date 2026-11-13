"""Score agent-written handler files against the contracts, in a subprocess.

    score_files({"app/handlers/cart.py": "..."}, "add_to_cart")  ->  [Settlement, ...]

The agent's code is untrusted, so it never runs in the caller's process: the files go into a fresh
copy of shop/app/ and a child process replays the handler's contracts against them with a timeout.
Code that does not even import (syntax error, missing name, bad import) is the agent's failure: FAIL
with class `does_not_load`. Anything else that stops the child from producing a verdict (timeout,
crash, no contracts) is UNKNOWN, which never counts as a pass. Model-free. The child shares the one shop database, so score one at a time.

Child entry point: python -m harel_lexsi.verifier.score --workspace DIR --handler NAME [--spec FILE]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .contracts import load_binding, load_holdout
from .replay import ROOT, SHOP, replay
from .settle import settle
from .types import Settlement, Verdict, Violation

DEFAULT_TIMEOUT = 120
DEFAULT_BINDING = ROOT / "spec" / "binding.yaml"
DEFAULT_CONTRACTS = (ROOT / "tests" / "fixtures" / "shop_contracts.yaml",
                     ROOT / "tests" / "fixtures" / "mutant_contracts.yaml")
_ALLOWED = re.compile(r"app/handlers/[A-Za-z0-9_]+\.py")


def allowed_path(path: str) -> bool:
    """The only files an agent may write: app/handlers/<name>.py (same rule as the harness)."""
    return bool(_ALLOWED.fullmatch(path)) and ".." not in Path(path).parts


def _unknown(reason: str) -> list[Settlement]:
    return [Settlement("scorer", Verdict.UNKNOWN, reason)]


def _to_json(s: Settlement) -> dict:
    return {"contract_id": s.contract_id, "verdict": s.verdict.value, "reason": s.reason,
            "violations": [vars(v) for v in s.violations]}


def _from_json(d: dict) -> Settlement:
    return Settlement(d["contract_id"], Verdict(d["verdict"]), d["reason"],
                      tuple(Violation(**v) for v in d["violations"]))


def score_files(
    files: Mapping[str, str],
    handler: str,
    *,
    spec: Path | None = None,
    contracts: Sequence[Path] = (),
    timeout: int = DEFAULT_TIMEOUT,
) -> list[Settlement]:
    """Settle `handler`'s contracts against the final contents of the agent's handler files."""
    workspace = Path(tempfile.mkdtemp(prefix="shop-score-")).resolve()
    try:
        shutil.copytree(SHOP / "app", workspace / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for path, content in files.items():
            if not allowed_path(path):
                continue  # the harness would have refused this write too
            (workspace / path).write_text(content)
        cmd = [sys.executable, "-m", "harel_lexsi.verifier.score", "--workspace", str(workspace),
               "--handler", handler]
        if spec:
            cmd += ["--spec", str(spec)]
        for c in contracts:
            cmd += ["--contracts", str(c)]
        env = {"PYTHONPATH": str(workspace), "PATH": "/usr/bin:/bin", "HOME": str(workspace)}
        env.update({k: v for k, v in os.environ.items() if k.startswith(("SHOP_", "VIRTUAL_ENV"))})
        try:
            r = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True,
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return _unknown(f"scoring timed out after {timeout}s")
        if r.returncode != 0:
            return _unknown(f"scorer exited {r.returncode}: {r.stderr.strip()[-200:]}")
        try:
            return [_from_json(d) for d in json.loads(r.stdout.splitlines()[-1])]
        except (ValueError, KeyError, IndexError):
            return _unknown("scorer produced no verdicts")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harel_lexsi.verifier.score")
    p.add_argument("--workspace", required=True)
    p.add_argument("--handler", required=True)
    p.add_argument("--spec")
    p.add_argument("--contracts", action="append", default=[])
    a = p.parse_args(argv)

    binding = load_binding(DEFAULT_BINDING)
    pool = []
    if a.spec:
        from .gate import load_spec_contracts
        pool += load_spec_contracts(Path(a.spec), binding)
    for path in [*a.contracts, *([] if a.spec or a.contracts else DEFAULT_CONTRACTS)]:
        pool += load_holdout(Path(path), binding)
    mine = [c for c in pool if c.handler == a.handler]
    if not mine:
        print(json.dumps([_to_json(Settlement("scorer", Verdict.UNKNOWN, "no contracts for this handler"))]))
        return 0
    sys.path.insert(0, a.workspace)
    try:
        handlers = importlib.import_module("app.handlers")
    except Exception as e:  # noqa: BLE001  the agent's code does not load: that is its failure, not UNKNOWN
        detail = f"{type(e).__name__}: {e}"[:300]  # internal; failure lines never show it
        fail = tuple(Settlement(c.id, Verdict.FAIL, "handler module does not load",
                                (Violation("load_error", 0, detail=detail),)) for c in mine)
        print(json.dumps([_to_json(s) for s in fail]))
        return 0
    out = []
    for c in mine:
        s = replay(c, handlers)
        out.append(_to_json(settle(c, s.capture, s.error, binding)))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
