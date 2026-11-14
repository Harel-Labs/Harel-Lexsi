"""What GRPO training needs around AgentTune's rollout so that it trains in the harness it is measured in.

The baseline (`scripts/baseline.py`) runs the builder through `EpisodeCoach` and the hosted engine. Training
cannot reuse those two as they are: AgentTune computes the prompt ids once, and the completion ids are
the model's own tokens, so the prompt must not be rewritten mid-episode and the history must not be
compacted. What carries over, and is shared code:

- the system prompt (`builder_prompt`) and the first user turn (`episode_coach.first_turn_text`), built
  here into the dataset prompt itself, so what the model is trained on is what it is measured on;
- the visible tests for the task's handler after every write, and the same `write_file` merge;
- "a ```python block is a write" and "stop on green", applied by wrapping AgentTune's tool-call extractor
  (`install_block_writes`). Only the text is reinterpreted; the tokens GRPO trains on stay the model's own.

Not shared: the compact repair request after a failed write. In training the history simply grows, with a
small step limit so it stays inside the context window.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .episode_coach import first_turn_text
from .hosted_engine import code_block_as_write
from .shop_harness import SHOP, TASKS, ShopSandbox, patch_start_for, task_for

VISIBLE = SHOP / "app" / "tests" / "visible" / "test_visible.py"
SCHEMA = SHOP / "app" / "schema.sql"


def training_prompt(task: dict, tasks_dir: Path = TASKS) -> str:
    """The prompt the model is trained on for `task`: the task text plus tests, tables and current code."""
    if task.get("start"):
        module_text = (tasks_dir / task["start"]).read_text()
    else:
        module_text = (SHOP / task["target"]).read_text()
    text, _, _ = first_turn_text(task["prompt"], task["handler"], task["target"], VISIBLE.read_text(),
                                 SCHEMA.read_text(), module_text)
    return text


def load_tasks(tasks_dir: Path = TASKS) -> list[dict]:
    return [json.loads(line) for line in (tasks_dir / "tasks.jsonl").read_text().splitlines() if line.strip()]


def training_rows(tasks: Sequence[dict], repeat: int = 1) -> dict[str, list]:
    """Dataset columns: `prompt` (a string) and `handler` (read by the settlement reward)."""
    rows = [t for t in tasks for _ in range(repeat)]
    return {"prompt": [training_prompt(t) for t in rows], "handler": [t["handler"] for t in rows]}


class EpisodeReset:
    """AgentTune `pre_step_hook`: at the start of each episode, reset the sandbox for that prompt's task.

    It only resets (clean workspace, seeded database, the patch task's start file); it does not touch the
    conversation, because the prompt ids were computed before this hook runs."""

    def __init__(self, sandbox: ShopSandbox):
        self.sandbox = sandbox
        self.episodes = 0

    def __call__(self, iteration: int, conversation: list[dict], tools: Any = None) -> None:
        if iteration != 0:
            return
        prompt = next((m["content"] for m in reversed(conversation) if m.get("role") == "user"), "")
        task = task_for(prompt)
        if task is None:
            raise RuntimeError("training prompt does not start with any task's prompt")
        self.sandbox.reset(start=patch_start_for(prompt), handler=task["handler"])
        self.episodes += 1


def install_block_writes(sandbox: ShopSandbox) -> Callable[[], None]:
    """Make AgentTune read a ```python block as a write_file call, and stop once the tests pass.

    Wraps `rollout_factory._extract_tool_calls`, which the tool loop calls on every model turn:
    - once the handler's visible tests all pass (`sandbox.green`), the episode ends: no further call is read,
      so a later block cannot overwrite a passing function;
    - otherwise a reply holding a Python block with a function becomes `write_file(<the task's module>, block)`.
    Returns a function that removes the patch."""
    from agenttune.agentic.rollout_engines import rollout_factory as rf

    original = rf._extract_tool_calls

    def extract(completion_msg: Any, raw_text: str):
        if sandbox.green:
            return []
        call = code_block_as_write(raw_text, sandbox.target_path())
        if call is not None:
            msg = {**completion_msg, "content": call} if isinstance(completion_msg, dict) else completion_msg
            return original(msg, call)
        return original(completion_msg, raw_text)

    rf._extract_tool_calls = extract
    return lambda: setattr(rf, "_extract_tool_calls", original)
