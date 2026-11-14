"""A1: the "standard LLM judge" reward that Harel is compared against.

The judge is AgentTune's `LLMJudge`, used through its own prompt builder and `JudgeScore`, with two
changes that matter for the comparison (A2 counts how many broken mutants each reward approves):

- A judge call that fails, or a reply that is not strict JSON with a score in [0, 1], is UNKNOWN
  (`score=None`). AgentTune's stock behaviour turns both into 0.0, which reads as "the judge rejected
  it" and would flatter the judge. UNKNOWN is never counted as a rejection or an approval.
- Temperature 0, and a bounded retry on transient API errors.

The prompt is fixed: `judge_prompt_v1.txt` plus the shared rules of the spec's `## Build` section (what the
builder was also told). It never sees the holdout contracts, the reference handlers or the verifier's
verdicts; the point of the baseline is a judge that works from what a reviewer would have.

    judge = HostedJudge(model="CohereLabs/c4ai-command-a-03-2025", api_key=..., base_url=HF_ROUTER)
    result = judge.judge(task_prompt, trajectory)      # JudgeResult(score=0.8|None, ...)
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenttune.agentic import Step, Trajectory
from agenttune.agentic.rewards.llm_judge import (
    LLMJudge,
    _build_absolute_prompt,
    _messages_from,
    _strip_fences,
)

from .builder_prompt import SPEC, shared_rules

PROMPT_FILE = Path(__file__).with_name("judge_prompt_v1.txt")
HF_ROUTER = "https://router.huggingface.co/v1"
DEFAULT_MODEL = "CohereLabs/c4ai-command-a-03-2025"  # 111B, served by Cohere on the HF router
APPROVE_AT = 0.5  # a score at or above this is "approved". Fixed before the first run.


def rubric(spec: Path = SPEC, prompt_file: Path = PROMPT_FILE) -> str:
    """The judge's system prompt: the saved template with the shared rules filled in."""
    return prompt_file.read_text().format(rules=shared_rules(spec.read_text()))


def rubric_sha256(text: str | None = None) -> str:
    return hashlib.sha256((text if text is not None else rubric()).encode()).hexdigest()


@dataclass(frozen=True)
class JudgeResult:
    score: float | None          # in [0, 1]; None = UNKNOWN (call failed or reply not valid)
    explanation: str = ""
    raw: str = ""
    error: str = ""

    @property
    def approved(self) -> bool | None:
        return None if self.score is None else self.score >= APPROVE_AT


def parse_strict(text: str) -> JudgeResult:
    """Strict JSON `{"score": number in [0, 1], "explanation": str}`; anything else is UNKNOWN."""
    body = _strip_fences(text or "")
    try:
        obj = json.loads(body)
        score = obj["score"]
        if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score) \
                or not 0 <= score <= 1:
            raise ValueError(f"score {score!r} is not a number in [0, 1]")
    except (ValueError, KeyError, TypeError) as e:
        return JudgeResult(None, raw=text or "", error=f"unparsed judge reply: {e}"[:200])
    return JudgeResult(float(score), str(obj.get("explanation", ""))[:400], raw=body)


class HostedJudge(LLMJudge):
    """AgentTune's LLMJudge over an OpenAI-compatible endpoint, with UNKNOWN instead of 0.0."""

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str, base_url: str = HF_ROUTER,
                 system_prompt: str | None = None, max_tokens: int = 300, retries: int = 3,
                 backoff: float = 2.0):
        super().__init__(model=model, api_key=api_key, base_url=base_url,
                         system_prompt=system_prompt if system_prompt is not None else rubric(),
                         judge_max_tokens=max_tokens)
        self.retries, self.backoff = retries, backoff

    def _call(self, messages: list[dict]) -> str:  # AgentTune's call, plus temperature 0 and retries
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = client.chat.completions.create(model=self.model, messages=messages,
                                                   max_tokens=self.judge_max_tokens, temperature=0)
                return r.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"{type(last).__name__}: {last}"[:200])

    def judge(self, task: str, trajectory: Any) -> JudgeResult:
        system, user = _build_absolute_prompt(task, trajectory, {}, self.absolute_rubric, self.system_prompt)
        try:
            return parse_strict(self._call(_messages_from(system, user)))
        except Exception as e:  # noqa: BLE001  never a score: UNKNOWN
            return JudgeResult(None, error=f"judge call failed: {e}"[:200])

    def reward_fn(self):
        """For AgentTune's `reward_fn=`: the score, or 0.0 for UNKNOWN (a trainer needs a number)."""
        return lambda traj: self.judge(getattr(traj, "task", ""), traj).score or 0.0


def trajectory_from_record(rec: dict) -> Trajectory:
    """A saved trajectories.jsonl record as the Trajectory object AgentTune's judge formats."""
    steps = [Step(step_number=s.get("step_number", i), state=s.get("state", ""), action=s.get("action") or {},
                  observation=str(s.get("observation", "")), thought=s.get("thought"))
             for i, s in enumerate(rec.get("steps") or [])]
    return Trajectory(task=rec["task"], steps=steps, trajectory_id=rec.get("trajectory_id", ""),
                      final_response=rec.get("final_response", ""))


