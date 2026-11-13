"""Shape what a small builder sees each turn, through AgentTune's step hooks.

Two things a 3B model does not do on its own:

- It never reads the visible tests, so it does not know what its function must return. The first
  turn therefore carries the tests that call its handler, and the function as it stands (the stub with
  its docstring, or for a patch task the code it has to fix).
- After a failing write it copies its previous attempt out of the conversation history, round after
  round. So after every write the history is replaced by one compact repair request: the task, the
  tests, the last attempt, what failed and on which line. The context stays flat instead of growing.

Everything shown is builder-visible: the task prompt, app/tests/visible, and the agent's own code.

    coach = EpisodeCoach(sandbox)
    create_rollout_fn(..., pre_step_hook=coach.pre_step, post_step_hook=coach.post_step)
"""
from __future__ import annotations

import ast
from typing import Any

from .builder_prompt import schema_digest
from .shop_harness import VISIBLE_TESTS, ShopSandbox, visible_tests_for

MAX_TEST_CHARS = 1800
MAX_MISTAKES = 5
MAX_REFUSED_CHARS = 1500


def function_source(module_text: str, name: str) -> str | None:
    """Source of the top-level function `name` (decorators included), or None."""
    try:
        tree = ast.parse(module_text)
    except SyntaxError:
        return None
    lines = module_text.splitlines()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            return "\n".join(lines[first - 1:node.end_lineno])
    return None


def tests_text(handler: str, test_source: str, limit: int = MAX_TEST_CHARS) -> str:
    """The module docstring (seed data) and the tests that call `h.<handler>`, the ones named after the
    handler first, cut to `limit` characters."""
    tree = ast.parse(test_source)
    found = visible_tests_for(handler, test_source)
    names = [n for n in found if handler in n] + [n for n in found if handler not in n]
    by_name = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    lines = test_source.splitlines()
    parts = []
    if (doc := ast.get_docstring(tree)):
        parts.append('"""' + doc + '"""')
    used = sum(len(p) for p in parts)
    for name in names:
        node = by_name[name]
        first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
        src = "\n".join(lines[first - 1:node.end_lineno])
        if used + len(src) > limit and len(parts) > 1:
            break
        parts.append(src)
        used += len(src)
    return "\n\n".join(parts)


class EpisodeCoach:
    def __init__(self, sandbox: ShopSandbox):
        self.sandbox = sandbox
        self._task = ""
        self._tests = ""
        self._tables = ""
        self._seen_writes = 0
        self._seen_refusals = 0
        self._repeats = 0
        self._previous: str | None = None
        self._original: str | None = None    # the function before the agent touched it
        self._mistakes: list[str] = []       # distinct failure reasons seen this episode

    # ----- first turn -----
    def pre_step(self, iteration: int, conversation: list[dict], tools: Any = None) -> None:
        sb = self.sandbox
        if iteration != 0 or not sb.handler or not conversation or sb.workspace is None:
            return
        self._task = conversation[-1].get("content") or ""
        self._seen_writes, self._repeats, self._previous = sb.writes, 0, None
        self._seen_refusals = sb.refusals
        self._original, self._mistakes = None, []
        self._tests = tests_text(sb.handler, (sb.workspace / VISIBLE_TESTS).read_text())
        self._tables = ("The tables and their columns (use exactly these names):\n"
                        + schema_digest((sb.workspace / "app" / "schema.sql").read_text()))
        extra = [self._tables, (f"The visible tests that call `{sb.handler}` (`h` is the handlers module):\n"
                  f"```python\n{self._tests}\n```")]
        target = sb.target_path()
        current = function_source((sb.workspace / target).read_text(), sb.handler) if target else None
        self._original = current
        if current:
            extra.append(f"The function as it is now in `{target}`:\n```python\n{current}\n```")
        extra.append("Reply with the complete function in one ```python block.")
        conversation[-1] = {**conversation[-1], "content": self._task + "\n\n" + "\n\n".join(extra)}

    # ----- after every write: one compact repair request instead of a growing history -----
    def post_step(self, step: Any, conversation: list[dict] | None = None, **_: Any) -> list[dict] | None:
        sb = self.sandbox
        if conversation is None or not sb.handler:
            return None
        head = [m for m in conversation if m.get("role") == "system"][:1]
        if sb.refusals != self._seen_refusals and sb.writes == self._seen_writes and sb.last_refusal:
            # the reply was not valid Python, so nothing was saved: say where, and ask again
            self._seen_refusals = sb.refusals
            why, text = sb.last_refusal
            parts = [self._task, self._tables,
                     f"The visible tests that call `{sb.handler}`:\n```python\n{self._tests}\n```",
                     f"Your last reply could not be saved: {why}.\n```python\n{text.rstrip()[:MAX_REFUSED_CHARS]}\n```",
                     ("Reply with the complete function as valid Python in one ```python block, and nothing "
                      "else. Keep it short. Use double quotes for SQL strings and pass values as parameters.")]
            return [*head, {"role": "user", "content": "\n\n".join(parts)}]
        if sb.writes == self._seen_writes or sb.last_tests is None:
            return None
        self._seen_writes, self._seen_refusals = sb.writes, sb.refusals
        code = (sb.last_write or "").rstrip()
        self._repeats = self._repeats + 1 if code == self._previous else 0
        self._previous = code
        t = sb.last_tests
        total = t["passed"] + t["failed"]
        if not t["failed"]:
            body = (f"{self._task}\n\nYour function is saved and all {total} visible tests that call it pass. "
                    "Reply with the single word: done")
            return [*head, {"role": "user", "content": body}]
        for f in t["failures"]:
            reason = f.partition(" - ")[2] or f
            if reason not in self._mistakes:
                self._mistakes.append(reason)
        failures = "\n".join(f"- {f}" for f in t["failures"]) or "- (see run_visible_tests)"
        parts = [self._task, self._tables,
                 f"The visible tests that call `{sb.handler}`:\n```python\n{self._tests}\n```"]
        if self._repeats:
            # it is copying its own attempt: take the attempt away and ask for a fresh one
            mistakes = "\n".join(f"- {m}" for m in self._mistakes[-MAX_MISTAKES:])
            if self._original:
                parts.append(f"Start again from the function as it was:\n```python\n{self._original}\n```")
            parts.append(f"Your attempts so far failed the tests. Mistakes you made (do not repeat them):\n{mistakes}")
            parts.append("Write the function again in a different way. Reply with the complete function in "
                         "one ```python block, and nothing else.")
        else:
            parts.append(f"Your last attempt:\n```python\n{code}\n```")
            parts.append(f"It is saved, but {t['failed']} of {total} of those tests fail:\n{failures}")
            parts.append("The tests are right. Reply with the corrected, complete function in one ```python "
                         "block, and nothing else.")
        return [*head, {"role": "user", "content": "\n\n".join(parts)}]
