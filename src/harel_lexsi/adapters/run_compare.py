"""A3, A4, A5: load baseline/retrained runs into AuditKIT, compare them, and count wasted tool calls.

A run folder is what `scripts/baseline.py` writes: `trajectories.jsonl` (AgentTune trajectories, one per
task) and `results.jsonl` (the Harel verdict per task). Lives in adapters/ because it imports AuditKIT
(noncommercial); the verifier never imports it.

- A3 `load_run`: AuditKIT episodes from the trajectories, each carrying the Harel verdict, split and classes.
- A4 `compare_runs`: held-out tasks only, paired by task id, verified success with AuditKIT's paired
  bootstrap. Every number is printed with its sample size.
- A5 `tool_metrics`: steps, wrong calls and redundant calls per task.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auditkit._bootstrap import paired_bootstrap
from auditkit.agent_eval.importers import episodes_from_agenttune

from .baseline import KNOWN_TOOLS, EpisodeResult

# Marks a tool call that failed: AgentTune records results as str(dict) and failures as {'error': ...}.
_ERROR_MARK = "'error'"


@dataclass
class Run:
    name: str
    results: dict[str, EpisodeResult]            # task id -> verdict record (the Harel verdict)
    episodes: dict[str, Any] = field(default_factory=dict)   # task id -> AuditKIT AgentEpisode
    trajectories: dict[str, dict] = field(default_factory=dict)  # task id -> raw trajectory record
    meta: dict[str, Any] = field(default_factory=dict)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_run(folder: str | Path, name: str | None = None) -> Run:
    """A3: episodes from `trajectories.jsonl`, with the verifier verdict attached to each."""
    folder = Path(folder)
    results = {r["task_id"]: EpisodeResult(**r) for r in _read_jsonl(folder / "results.jsonl")}
    raw = {r["task_id"]: r for r in _read_jsonl(folder / "trajectories.jsonl")}
    episodes = episodes_from_agenttune(str(folder / "trajectories.jsonl"))
    by_id = dict(zip(raw, episodes, strict=True))  # same file, same order
    for task_id, ep in by_id.items():
        res = results.get(task_id)
        if res is not None:
            ep.metadata["harel"] = {"task_id": task_id, "verdict": res.verdict, "classes": res.classes,
                                    "split": res.split}
    meta_file = folder / "summary.json"
    meta = json.loads(meta_file.read_text()).get("meta", {}) if meta_file.is_file() else {}
    return Run(name or folder.name, results, by_id, raw, meta)


# ----- A5: tool-call metrics -------------------------------------------------------------------

def _calls(record: Mapping[str, Any]) -> list[tuple[str, Any, str]]:
    out = []
    for step in record.get("steps") or []:
        a = step.get("action") or {}
        out.append((a.get("name"), a.get("arguments"), str(step.get("observation", ""))))
    return out


def tool_metrics(record: Mapping[str, Any]) -> dict[str, int]:
    """Per task: `steps`, `wrong` calls (unknown tool, bad arguments, or the tool returned an error) and
    `redundant` calls (a repeat of an earlier identical call with nothing written in between)."""
    steps = wrong = redundant = 0
    seen: set[str] = set()
    for name, args, obs in _calls(record):
        steps += 1
        bad = name not in KNOWN_TOOLS or not isinstance(args, dict | type(None)) or _ERROR_MARK in obs
        wrong += bad
        key = json.dumps([name, args], sort_keys=True, default=str)
        if name == "write_file":
            seen.clear()  # a write changes the world, so reads and test runs are new again
        elif key in seen:
            redundant += 1
        seen.add(key)
    return {"steps": steps, "wrong": wrong, "redundant": redundant}


# ----- A4: before and after --------------------------------------------------------------------

def _outcome(run: Run, task_id: str) -> float:
    return 1.0 if run.results[task_id].verdict == "pass" else 0.0  # UNKNOWN and FAIL are both 0


def compare_runs(base: Run, new: Run, *, split: str = "heldout", n_resamples: int = 2000,
                 seed: int = 42) -> dict[str, Any]:
    """Paired comparison on the tasks of `split` that both runs have. Held-out by default."""
    ids = sorted(t for t, r in base.results.items() if r.split == split and t in new.results)
    pairs = [(_outcome(base, t), _outcome(new, t)) for t in ids]
    stats = paired_bootstrap(pairs, n_resamples=n_resamples, seed=seed) if len(pairs) >= 2 else {
        "n": len(pairs), "error": "fewer than 2 paired tasks"}

    def side(run: Run) -> dict[str, Any]:
        m = [tool_metrics(run.trajectories[t]) for t in ids if t in run.trajectories]
        n = len(m)
        mean = (lambda k: round(sum(x[k] for x in m) / n, 2) if n else 0.0)
        return {"n": n, "steps": mean("steps"), "wrong": mean("wrong"), "redundant": mean("redundant")}

    return {"split": split, "tasks": ids, "n": len(ids), "stats": stats,
            "tool_metrics": {base.name: side(base), new.name: side(new)}}


def render_comparison(base: Run, new: Run, c: Mapping[str, Any]) -> str:
    s, n = c["stats"], c["n"]
    lines = [f"# {base.name} vs {new.name} ({c['split']} tasks only)", ""]
    if "error" in s:
        return "\n".join(lines + [f"Not enough paired tasks to test significance (n={n})."]) + "\n"
    lines += ["| measure | " + base.name + " | " + new.name + " | n |", "|---|---|---|---|",
              f"| verified success | {s['mean_baseline']:.0%} | {s['mean_candidate']:.0%} | {n} |",
              "", f"delta {s['delta']:+.0%}, p={s['p_value']:.3f}, significant={s['significant']} (n={n} paired tasks)",
              "", "| tool calls per task | steps | wrong | redundant | n |", "|---|---|---|---|---|"]
    for name, m in c["tool_metrics"].items():
        lines.append(f"| {name} | {m['steps']} | {m['wrong']} | {m['redundant']} | {m['n']} |")
    return "\n".join(lines) + "\n"


def compare_folders(base_dir: str | Path, new_dir: str | Path, **kw: Any) -> tuple[dict[str, Any], str]:
    base, new = load_run(base_dir, "base"), load_run(new_dir, "retrained")
    c = compare_runs(base, new, **kw)
    return c, render_comparison(base, new, c)


def verdicts(runs: Sequence[Run]) -> dict[str, dict[str, str]]:
    return {r.name: {t: res.verdict for t, res in r.results.items()} for r in runs}
