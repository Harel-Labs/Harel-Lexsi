"""T2: score a builder's episodes two ways and summarise, so the baseline shows the verifier/visible gap.

For each episode we rebuild the handler files the agent left behind and settle the handler's holdout
contracts against them (the verifier verdict), and we record how the visible tests did on the same final
workspace. The verifier verdict is the number that counts; visible tests are recorded beside it.

A patch task starts from a start file, so that file is the base: an agent that writes nothing is scored on
the buggy start, not on the stub. UNKNOWN (no contracts, timeout, crash) is its own bucket, never a pass.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harel_lexsi.verifier.classify import classify
from harel_lexsi.verifier.types import Settlement, Verdict

from .settlement_reward import _get, final_files, tool_calls

Scorer = Callable[[Mapping[str, str], str], Sequence[Settlement]]
KNOWN_TOOLS = ("read_file", "write_file", "run_visible_tests", "finish")


@dataclass
class EpisodeResult:
    task_id: str
    handler: str
    kind: str                 # implement | patch
    split: str                # train | heldout
    verdict: str              # pass | fail | unknown
    classes: list[str] = field(default_factory=list)
    visible_passed: int = 0
    visible_failed: int = 0
    steps: int = 0
    wrote_files: bool = False
    malformed_calls: int = 0
    error: str = ""

    @property
    def visible_ok(self) -> bool:
        return self.visible_failed == 0 and self.visible_passed > 0


def overall(settlements: Sequence[Settlement]) -> Verdict:
    if any(s.verdict is Verdict.FAIL for s in settlements):
        return Verdict.FAIL
    if settlements and all(s.verdict is Verdict.PASS for s in settlements):
        return Verdict.PASS
    return Verdict.UNKNOWN


def malformed_calls(trajectory: Any) -> int:
    """Tool calls the harness could not even dispatch: unknown tool name or non-object arguments."""
    n = 0
    for step in _get(trajectory, "steps", None) or []:
        for name, args in tool_calls(step):
            if name not in KNOWN_TOOLS or not isinstance(args, dict | type(None)):
                n += 1
    return n


def split_of(task_id: str, split: Mapping[str, Any]) -> str:
    return "heldout" if task_id in set(split.get("heldout", ())) else "train"


def score_episode(task: Mapping[str, Any], trajectory: Any, *, scorer: Scorer, split: Mapping[str, Any],
                  start_text: str | None = None, visible: Mapping[str, int] | None = None,
                  error: str = "") -> EpisodeResult:
    written = final_files(trajectory) if trajectory is not None else {}
    files = dict(written)
    if start_text is not None:
        files.setdefault(task["target"], start_text)  # patch task: an unedited start file is the answer
    if error or trajectory is None:
        settlements: list[Settlement] = []
    else:
        settlements = list(scorer(files, task["handler"]))
    verdict = overall(settlements)
    classes = sorted({f.failure_class.value for s in settlements for f in classify(s)})
    vis = visible or {}
    return EpisodeResult(
        task_id=task["id"], handler=task["handler"], kind=task["kind"], split=split_of(task["id"], split),
        verdict=verdict.value, classes=classes,
        visible_passed=int(vis.get("passed", 0)), visible_failed=int(vis.get("failed", 0)),
        steps=len(_get(trajectory, "steps", None) or []), wrote_files=bool(written),
        malformed_calls=malformed_calls(trajectory) if trajectory is not None else 0, error=error,
    )


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def summarise(results: Sequence[EpisodeResult]) -> dict[str, Any]:
    def block(rs: Sequence[EpisodeResult]) -> dict[str, Any]:
        n = len(rs)
        verified = sum(r.verdict == "pass" for r in rs)
        visible = sum(r.visible_ok for r in rs)
        return {
            "n": n,
            "verified_success": verified,
            "verified_rate": _rate(verified, n),
            "visible_pass": visible,
            "visible_rate": _rate(visible, n),
            "gap": round(_rate(visible, n) - _rate(verified, n), 4),
            "failed": sum(r.verdict == "fail" for r in rs),
            "unknown": sum(r.verdict == "unknown" for r in rs),
            "unknown_rate": _rate(sum(r.verdict == "unknown" for r in rs), n),
            "mean_steps": round(sum(r.steps for r in rs) / n, 2) if n else 0.0,
            "wrote_nothing": sum(not r.wrote_files for r in rs),
            "malformed_calls": sum(r.malformed_calls for r in rs),
            "errors": sum(bool(r.error) for r in rs),
            "failure_classes": dict(Counter(c for r in rs for c in r.classes)),
        }

    out = {"all": block(results)}
    for name in ("train", "heldout"):
        out[name] = block([r for r in results if r.split == name])
    for kind in ("implement", "patch"):
        out[kind] = block([r for r in results if r.kind == kind])
    return out


def render_markdown(meta: Mapping[str, Any], results: Sequence[EpisodeResult]) -> str:
    s = summarise(results)
    lines = ["# T2 baseline", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "| set | n | verified | visible | gap | unknown | mean steps | wrote nothing | malformed calls |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name in ("train", "heldout", "all", "implement", "patch"):
        b = s[name]
        lines.append(f"| {name} | {b['n']} | {b['verified_success']} ({b['verified_rate']:.0%}) | "
                     f"{b['visible_pass']} ({b['visible_rate']:.0%}) | {b['gap']:+.0%} | {b['unknown']} | "
                     f"{b['mean_steps']} | {b['wrote_nothing']} | {b['malformed_calls']} |")
    lines += ["", "| task | split | verifier | classes | visible (pass/fail) | steps | note |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.task_id} | {r.split} | {r.verdict} | {', '.join(r.classes) or '-'} | "
                     f"{r.visible_passed}/{r.visible_failed} | {r.steps} | {r.error or ''} |")
    return "\n".join(lines) + "\n"


def write_outputs(out: Path, meta: Mapping[str, Any], results: Sequence[EpisodeResult]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("".join(json.dumps(asdict(r)) + "\n" for r in results))
    (out / "summary.json").write_text(json.dumps({"meta": dict(meta), "summary": summarise(results)}, indent=2))
    (out / "summary.md").write_text(render_markdown(meta, results))


def _as_dict(trajectory: Any) -> dict[str, Any]:
    return trajectory.to_dict() if hasattr(trajectory, "to_dict") else dict(trajectory)


def run_baseline(tasks: Sequence[Mapping[str, Any]], *, split: Mapping[str, Any], sandbox: Any,
                 rollout: Callable[[str], Any], start_for: Callable[[str], tuple[str, str] | None],
                 scorer: Scorer, out: Path, meta: Mapping[str, Any]) -> list[EpisodeResult]:
    """Run each task once, in order, one at a time (the shop database is shared).

    `sandbox` needs `reset(start=...)` and `run_visible_tests()`; `rollout(prompt)` returns the episode's
    trajectory. Results are written after every episode, so a crash keeps what was already measured.
    A failing episode is recorded as UNKNOWN with its error and the run goes on.
    """
    out.mkdir(parents=True, exist_ok=True)
    results: list[EpisodeResult] = []
    traj_path = out / "trajectories.jsonl"
    traj_path.write_text("")
    for t in tasks:
        start = start_for(t["prompt"])
        sandbox.reset(start=start, handler=t["handler"])
        trajectory, error, visible = None, "", None
        try:
            trajectory = rollout(t["prompt"])
        except Exception as e:  # noqa: BLE001 - one bad episode must not end the run
            error = f"{type(e).__name__}: {e}"[:200]
        if trajectory is not None:
            try:
                visible = sandbox.run_visible_tests()
            except Exception as e:  # noqa: BLE001
                error = f"visible tests: {type(e).__name__}: {e}"[:200]
            record = {"task_id": t["id"], **_as_dict(trajectory)}
            with traj_path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        results.append(score_episode(t, trajectory, scorer=scorer, split=split,
                                     start_text=start[1] if start else None, visible=visible, error=error))
        write_outputs(out, meta, results)
    return results
