"""T8: from sampled answers to preference pairs, with the Harel verifier as the judge.

A preference pair is one task, one answer the verifier PASSES (chosen) and one it FAILS (rejected). The two are
answers to the same prompt, so DPO learns what separates them. The verifier is the only judge: nothing here
asks a model which answer is better.

    attempts (every write of every episode)  ->  Candidate (distinct function, verdict, classes)
    candidates                                ->  pairs (hard negatives first, capped per task)
    pairs                                     ->  train / eval split, coverage report, go/no-go

Two rules protect the held-out measurement: build_pairs refuses a candidate from a held-out task, and
`assert_train_only` checks a finished pair file. Everything here is plain Python: no model, no database
(the verifier is passed in as `scorer`, so tests use a fake).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

INVALID = "invalid_python"   # the harness refused the text: it is not Python
Scorer = Callable[[Mapping[str, Any], str], tuple[str, list[str]]]   # (task, code) -> (verdict, classes)


def normalise(code: str) -> str:
    """Whitespace-stable form of a function, so near-identical samples count once."""
    lines = [line.rstrip() for line in code.replace("\r\n", "\n").strip("\n").split("\n")]
    return "\n".join(lines).strip() + "\n"


def fingerprint(task_id: str, code: str) -> str:
    return hashlib.sha1(f"{task_id}\n{normalise(code)}".encode()).hexdigest()[:16]


def as_block(code: str) -> str:
    """The answer as the model writes it: one fenced python block."""
    return "```python\n" + code.rstrip("\n") + "\n```"


@dataclass
class Candidate:
    task_id: str
    code: str                      # normalised
    verdict: str                   # pass | fail | unknown
    classes: list[str] = field(default_factory=list)
    visible_ok: bool | None = None  # passed every visible test for the handler when it was written
    seen: int = 1                  # how many times the model produced it
    fp: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ----- attempts -> candidates ------------------------------------------------------------------------------------

def collect_candidates(task: Mapping[str, Any], attempts: Iterable[Mapping[str, Any]], scorer: Scorer) -> list[Candidate]:
    """The distinct functions among `attempts` (a sandbox's `attempts` for one or more episodes of `task`).

    A refused attempt (not valid Python) is a FAIL with class `invalid_python` and is not sent to the verifier.
    An accepted one is scored once per distinct text. UNKNOWN stays UNKNOWN and is never used by build_pairs."""
    seen: dict[str, Candidate] = {}
    for a in attempts:
        code = normalise(a["content"])
        fp = fingerprint(task["id"], code)
        tests = a.get("tests") or {}
        ok = bool(a.get("accepted")) and tests.get("failed") == 0 and (tests.get("passed") or 0) > 0
        if fp in seen:
            seen[fp].seen += 1
            seen[fp].visible_ok = bool(seen[fp].visible_ok or ok)
            continue
        if a.get("accepted"):
            verdict, classes = scorer(task, code)
        else:
            verdict, classes = "fail", [INVALID]
        seen[fp] = Candidate(task["id"], code, verdict, list(classes), ok, 1, fp)
    return list(seen.values())


def verifier_scorer(spec: Path) -> Scorer:
    """The real scorer: merge the function into the task's module the way the harness does, then settle the handler's
    contracts in the verifier's subprocess. Needs Postgres, so it only runs inside a Job."""
    from harel_lexsi.verifier.classify import classify
    from harel_lexsi.verifier.score import score_files

    from .baseline import overall
    from .settlement_reward import final_files

    cache: dict[str, tuple[str, list[str]]] = {}

    def score(task: Mapping[str, Any], code: str) -> tuple[str, list[str]]:
        key = fingerprint(task["id"], code)
        if key not in cache:
            traj = {"task": task["prompt"], "steps": [{"action": {"tool_calls": [{
                "name": "write_file", "arguments": {"path": task["target"], "content": code}}]}}]}
            files = final_files(traj)
            if not files:
                cache[key] = ("unknown", [])
            else:
                settlements = list(score_files(files, task["handler"], spec=spec))
                classes = sorted({f.failure_class.value for s in settlements for f in classify(s)})
                cache[key] = (overall(settlements).value, classes)
        return cache[key]
    return score


# ----- the prompt the model sees at inference ----------------------------------------------------------------------

def inference_messages(task: Mapping[str, Any]) -> list[dict]:
    """The two messages the model is given at step 0 of an episode: AgentTune's system message (the builder's system
    prompt plus the tool schemas in the fallback text) and the first user turn. DPO trains on exactly this prompt."""
    from transformers.utils import get_json_schema

    from .builder_prompt import TOOLS_FALLBACK, system_prompt
    from .shop_harness import ShopSandbox, make_tools
    from .train_rollout import training_prompt

    sandbox = ShopSandbox()
    schemas = [get_json_schema(fn) for fn in make_tools(sandbox).values()]
    system = system_prompt() + "\n\n" + TOOLS_FALLBACK.format(tools=json.dumps(schemas, indent=2))
    return [{"role": "system", "content": system}, {"role": "user", "content": training_prompt(dict(task))}]


# ----- candidates -> pairs ---------------------------------------------------------------------------------------

def _priority(c: Candidate) -> int:
    """0 = looks right and is wrong (hardest negative), 1 = a real failure, 2 = not even Python."""
    if INVALID in c.classes:
        return 2
    return 0 if c.visible_ok else 1


def build_pairs(candidates: Iterable[Candidate], tasks: Mapping[str, Mapping[str, Any]], *,
                messages_for: Callable[[Mapping[str, Any]], list[dict]], heldout: Iterable[str] = (),
                per_task_cap: int = 40, max_invalid_share: float = 0.25) -> list[dict]:
    """Pairs (chosen = a PASS, rejected = a FAIL, same task), hardest negatives first, at most `per_task_cap` per task.

    Not-valid-Python negatives are capped at `max_invalid_share` of a task's pairs, so the model also learns what a
    wrong but valid function looks like. A task with no PASS or no FAIL gives no pairs. Deterministic."""
    held = set(heldout)
    by_task: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        if c.task_id in held:
            raise ValueError(f"a candidate from the held-out task {c.task_id} must never reach the pairs")
        by_task[c.task_id].append(c)
    pairs: list[dict] = []
    max_invalid = math.ceil(per_task_cap * max_invalid_share)
    for task_id in sorted(by_task):
        cs = by_task[task_id]
        passes = sorted((c for c in cs if c.verdict == "pass"), key=lambda c: (-c.seen, c.fp))
        fails = sorted((c for c in cs if c.verdict == "fail"), key=lambda c: (_priority(c), -c.seen, c.fp))
        if not passes or not fails:
            continue
        prompt = messages_for(tasks[task_id])
        picked: list[Candidate] = []
        invalid = 0
        for f in fails:
            if len(picked) >= per_task_cap:
                break
            if _priority(f) == 2:
                if invalid >= max_invalid:
                    continue
                invalid += 1
            picked.append(f)
        for i, f in enumerate(picked):
            good = passes[i % len(passes)]  # round robin through the passing answers
            pairs.append({"task_id": task_id, "prompt": prompt,
                          "chosen": [{"role": "assistant", "content": as_block(good.code)}],
                          "rejected": [{"role": "assistant", "content": as_block(f.code)}],
                          "rejected_classes": f.classes, "hard_negative": _priority(f) == 0,
                          "chosen_fp": good.fp, "rejected_fp": f.fp})
    return pairs


def split_pairs(pairs: Sequence[dict], eval_fraction: float = 0.1) -> tuple[list[dict], list[dict]]:
    """Hold back a share of each task's pairs (the last ones, by fingerprint order) for the DPO eval loss only."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_task[p["task_id"]].append(p)
    train: list[dict] = []
    held: list[dict] = []
    for task_id in sorted(by_task):
        rows = sorted(by_task[task_id], key=lambda p: p["rejected_fp"])
        n_eval = int(len(rows) * eval_fraction) if len(rows) >= 5 else 0
        train += rows[: len(rows) - n_eval]
        held += rows[len(rows) - n_eval:]
    return train, held


def assert_train_only(pairs: Iterable[Mapping[str, Any]], heldout: Iterable[str]) -> None:
    bad = sorted({p["task_id"] for p in pairs} & set(heldout))
    if bad:
        raise AssertionError(f"held-out tasks in the pairs: {bad}")


# ----- what came out ---------------------------------------------------------------------------------------------

def coverage(candidates: Sequence[Candidate], pairs: Sequence[dict], train_task_ids: Iterable[str]) -> dict:
    """Per-task and overall counts: where the pairs come from, and what is missing."""
    per: dict[str, dict[str, int]] = {t: {"candidates": 0, "pass": 0, "fail": 0, "unknown": 0, "pairs": 0}
                                      for t in train_task_ids}
    for c in candidates:
        row = per.setdefault(c.task_id, {"candidates": 0, "pass": 0, "fail": 0, "unknown": 0, "pairs": 0})
        row["candidates"] += 1
        row[c.verdict] += 1
    for p in pairs:
        per[p["task_id"]]["pairs"] += 1
    rejected = Counter(c for p in pairs for c in (p["rejected_classes"] or ["(none)"]))
    n = len(pairs)
    return {"tasks": len(per), "tasks_with_a_pass": sum(1 for r in per.values() if r["pass"]),
            "tasks_with_pairs": sum(1 for r in per.values() if r["pairs"]), "pairs": n,
            "hard_negative_share": round(sum(p["hard_negative"] for p in pairs) / n, 3) if n else 0.0,
            "invalid_share": round(rejected.get(INVALID, 0) / n, 3) if n else 0.0,
            "rejected_classes": dict(rejected), "per_task": per}


def go_no_go(report: Mapping[str, Any], *, min_tasks: int = 4, min_pairs: int = 60) -> tuple[bool, list[str]]:
    """The check between pair-building and training: enough tasks and enough pairs, or stop and report."""
    reasons = []
    if report["tasks_with_pairs"] < min_tasks:
        reasons.append(f"only {report['tasks_with_pairs']} task(s) have pairs (need {min_tasks})")
    if report["pairs"] < min_pairs:
        reasons.append(f"only {report['pairs']} pair(s) (need {min_pairs})")
    return not reasons, reasons


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
