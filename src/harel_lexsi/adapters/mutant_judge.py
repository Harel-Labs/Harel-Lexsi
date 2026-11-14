"""A2: show the V7 mutants to the LLM judge, and count what each reward approves. Rules: docs/a2-plan.md.

An item is one handler as the judge will read it: a broken one (17 mutants, 2 traps) or a correct one (6 controls).
The judge gets the handler's task prompt, the helper functions the code calls, the code, and optionally the result of
running the visible tests. It never gets the mutant's id, operator or `why` note, the contracts, or the verifier's verdict.

Pure Python here (no database, no network): building the items needs `shop/` on sys.path so `reference` imports;
running tests, the verifier and the judge happens in scripts/a2_run.py, inside a Job.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from harel_lexsi.verifier.replay import SHOP

APPROVE_AT = 1.0     # docs/a2-plan.md: approved = the judge found nothing wrong
LOOSE_AT = 0.5       # sensitivity check only
HELPERS = SHOP / "reference" / "handlers" / "_util.py"


def _reference():
    if str(SHOP) not in sys.path:
        sys.path.insert(0, str(SHOP))
    from reference import mutants  # noqa: PLC0415  (needs SHOP on sys.path)

    return mutants, importlib.import_module("reference.handlers")


def presentable(src: str) -> str:
    """Mutant source as ordinary code: every `_skip(...)` statement (the mutation set's placeholder for "this call was
    removed") is deleted, or replaced by `pass` when deleting it would leave a block empty."""
    tree = ast.parse(src)
    spans = sorted(((n.lineno, n.end_lineno) for n in ast.walk(tree)
                    if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Name) and n.value.func.id == "_skip"), reverse=True)
    lines = src.split("\n")
    for first, last in spans:
        kept = lines[: first - 1] + lines[last:]
        try:
            ast.parse("\n".join(kept))
            lines = kept
        except SyntaxError:  # it was the only statement of its block
            indent = lines[first - 1][: len(lines[first - 1]) - len(lines[first - 1].lstrip())]
            lines = lines[: first - 1] + [indent + "pass"] + lines[last:]
    out = "\n".join(lines)
    ast.parse(out)
    return out


@dataclass(frozen=True)
class Item:
    id: str            # never shown to the judge
    kind: str          # mutant | trap | control
    handler: str
    operator: str      # never shown to the judge
    code: str          # what the judge reads
    target: str        # app/handlers/<module>.py
    handlers_module: str = ""   # for traps and controls: the module the visible tests import

    @property
    def broken(self) -> bool:
        return self.kind != "control"

    def as_dict(self) -> dict:
        return asdict(self)


def build_items(tasks: Mapping[str, Mapping[str, Any]], traps: Sequence[tuple[str, str, Any]]) -> list[Item]:
    """The 19 broken handlers and the 6 correct ones, as the judge will read them.

    `tasks` maps task id to the tasks.jsonl record (for each handler's target file); `traps` is verifier.gate.TRAPS."""
    mutants, reference = _reference()
    target = {t["handler"]: t["target"] for t in tasks.values() if t["kind"] == "implement"}
    items = [Item(m.id, "mutant", m.handler, m.operator, presentable(mutants.source(m)), target[m.handler])
             for m in mutants.MUTANTS]
    for module, handler, _ in traps:
        trap_src = (SHOP / "reference" / "traps" / f"{module}.py").read_text()
        wrapper = trap_src[trap_src.index(f"def {handler}("):].rstrip() + "\n"
        wrapped = inspect.getsource(getattr(reference, handler)).replace(f"def {handler}(", f"def _{handler}(", 1)
        items.append(Item(module, "trap", handler, "trap", wrapped.rstrip() + "\n\n\n" + wrapper, target[handler],
                          f"reference.traps.{module}"))
    for handler in dict.fromkeys(i.handler for i in items):  # the correct version of every handler that has a mutant
        items.append(Item(f"reference-{handler}", "control", handler, "none", inspect.getsource(getattr(reference, handler)),
                          target[handler], "reference.handlers"))
    return items


def judge_task(task_prompt: str, helpers_source: str | None = None) -> str:
    """What the judge is told the code is for: the builder's own task prompt, plus the helpers the code may call."""
    helpers = helpers_source if helpers_source is not None else HELPERS.read_text()
    return (f"{task_prompt}\n\nThe handler may call these helper functions, which already exist and are correct:\n"
            f"```python\n{helpers.strip()}\n```")


def as_trajectory(item: Item, task: str, tests: Mapping[str, int] | None):
    """One step: the code was written; with `tests`, the result line a judge reward would also see."""
    from agenttune.agentic import Step, Trajectory

    obs = "saved" if tests is None else f"saved. visible tests: {tests['passed']} passed, {tests['failed']} failed"
    action = {"tool_calls": [{"name": "write_file", "arguments": {"path": item.target, "content": item.code}}]}
    return Trajectory(task=task, steps=[Step(step_number=0, state="", action=action, observation=obs)])


