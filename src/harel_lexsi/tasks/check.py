"""S5: freeze the train / held-out split, and check the task set.

`freeze` writes split.json: which task ids train and which are held out (whole handlers, never half a handler),
plus the SHA-256 of tasks.jsonl and of the split itself. It must run BEFORE the first baseline run. `check`
fails if anything has moved since, if a held-out handler leaks into training, and (with a spec) if a handler
has no holdout contract or a patch bug is not caught by the verifier, i.e. a task that could not be scored.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import types
from pathlib import Path

from .build import HANDLERS, TASKS_DIR, _reference, build_tasks, sha256_text, tasks_text

FORBIDDEN_IN_PROMPTS = ("holdout", "contract", "mutant", "reference", "trap")


def load_tasks(root: Path = TASKS_DIR) -> list[dict]:
    return [json.loads(line) for line in (Path(root) / "tasks.jsonl").read_text().splitlines() if line.strip()]


def _split_hash(split: dict) -> str:
    body = {k: split[k] for k in ("heldout_handlers", "train", "heldout", "tasks_sha256")}
    return sha256_text(json.dumps(body, sort_keys=True))


def freeze(root: Path, heldout_handlers: list[str]) -> dict:
    root = Path(root)
    path = root / "split.json"
    if path.exists():
        raise RuntimeError("split.json already exists: the split is frozen and must not change")
    tasks = load_tasks(root)
    known = {t["handler"] for t in tasks}
    unknown = sorted(set(heldout_handlers) - known)
    if unknown or not heldout_handlers:
        raise ValueError(f"held-out handlers must be real task handlers, got {heldout_handlers} (unknown: {unknown})")
    held = set(heldout_handlers)
    split = {
        "heldout_handlers": sorted(held),
        "train": [t["id"] for t in tasks if t["handler"] not in held],
        "heldout": [t["id"] for t in tasks if t["handler"] in held],
        "tasks_sha256": sha256_text((root / "tasks.jsonl").read_text()),
    }
    if not split["train"]:
        raise ValueError("nothing left to train on")
    split["split_sha256"] = _split_hash(split)
    path.write_text(json.dumps(split, indent=2, sort_keys=True) + "\n")
    return split


def _structure_errors(root: Path, tasks: list[dict]) -> list[str]:
    errs: list[str] = []
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        errs.append("duplicate task ids")
    fresh, starts = build_tasks()
    if tasks_text(fresh) != (root / "tasks.jsonl").read_text():
        errs.append("tasks.jsonl is not what the spec and reference code now produce (run: python -m "
                    "harel_lexsi.tasks build), unless the split is frozen on purpose")
    for t in tasks:
        low = t["prompt"].lower()
        errs += [f"{t['id']}: prompt mentions '{w}'" for w in FORBIDDEN_IN_PROMPTS if w in low]
        if t["kind"] == "implement":
            if t["start"] is not None:
                errs.append(f"{t['id']}: an implement task has no start file")
            continue
        start = root / t["start"] if t["start"] else None
        if start is None or not start.is_file():
            errs.append(f"{t['id']}: start file missing")
            continue
        text = start.read_text()
        if sha256_text(text) != t["start_sha256"]:
            errs.append(f"{t['id']}: start file changed since tasks.jsonl was written")
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            errs.append(f"{t['id']}: start file does not parse ({e})")
            continue
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        if t["handler"] not in funcs:
            errs.append(f"{t['id']}: start file does not define {t['handler']}")
        for word in ("reference", "holdout", "mutant"):
            if word in text.lower():
                errs.append(f"{t['id']}: start file mentions '{word}'")
        for name, node in funcs.items():
            if name in HANDLERS and name != t["handler"] and "NotImplementedError" not in ast.unparse(node):
                errs.append(f"{t['id']}: sibling handler {name} is not a stub, so it would hand out an answer")
    return errs


def _overlap_errors() -> list[str]:
    """Patch bugs must not be the V6 mutants or the S4 traps (those are the test set)."""
    task_bugs = _reference()
    from reference import mutants  # noqa: PLC0415
    errs = []
    mutant_patches = {(m.old.strip(), m.new.strip()) for m in mutants.MUTANTS}
    trap_text = "".join(p.read_text() for p in (Path(task_bugs.__file__).parent / "traps").glob("*.py"))
    for b in task_bugs.BUGS:
        if (b.old.strip(), b.new.strip()) in mutant_patches:
            errs.append(f"patch bug {b.id} is the same change as a V6 mutant")
        if b.new.strip() and b.new.strip() in trap_text:
            errs.append(f"patch bug {b.id} appears in an S4 trap")
    return errs


def _split_errors(root: Path, tasks: list[dict]) -> list[str]:
    path = root / "split.json"
    if not path.is_file():
        return ["split.json is missing: the split has not been frozen"]
    split = json.loads(path.read_text())
    errs = []
    if split.get("tasks_sha256") != sha256_text((root / "tasks.jsonl").read_text()):
        errs.append("tasks.jsonl changed after the split was frozen")
    if split.get("split_sha256") != _split_hash(split):
        errs.append("split.json does not match its own hash")
    train, held = set(split["train"]), set(split["heldout"])
    ids = {t["id"] for t in tasks}
    if train & held:
        errs.append(f"tasks in both splits: {sorted(train & held)}")
    if (train | held) != ids:
        errs.append("the split does not cover exactly the tasks in tasks.jsonl")
    by_id = {t["id"]: t for t in tasks}
    leaked = {by_id[i]["handler"] for i in train if i in by_id} & {by_id[i]["handler"] for i in held if i in by_id}
    if leaked:
        errs.append(f"held-out handlers also appear in training: {sorted(leaked)}")
    return errs


def _scorable_errors(root: Path, tasks: list[dict], spec: Path) -> list[str]:
    """Needs Postgres. Every handler has a holdout contract; every patch bug is caught by at least one."""
    from harel_lexsi.verifier import Verdict, load_binding, replay, settle  # noqa: PLC0415
    from harel_lexsi.verifier import gate as G  # noqa: PLC0415

    G.check_database()
    binding = load_binding(G.DEFAULT_BINDING)
    contracts = G.load_spec_contracts(spec, binding)
    errs = []
    for handler in sorted({t["handler"] for t in tasks}):
        if not any(c.handler == handler for c in contracts):
            errs.append(f"no holdout contract covers {handler}")
    for t in tasks:
        if t["kind"] != "patch":
            continue
        mine = [c for c in contracts if c.handler == t["handler"]]
        if not mine:
            continue
        module = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
            f"task_start_{t['id'].replace('-', '_')}", root / t["start"]))
        module.__spec__.loader.exec_module(module)
        handlers = types.SimpleNamespace(**{t["handler"]: getattr(module, t["handler"])})
        verdicts = []
        for c in mine:
            s = replay(c, handlers)
            verdicts.append(settle(c, s.capture, s.error, binding).verdict)
        if Verdict.FAIL not in verdicts:
            errs.append(f"{t['id']}: no holdout contract catches the bug in the start file, so the task could not "
                        f"be scored ({', '.join(v.value for v in verdicts)})")
    return errs


def check_tasks(root: Path = TASKS_DIR, *, spec: Path | None = None, allow_unfrozen: bool = False) -> list[str]:
    """All problems found; empty means the task set is sound."""
    root = Path(root)
    tasks = load_tasks(root)
    errs = _structure_errors(root, tasks) + _overlap_errors()
    if not (allow_unfrozen and not (root / "split.json").exists()):
        errs += _split_errors(root, tasks)
    if spec is not None:
        errs += _scorable_errors(root, tasks, spec)
    return errs
