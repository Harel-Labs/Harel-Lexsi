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

import ast
import functools
import hashlib
import importlib.util
import json
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

from .module_merge import NotPython, defined_names, merge_module

SHOP = Path(__file__).resolve().parents[3] / "shop"
TASKS = Path(__file__).resolve().parents[3] / "tasks"
DENIED = ("holdout", "reference", "spec")
OUTPUT_TAIL = 2000
FAILURE_LINE = 220   # characters of one failing-test line shown to the agent
MAX_FAILURES = 6


class SandboxError(Exception):
    """A tool call was refused. The message is what the agent sees."""


def task_for(prompt: str, tasks_dir: Path = TASKS) -> dict | None:
    """The task record whose prompt is `prompt` (the rollout only carries the prompt), else None."""
    path = tasks_dir / "tasks.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        if line.strip() and (t := json.loads(line))["prompt"] == prompt:
            return t
    return None


def patch_start_for(prompt: str, tasks_dir: Path = TASKS) -> tuple[str, str] | None:
    """(target path, start-file text) when `prompt` is a patch task's prompt, else None.

    Implement tasks need nothing: the stub is already in the workspace. The start file's SHA-256 is
    checked against the task record, so an edited start file cannot slip in unnoticed.
    """
    t = task_for(prompt, tasks_dir)
    if t is None or not t.get("start"):
        return None
    text = (tasks_dir / t["start"]).read_text()
    if hashlib.sha256(text.encode()).hexdigest() != t["start_sha256"]:
        raise SandboxError(f"start file for {t['id']} does not match its recorded hash")
    return t["target"], text


VISIBLE_TESTS = "app/tests/visible/test_visible.py"
COMPOSITE = "_task_handlers"  # workspace module: the reference handlers with the task's handler swapped in
_COMPOSITE_SOURCE = '''"""Handlers for the visible tests of one task: everything from the reference, except the handler
under test, which comes from the agent's app/handlers. Lives outside app/, so the agent cannot read it."""
import importlib

import reference.handlers as _ref

for _name in _ref.__all__:
    globals()[_name] = getattr(_ref, _name)
{handler} = getattr(importlib.import_module("app.handlers"), "{handler}")
'''


_TEST_HEADER = re.compile(r"^_{3,} (test_\w+)(?:\[.*\])? _{3,}$")
_AGENT_FRAME = re.compile(r"^app/handlers/\w+\.py:(\d+): in \w+$")


def _agent_lines(pytest_output: str) -> dict[str, tuple[int, str]]:
    """For each failing test in `--tb=short` output: (line number, source line) of the deepest frame
    inside the agent's own handler file. That is the line the agent has to change."""
    out: dict[str, tuple[int, str]] = {}
    test = None
    lines = pytest_output.splitlines()
    for i, line in enumerate(lines):
        if (m := _TEST_HEADER.match(line)):
            test = m.group(1)
        elif test and (m := _AGENT_FRAME.match(line)) and i + 1 < len(lines):
            out[test] = (int(m.group(1)), lines[i + 1].strip()[:120])
    return out


