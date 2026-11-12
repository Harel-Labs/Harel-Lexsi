"""T1: `harel_settlement_reward`, an AgentTune reward function backed by the Harel verifier.

AgentTune calls `reward_fn(completions, **columns)` and, in agentic mode, also passes `trajectories`:
one rollout per completion, whose steps hold the agent's tool calls. We rebuild the final handler files
from the `write_file` calls, settle the handler's contracts against them in a subprocess (see
verifier/score.py), and turn the settlements into a number with `settlement_reward`.

    trainer = create_agentic_trainer(..., reward_funcs=[harel_settlement_reward])
    # train_dataset needs a `handler` column naming the handler each task asks for

Deterministic: same trajectory, same files, same replay, same reward. UNKNOWN (timeout, crash, no
contracts, no trajectory) scores 0, never positive. Scoring uses the shared shop database one rollout at
a time.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from harel_lexsi.verifier.score import DEFAULT_TIMEOUT, allowed_path, score_files
from harel_lexsi.verifier.types import Settlement

from .rewards import settlement_reward

Scorer = Callable[[Mapping[str, str], str], Sequence[Settlement]]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def final_files(trajectory: Any) -> dict[str, str]:
    """Files the agent left behind: its `write_file` calls in order, later writes winning.

    Accepts an AgentTune Trajectory or its dict form. Calls with bad arguments or a path the harness
    would refuse are skipped, exactly as the harness itself refused them.
    """
    files: dict[str, str] = {}
    for step in _get(trajectory, "steps", None) or []:
        action = _get(step, "action", None) or {}
        if _get(action, "name") != "write_file":
            continue
        args = _get(action, "arguments", None) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        path, content = (args.get("path"), args.get("content")) if isinstance(args, dict) else (None, None)
        if isinstance(path, str) and isinstance(content, str) and allowed_path(path):
            files[path] = content
    return files


def make_harel_settlement_reward(
    *,
    scorer: Scorer | None = None,
    spec: Path | None = None,
    contracts: Sequence[Path] = (),
    fail_reward: float = -1.0,
    step_penalty: float = 0.0,
    timeout: int = DEFAULT_TIMEOUT,
) -> Callable[..., list[float]]:
    """Build the reward function. `scorer` defaults to the subprocess scorer; tests inject a fake."""
    score = scorer or (lambda files, handler: score_files(
        files, handler, spec=spec, contracts=contracts, timeout=timeout))
    cache: dict[str, float] = {}

    def harel_settlement_reward(completions, handler=None, trajectories=None, **kwargs) -> list[float]:
        n = len(completions)
        handlers = handler if isinstance(handler, list) else [handler] * n
        trajs = trajectories if trajectories is not None else [None] * n
        out = []
        for h, traj in zip(handlers, trajs, strict=True):
            if not h or traj is None:
                out.append(0.0)  # nothing to score: UNKNOWN
                continue
            files = final_files(traj)
            key = hashlib.sha256(json.dumps([h, files], sort_keys=True).encode()).hexdigest()
            if key not in cache:
                cache[key] = settlement_reward(score(files, h), fail_reward=fail_reward,
                                               step_penalty=step_penalty)
            out.append(cache[key])
        return out

    return harel_settlement_reward


# Ready to use with the defaults: reward_funcs=[harel_settlement_reward]
harel_settlement_reward = make_harel_settlement_reward()

__all__ = ["final_files", "harel_settlement_reward", "make_harel_settlement_reward"]