# ----- counting ------------------------------------------------------------------------------------------------------

def approved(score: float | None, line: float = APPROVE_AT) -> bool | None:
    """None = UNKNOWN (the judge gave no usable score): never an approval, never a rejection."""
    return None if score is None else score >= line


def count(rows: Iterable[Mapping[str, Any]], key: str, line: float = APPROVE_AT) -> dict[str, int]:
    """Judge counts over `rows` for the score column `key`: n, approved, rejected, unknown."""
    rows = list(rows)
    marks = [approved(r[key], line) for r in rows]
    return {"n": len(rows), "approved": sum(m is True for m in marks), "rejected": sum(m is False for m in marks),
            "unknown": sum(m is None for m in marks)}


def count_verifier(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    """Verifier counts for the verdict column `key`: approved = no contract failed ("pass"); UNKNOWN apart."""
    rows = list(rows)
    return {"n": len(rows), "approved": sum(r[key] == "pass" for r in rows), "rejected": sum(r[key] == "fail" for r in rows),
            "unknown": sum(r[key] not in ("pass", "fail") for r in rows)}


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    broken = [r for r in rows if r["kind"] != "control"]
    control = [r for r in rows if r["kind"] == "control"]

    def block(rs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {"verifier_fixtures": count_verifier(rs, "verifier_fixtures"),
                "verifier_hidden_contracts": count_verifier(rs, "verifier_hidden"),
                "judge_with_tests": count(rs, "judge_with_tests"), "judge_code_only": count(rs, "judge_code_only"),
                "judge_with_tests_loose_line": count(rs, "judge_with_tests", LOOSE_AT),
                "judge_code_only_loose_line": count(rs, "judge_code_only", LOOSE_AT),
                "pass_visible_tests": sum(r["visible_failed"] == 0 and r["visible_passed"] > 0 for r in rs)}
    return {"broken": block(broken), "mutants": block([r for r in broken if r["kind"] == "mutant"]),
            "traps": block([r for r in broken if r["kind"] == "trap"]), "control": block(control),
            "approve_at": APPROVE_AT, "loose_at": LOOSE_AT}


def render_markdown(meta: Mapping[str, Any], s: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    def c(d: Mapping[str, int]) -> str:
        extra = f", {d['unknown']} UNKNOWN" if d["unknown"] else ""
        return f"{d['approved']} of {d['n']}{extra}"

    b, ctl = s["broken"], s["control"]
    lines = ["# A2: what each reward approved, on the same broken handlers", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "## Broken handlers approved (lower is better)", "",
              "| reward | all 19 | 17 mutants | 2 traps |", "|---|---|---|---|"]
    for label, key in (("verifier, hidden spec contracts", "verifier_hidden_contracts"),
                       ("verifier, V7 fixtures (designed for these mutants)", "verifier_fixtures"),
                       ("**LLM judge, code + visible tests, score 1.0 (headline)**", "judge_with_tests"),
                       ("LLM judge, code only, score 1.0", "judge_code_only"),
                       ("LLM judge, code + visible tests, looser line 0.5 (sensitivity)", "judge_with_tests_loose_line"),
                       ("LLM judge, code only, looser line 0.5 (sensitivity)", "judge_code_only_loose_line")):
        lines.append(f"| {label} | {c(b[key])} | {c(s['mutants'][key])} | {c(s['traps'][key])} |")
    lines += ["", f"{b['pass_visible_tests']} of {b['verifier_fixtures']['n']} broken handlers pass every visible test that calls them.", "",
              "## Control: the 6 correct handlers approved (higher is better)", "", "| reward | approved |", "|---|---|"]
    for label, key in (("verifier, hidden spec contracts", "verifier_hidden_contracts"), ("verifier, V7 fixtures", "verifier_fixtures"),
                       ("LLM judge, code + visible tests, score 1.0", "judge_with_tests"), ("LLM judge, code only, score 1.0", "judge_code_only")):
        lines.append(f"| {label} | {c(ctl[key])} |")
    lines += ["", "## Every item", "",
              "| item | kind | handler | visible tests | verifier hidden | verifier fixtures | judge + tests | judge code only | judge's reason (with tests) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        def sc(x):
            return "UNKNOWN" if x is None else f"{x:.2f}"
        lines.append(f"| {r['id']} | {r['kind']} | {r['handler']} | {r['visible_passed']}/{r['visible_passed'] + r['visible_failed']} | "
                     f"{r['verifier_hidden']} | {r['verifier_fixtures']} | {sc(r['judge_with_tests'])} | {sc(r['judge_code_only'])} | "
                     f"{(r.get('judge_with_tests_why') or '')[:140]} |")
    return "\n".join(lines) + "\n"