def visible_tests_for(handler: str, test_source: str) -> list[str]:
    """Names of the visible tests that call `h.<handler>`, in file order."""
    out = []
    for node in ast.parse(test_source).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_") and any(
                isinstance(n, ast.Attribute) and n.attr == handler
                and isinstance(n.value, ast.Name) and n.value.id == "h" for n in ast.walk(node)):
            out.append(node.name)
    return out


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
        self.handler: str | None = None      # the task's handler: focuses the visible tests on it
        self.last_tests: dict | None = None  # result of the visible tests after the latest write
        self.last_write: str | None = None   # content of the latest write, to notice a repeat
        self.writes = 0                      # accepted writes this episode
        self.refusals = 0                    # writes refused because the text was not Python
        self.last_refusal: tuple[str, str] | None = None  # (why, the text)

    # ----- lifecycle -----
    def reset(self, start: tuple[str, str] | None = None, handler: str | None = None) -> None:
        """Begin an episode. `start` = (target, text): a patch task's file, placed over the stub.
        `handler` = the handler the task is about: the visible tests then run only the tests that call
        it, with every other handler taken from the reference, so a correct handler can pass them."""
        self.close()
        self.handler, self.last_tests, self.last_write, self.writes = handler, None, None, 0
        self.refusals, self.last_refusal = 0, None
        self.workspace = Path(tempfile.mkdtemp(prefix="shop-episode-")).resolve()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(self.shop / "app", self.workspace / "app", ignore=ignore)
        shutil.copytree(self.shop / "tools", self.workspace / "tools", ignore=ignore)
        shutil.copytree(self.shop / "db", self.workspace / "db")
        if start is not None:
            target, text = start
            self._resolve(target, write=True).write_text(text)
        if handler:
            (self.workspace / f"{COMPOSITE}.py").write_text(_COMPOSITE_SOURCE.format(handler=handler))
        self._conn = self._db.connect()
        self._db.migrate(self._conn)  # schema + seed from scratch: clean state every episode
        self.episode_id = uuid.uuid4().hex[:12]
        self.step = 0

    @property
    def green(self) -> bool:
        """The agent has written its handler and every visible test that calls it passes."""
        return bool(self.handler and self.writes and self.last_tests and not self.last_tests["failed"]
                    and self.last_tests["passed"])

    def target_path(self) -> str | None:
        """The handler module (app/handlers/<name>.py) that defines the task's handler, if known."""
        if self.workspace is None or not self.handler:
            return None
        for f in sorted((self.workspace / "app" / "handlers").glob("*.py")):
            if re.search(rf"^def {re.escape(self.handler)}\(", f.read_text(), flags=re.MULTILINE):
                return f"app/handlers/{f.name}"
        return None

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
        """Merge `content` into the module: its definitions replace same-named ones, the rest stays.
        When the task's handler is known, the visible tests for it run right away and are reported."""
        target = self._resolve(path, write=True)
        existing = target.read_text() if target.is_file() else ""
        try:
            merged = merge_module(existing, content)
        except NotPython as e:
            self.refusals += 1
            self.last_refusal = (str(e), content)
            raise SandboxError(f"{e}. Nothing was written.") from None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(merged)
        result = {"path": path, "bytes": len(merged.encode()), "defined": defined_names(content)}
        if self.handler:
            t = self.run_visible_tests()
            result["tests"] = {k: t[k] for k in ("passed", "failed", "failures")}
            if t["failed"] and content == self.last_write:
                result["note"] = ("This is exactly the code you wrote last time, so the same tests fail. "
                                  "Change the line named in the failure.")
            elif not t["failed"]:
                result["note"] = "All visible tests for your handler pass. Call finish."
        self.last_write = content
        self.writes += 1
        return result

    def run_visible_tests(self) -> dict:
        assert self.workspace is not None
        env = {**os.environ, "PYTHONPATH": str(self.workspace), "COLUMNS": "200"}
        env.pop("SHOP_HANDLERS", None)
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE"]
        focused = []
        if self.handler:
            focused = visible_tests_for(self.handler, (self.workspace / VISIBLE_TESTS).read_text())
        if focused:
            cmd += [f"{VISIBLE_TESTS}::{name}" for name in focused]
            env["SHOP_HANDLERS"] = COMPOSITE
            env["PYTHONPATH"] = os.pathsep.join([str(self.workspace), str(self.shop)])  # shop/: `reference`
        else:
            cmd.append("app/tests/visible")
        try:
            r = subprocess.run(cmd, cwd=self.workspace, env=env, capture_output=True, text=True,
                               timeout=120, check=False)
        except subprocess.TimeoutExpired:
            self.last_tests = {"passed": 0, "failed": -1, "failures": ["timed out after 120s"],
                               "output_tail": "timed out after 120s"}
            return self.last_tests
        out = (r.stdout + r.stderr).replace(str(self.workspace) + os.sep, "")
        count = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}", out)) else 0
        where = _agent_lines(out)
        failures, seen = [], set()
        for line in out.splitlines():
            if not line.startswith(("FAILED ", "ERROR ")):
                continue
            text = re.sub(rf"^(FAILED|ERROR) {re.escape(VISIBLE_TESTS)}::", "", line)
            test, _, reason = text.partition(" - ")
            if reason in seen:
                continue  # six lines should carry six different facts
            seen.add(reason)
            if (loc := where.get(test.split("[")[0])) is not None:
                text += f" (your code, line {loc[0]}: `{loc[1]}`)"
            failures.append(text[:FAILURE_LINE])
        self.last_tests = {"passed": count("passed"), "failed": count("failed") + count("error"),
                           "failures": failures[:MAX_FAILURES], "output_tail": out[-OUTPUT_TAIL:]}
        return self.last_tests

    def finish(self, answer: str = "") -> str:
        """What `finish` answers. It never locks the episode (so a replay of the writes needs no test
        results); it tells the agent whether the visible tests for its handler pass."""
        if not self.handler:
            return answer
        if self.last_tests is None:
            return (f"Not done yet: you have not written `{self.handler}`. "
                    "Call write_file with the function, on its own.")
        if self.last_tests["failed"]:
            lines = "; ".join(self.last_tests["failures"]) or "see run_visible_tests"
            return (f"Not done yet: {self.last_tests['failed']} visible test(s) for `{self.handler}` still "
                    f"fail: {lines}. Fix the function and call write_file again.")
        return "Done: the visible tests for your handler pass. Reply with one short sentence and no tool call."


def make_tools(sandbox: ShopSandbox) -> dict[str, Callable[..., Any]]:
    """The four tools as plain functions (typed, documented), each wrapped with step markers."""

    def marked(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)  # keep the real signature: the model's tool schema comes from it
        def run(*args, **kwargs):
            sandbox.next_step()
            sandbox.mark(name, "start")
            try:
                return fn(*args, **kwargs)
            finally:
                sandbox.mark(name, "end")
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
        """Write handler code. Only app/handlers/*.py can be written.

        Send just the function(s) you are implementing, plus any imports they need. They replace the
        functions with the same name in the module; every other function already there is kept.

        Args:
            path: Path relative to the project root, e.g. app/handlers/cart.py.
            content: Python source with the function(s) to write.

        Returns:
            The path, the size of the module after the merge, the names you defined, and the result of
            the visible tests for your handler (how many pass and fail, and why the failing ones fail).
        """
        return sandbox.write_file(path, content)

    def run_visible_tests() -> dict:
        """Run the visible tests for your handler again (write_file already runs them).

        Returns:
            Counts of passed and failed tests and why the failing ones fail.
        """
        t = sandbox.run_visible_tests()
        return {k: t[k] for k in ("passed", "failed", "failures")}

    def finish(answer: str = "") -> str:
        """Declare the task complete. Call it only after write_file reports that the tests pass.

        Args:
            answer: A short note on what you did.

        Returns:
            Whether the task is done, or which visible tests still fail.
        """
        return sandbox.finish(answer)

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
        record = task_for(task)
        self.sandbox.reset(start=patch_start_for(task), handler=record["handler"] if record else None)
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
