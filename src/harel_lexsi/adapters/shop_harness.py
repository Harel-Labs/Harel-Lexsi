"""AgentTune harness for the shop (S6): the tools L1 gets and the sandbox they run in.

Lives in adapters/ because it imports AgentTune (noncommercial). The verifier never imports this.

Rules enforced here:
- The workspace is a per-episode copy of shop/app/ plus shop/tools/ and shop/db/. The reference
  handlers and the spec are never copied, and path checks refuse them by name as well.
- Reads are allowed under app/; writes only to app/handlers/*.py.
- Every tool call emits a `harel.step` message into the WAL (start and end) for V1 to capture.
- Episodes share one database and reset it on start, so run them one at a time.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agenttune.agentic import DictToolHarness
from agenttune.agentic.harness import Observation

from harel_lexsi.verifier.capture import marker_payload

SHOP = Path(__file__).resolve().parents[3] / "shop"
DENIED = ("holdout", "reference", "spec")
OUTPUT_TAIL = 2000


class SandboxError(Exception):
    """A tool call was refused. The message is what the agent sees."""


def _load_shop_db():
    spec = importlib.util.spec_from_file_location("shop_tools_db", SHOP / "tools" / "db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ShopSandbox:
    """One episode's workspace and database. Call reset() to start (or restart) an episode."""

    def __init__(self, shop: Path = SHOP, factory_version: str | None = None):
        self.shop = shop
        self.factory_version = factory_version  # G1: stamped on every step marker
        self._db = _load_shop_db()
        self.workspace: Path | None = None
        self.episode_id = ""
        self.step = 0
        self._conn = None

    # ----- lifecycle -----
    def reset(self) -> None:
        self.close()
        self.workspace = Path(tempfile.mkdtemp(prefix="shop-episode-")).resolve()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(self.shop / "app", self.workspace / "app", ignore=ignore)
        shutil.copytree(self.shop / "tools", self.workspace / "tools", ignore=ignore)
        shutil.copytree(self.shop / "db", self.workspace / "db")
        self._conn = self._db.connect()
        self._db.migrate(self._conn)  # schema + seed from scratch: clean state every episode
        self.episode_id = uuid.uuid4().hex[:12]
        self.step = 0

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self.workspace is not None:
            shutil.rmtree(self.workspace, ignore_errors=True)
            self.workspace = None

    # ----- step markers for V1 -----
    def mark(self, tool: str, phase: str) -> None:
        if self._conn is None:
            return
        payload = marker_payload(self.episode_id, self.step, tool, phase, self.factory_version)
        self._conn.execute("SELECT pg_logical_emit_message(true, 'harel.step', %s)", (payload,))

    def next_step(self) -> None:
        self.step += 1

    # ----- path confinement -----
    def _resolve(self, rel: str, *, write: bool) -> Path:
        if self.workspace is None:
            raise SandboxError("no episode started")
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            raise SandboxError("path must be relative and stay inside the project")
        if any(d in part.lower() for part in p.parts for d in DENIED):
            raise SandboxError("path not allowed")
        target = self.workspace / p
        cur = self.workspace
        for part in p.parts:
            cur = cur / part
            if cur.is_symlink():
                raise SandboxError("symlinks are not allowed")
        if not target.resolve().is_relative_to(self.workspace):
            raise SandboxError("path must stay inside the project")
        parts = p.parts
        if write:
            if not (len(parts) == 3 and parts[:2] == ("app", "handlers") and parts[2].endswith(".py")):
                raise SandboxError("you may only write app/handlers/*.py")
        elif not parts or parts[0] != "app":
            raise SandboxError("you may only read files under app/")
        return target

    # ----- tool bodies -----
    def read_file(self, path: str) -> str:
        target = self._resolve(path, write=False)
        if not target.is_file():
            raise SandboxError(f"no such file: {path}")
        return target.read_text()

    def write_file(self, path: str, content: str) -> dict:
        target = self._resolve(path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"path": path, "bytes": len(content.encode())}

    def run_visible_tests(self) -> dict:
        assert self.workspace is not None
        env = {**os.environ, "PYTHONPATH": str(self.workspace)}
        env.pop("SHOP_HANDLERS", None)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "app/tests/visible", "-q", "-p", "no:cacheprovider"],
                cwd=self.workspace, env=env, capture_output=True, text=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"passed": 0, "failed": -1, "output_tail": "timed out after 120s"}
        out = r.stdout + r.stderr
        count = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}", out)) else 0
        return {"passed": count("passed"), "failed": count("failed") + count("error"),
                "output_tail": out[-OUTPUT_TAIL:]}


def make_tools(sandbox: ShopSandbox) -> dict[str, Callable[..., Any]]:
    """The four tools as plain functions (typed, documented), each wrapped with step markers."""

    def marked(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def run(*args, **kwargs):
            sandbox.next_step()
            sandbox.mark(name, "start")
            try:
                return fn(*args, **kwargs)
            finally:
                sandbox.mark(name, "end")
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        run.__annotations__ = fn.__annotations__
        return run

    def read_file(path: str) -> str:
        """Read a file under app/ in the project and return its text.

        Args:
            path: Path relative to the project root, e.g. app/handlers/cart.py.

        Returns:
            The file contents.
        """
        return sandbox.read_file(path)

    def write_file(path: str, content: str) -> dict:
        """Write a handler file. Only app/handlers/*.py can be written.

        Args:
            path: Path relative to the project root, e.g. app/handlers/cart.py.
            content: The full new file contents.

        Returns:
            The path and the number of bytes written.
        """
        return sandbox.write_file(path, content)

    def run_visible_tests() -> dict:
        """Run the visible tests against your handlers.

        Returns:
            Counts of passed and failed tests and the tail of the pytest output.
        """
        return sandbox.run_visible_tests()

    def finish(answer: str = "") -> str:
        """Declare the task complete.

        Args:
            answer: A short note on what you did.

        Returns:
            The same note.
        """
        return answer

    return {n: marked(n, f) for n, f in
            [("read_file", read_file), ("write_file", write_file),
             ("run_visible_tests", run_visible_tests), ("finish", finish)]}


class ShopHarness(DictToolHarness):
    """DictToolHarness with a sandbox that resets per episode and a marker on finish.

    DictToolHarness handles the done tool itself without calling it, so the finish marker is
    emitted here; the other tools mark themselves (see make_tools).
    """

    def __init__(self, sandbox: ShopSandbox | None = None, *, max_steps: int = 20,
                 factory_version: str | None = None):
        self.sandbox = sandbox or ShopSandbox(factory_version=factory_version)
        super().__init__(make_tools(self.sandbox), max_steps=max_steps, done_tool="finish")

    def reset(self, task: str) -> Observation:
        self.sandbox.reset()
        return super().reset(task)

    def step(self, action: dict):
        if action.get("name") == self.done_tool:
            self.sandbox.next_step()
            self.sandbox.mark("finish", "start")
            result = super().step(action)
            self.sandbox.mark("finish", "end")
            return result
        return super().step(action)

    def close(self) -> None:
        self.sandbox.close()


def make_shop_harness(*, max_steps: int = 20, factory_version: str | None = None) -> ShopHarness:
    return ShopHarness(max_steps=max_steps, factory_version=factory_version)
