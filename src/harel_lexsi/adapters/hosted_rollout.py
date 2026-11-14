"""One episode runner against any OpenAI-compatible endpoint: the HF router, or `scripts/serve_hf.py` in a Job.

It is the T2 baseline's rollout (scripts/baseline.py --backend api), packaged so the T8 collection and the
T8 evaluation can build one per worker. Same system prompt, same coach, same python-block writes, same
stop-on-green, same limits. The only things that change are the endpoint and the sampling temperature.

    sandbox = ShopSandbox()
    runner = EpisodeRunner(sandbox, base_url="http://localhost:8000/v1", api_key="x", api_model="openai/tiny")
    trajectory = runner.run(task)      # resets the sandbox for the task, plays the episode
    sandbox.attempts                   # every write the model made, refused ones too
"""
from __future__ import annotations

from typing import Any

from .builder_prompt import TOOL_RESULT, TOOLS_FALLBACK, system_prompt
from .episode_coach import EpisodeCoach
from .hosted_engine import make_hosted_engine
from .shop_harness import ShopSandbox, make_tools, patch_start_for

TOKENIZER = "CohereLabs/tiny-aya-global"
CONTEXT = 8192


class EpisodeRunner:
    def __init__(self, sandbox: ShopSandbox, *, base_url: str, api_key: str, api_model: str, tokenizer: Any = None,
                 max_steps: int = 8, max_new_tokens: int = 1024, temperature: float = 0.9):
        from agenttune.agentic import create_rollout_fn

        if tokenizer is None:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(TOKENIZER)  # only its chat template is used
        self.sandbox = sandbox
        self.engine = make_hosted_engine(api_model=api_model, api_key=api_key, base_url=base_url, tokenizer=tokenizer,
                                         target_path=sandbox.target_path, is_done=lambda: sandbox.green)
        self.captured: list = []
        coach = EpisodeCoach(sandbox)
        self._rollout = create_rollout_fn(
            rollout_engine=self.engine, tools=list(make_tools(sandbox).values()), max_steps=max_steps,
            on_trajectory_end=self.captured.append, pre_step_hook=coach.pre_step, post_step_hook=coach.post_step,
            system_prompt=system_prompt(), tools_fallback_prompt=TOOLS_FALLBACK, tool_result_format=TOOL_RESULT,
            context_length=CONTEXT - max_new_tokens - 64)
        self.gen = {"max_new_tokens": max_new_tokens, "temperature": temperature}

    def run(self, task: dict) -> Any:
        """Play one episode of `task` (a tasks.jsonl record) and return its trajectory."""
        self.sandbox.reset(start=patch_start_for(task["prompt"]), handler=task["handler"])
        self.captured.clear()
        self._rollout(task["prompt"], **self.gen)
        if not self.captured:
            raise RuntimeError("rollout produced no trajectory")
        return self.captured[-1]
