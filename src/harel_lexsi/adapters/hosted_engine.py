"""A hosted (API) rollout engine for a Cohere model whose chat template ignores `tools`.

AgentTune's API engine assumes native tool calling. Tiny Aya has none, locally or hosted, so three
things are adapted here, all at the engine boundary (AgentTune itself is untouched):

1. Tokenizer. The API engine has none, so AgentTune cannot see that the chat template ignores `tools`
   and never turns on its system-prompt tool fallback. The engine is given the tokenizer (no weights).
2. Text-only protocol. The tool schemas are in the system prompt and tool results are user turns, so no
   native `tools` are sent and assistant turns that carry `tool_calls` are flattened to text (the API
   rejects them without matching tool messages).
4. Stop on green. Once the handler's visible tests all pass, the next turn is not requested (see
   `is_done`), which ends the episode with the passing function in place.
3. Code blocks are writes. A small model writes its function as a ```python block and then again,
   JSON-escaped, inside a tool call; the second copy runs out of tokens and cannot be parsed. A reply
   with a complete Python block that defines a function is therefore read as
   `write_file(<the task's module>, <the block>)`, and the rest of the reply is ignored.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

_BLOCK = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
_OPEN_BLOCK = re.compile(r"```(?:python|py)\s*\n(.*)\Z", re.DOTALL)  # never closed: cut off mid-reply


def code_block_as_write(reply: str, path: str | None) -> str | None:
    """The reply as a one-call tool list `write_file(path, code)` when it holds a Python block with a
    function in it; else None (the reply is left as the model wrote it).

    The block does not have to parse, or even be closed (a reply can run out of tokens): the harness
    refuses text that is not valid Python and says which line, which is feedback the model needs. Left as
    plain text, such a reply would end the episode with nothing written and nothing learned."""
    if not path or not reply:
        return None
    blocks = _BLOCK.findall(reply)
    if not blocks and (m := _OPEN_BLOCK.search(reply)):
        blocks = [m.group(1)]
    for code in blocks:
        if re.search(r"^\s*(async\s+)?def \w+\(", code, flags=re.MULTILINE):
            return json.dumps([{"tool_name": "write_file",
                                "parameters": {"path": path, "content": code.rstrip() + "\n"}}])
    return None


def _call_parts(call: dict) -> tuple[Any, Any]:
    src = call.get("function") if isinstance(call.get("function"), dict) else call
    args = src.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    return src.get("name"), args


def flatten_turn(message: Any) -> Any:
    """An assistant turn with `tool_calls`, as the text the model itself would have written: a Python
    block for a lone write_file (so its history matches what it is asked to produce), else a JSON list."""
    if not (isinstance(message, dict) and message.get("tool_calls")):
        return message
    calls = [_call_parts(c) for c in message["tool_calls"]]
    if len(calls) == 1 and calls[0][0] == "write_file" and isinstance(calls[0][1], dict) \
            and isinstance(calls[0][1].get("content"), str):
        text = "```python\n" + calls[0][1]["content"].rstrip() + "\n```"
    else:
        text = json.dumps([{"tool_name": n, "parameters": a} for n, a in calls])
    return {"role": message["role"], "content": text}


DONE = "done"


def make_hosted_engine(*, api_model: str, api_key: str, base_url: str, tokenizer: Any,
                       target_path: Callable[[], str | None],
                       is_done: Callable[[], bool] = lambda: False) -> Any:
    """APIRolloutEngine with the adaptations above. `target_path()` gives the module of the handler the
    current episode is about (ShopSandbox.target_path). `is_done()` (ShopSandbox.green) ends the episode:
    once the handler's visible tests all pass, the model is not asked again, so it cannot overwrite a
    passing function with a worse one (a small model does not stop on its own)."""
    from agenttune.agentic.rollout_engines.api_engine import APIRolloutEngine

    engine = APIRolloutEngine(model=api_model, api_key=api_key, base_url=base_url)
    engine._get_tokenizer = lambda: tokenizer
    generate = engine.generate

    def text_only(prompts, tools=None, gen_cfg=None):
        if is_done():  # a plain-text turn ends AgentTune's tool loop
            return {"completions": [DONE], "completions_structured": [{"role": "assistant", "content": DONE}],
                    "logprobs": [None], "metadata": {"backend": "api", "model": api_model, "ended": "green"}}
        if isinstance(prompts, list):
            prompts = [flatten_turn(m) for m in prompts]
        out = generate(prompts, tools=None, gen_cfg=gen_cfg)
        call = code_block_as_write(out["completions"][0], target_path())
        if call is not None:
            out["completions"][0] = call
            if out.get("completions_structured"):
                out["completions_structured"][0] = {"role": "assistant", "content": call}
        return out

    engine.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    counted = text_only

    def with_usage(prompts, tools=None, gen_cfg=None):
        out = counted(prompts, tools=tools, gen_cfg=gen_cfg)
        meta = out.get("metadata") or {}
        if "prompt_tokens" in meta:
            engine.usage["calls"] += 1
            engine.usage["prompt_tokens"] += int(meta.get("prompt_tokens") or 0)
            engine.usage["completion_tokens"] += int(meta.get("completion_tokens") or 0)
        return out

    engine.generate = with_usage
    return engine