def score_run(run: Path, judge: HostedJudge, out: Path | None = None) -> list[dict]:
    """Judge every trajectory of a baseline run and write `judge_scores.jsonl` + `judge_summary.json/md`.

    Each row also carries the Harel verdict and the visible-test result of the same episode, so the
    two rewards can be read side by side; the judge never sees them.
    """
    out = out or run
    results = {r["task_id"]: r for r in map(json.loads, (run / "results.jsonl").read_text().splitlines())}
    rows = []
    for line in (run / "trajectories.jsonl").read_text().splitlines():
        rec = json.loads(line)
        res = judge.judge(rec["task"], trajectory_from_record(rec))
        r = results[rec["task_id"]]
        rows.append({"task_id": rec["task_id"], "split": r["split"], "judge_score": res.score,
                     "judge_approved": res.approved, "judge_explanation": res.explanation,
                     "judge_error": res.error, "verifier": r["verdict"],
                     "visible_ok": r["visible_failed"] == 0 and r["visible_passed"] > 0})
    out.mkdir(parents=True, exist_ok=True)
    (out / "judge_scores.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    meta = {"judge_model": judge.model, "base_url": judge._base_url, "temperature": 0,
            "approve_at": APPROVE_AT, "rubric_sha256": rubric_sha256(judge.system_prompt),
            "prompt_file": PROMPT_FILE.name, "run": run.name}
    summary = summarise(rows)
    (out / "judge_summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2))
    (out / "judge_summary.md").write_text(render_markdown(meta, summary, rows))
    return rows


def summarise(rows: list[dict]) -> dict:
    """Judge approval against the Harel verdict, with n. UNKNOWN judge results are counted apart."""
    def block(rs: list[dict]) -> dict:
        known = [r for r in rs if r["judge_approved"] is not None]
        approved = [r for r in known if r["judge_approved"]]
        return {"n": len(rs), "judge_unknown": len(rs) - len(known), "judge_approved": len(approved),
                "approved_and_verified": sum(r["verifier"] == "pass" for r in approved),
                "approved_but_failed_verifier": sum(r["verifier"] == "fail" for r in approved),
                "rejected_but_verified": sum(r["verifier"] == "pass" for r in known if not r["judge_approved"]),
                "verifier_pass": sum(r["verifier"] == "pass" for r in rs),
                "verifier_fail": sum(r["verifier"] == "fail" for r in rs),
                "visible_ok": sum(r["visible_ok"] for r in rs)}
    by_score: dict[str, dict[str, int]] = {}
    for r in rows:
        key = "UNKNOWN" if r["judge_score"] is None else f"{r['judge_score']:.2f}"
        cell = by_score.setdefault(key, {"n": 0, "verifier_pass": 0, "verifier_fail": 0, "verifier_unknown": 0})
        cell["n"] += 1
        cell[f"verifier_{r['verifier']}"] += 1
    return {"all": block(rows), "train": block([r for r in rows if r["split"] == "train"]),
            "heldout": block([r for r in rows if r["split"] == "heldout"]),
            "by_judge_score": dict(sorted(by_score.items(), reverse=True))}


def render_markdown(meta: dict, summary: dict, rows: list[dict]) -> str:
    a = summary["all"]
    lines = ["# A1: LLM-judge reward on the T2 trajectories", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    header = ("| set | n | judge approved | of those, verifier PASS | of those, verifier FAIL | "
              "judge UNKNOWN | verifier PASS (all) |")
    lines += ["", header, "|---|---|---|---|---|---|---|"]
    for name in ("train", "heldout", "all"):
        s = summary[name]
        lines.append(f"| {name} | {s['n']} | {s['judge_approved']} | {s['approved_and_verified']} | "
                     f"{s['approved_but_failed_verifier']} | {s['judge_unknown']} | {s['verifier_pass']} |")
    lines += ["", "By judge score (does not depend on where the approval line is drawn):", "",
              "| judge score | n | verifier PASS | verifier FAIL |", "|---|---|---|---|"]
    lines += [f"| {k} | {c['n']} | {c['verifier_pass']} | {c['verifier_fail']} |"
              for k, c in summary["by_judge_score"].items()]
    lines += ["", (f"At the fixed line (score >= {APPROVE_AT}) the judge approved {a['judge_approved']} of "
                   f"{a['n']} trajectories; the verifier passed {a['verifier_pass']}. Two numbers with n, never "
                   f"blended."), "",
              "| task | split | judge | verifier | visible | why |", "|---|---|---|---|---|---|"]
    for r in rows:
        score = "UNKNOWN" if r["judge_score"] is None else f"{r['judge_score']:.2f}"
        lines.append(f"| {r['task_id']} | {r['split']} | {score} | {r['verifier']} | "
                     f"{'ok' if r['visible_ok'] else 'fail'} | {(r['judge_explanation'] or r['judge_error'])[:110]} |")
    return "\n".join(lines) + "\n"


__all__ = [
    "HostedJudge",
    "JudgeResult",
    "parse_strict",
    "rubric",
    "rubric_sha256",
    "score_run",
    "summarise",
    "trajectory_from_record",
]
