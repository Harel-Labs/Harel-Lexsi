"""S5: build the task set from the spec and the reference code. Deterministic, no model involved.

Two kinds of task, one handler each:
  implement  the stub in app/handlers/<module>.py; the prompt is that handler's row of the Build section
  patch      the same file with the handler already written but carrying one fresh bug (reference/task_bugs.py)
             and its siblings left as stubs, so a task never hands out another handler's answer

A task prompt never describes a bug and never quotes a contract. `planned_effects` are what the Build section
says the handler changes, written from the Build text only (never from the holdout); V9 compares them with the
holdout to find effects no task plans for.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

from harel_lexsi.spec.split import split_spec
from harel_lexsi.verifier.replay import ROOT, SHOP

TASKS_DIR = ROOT / "tasks"
SPEC = ROOT / "spec" / "spec.md"

# handler -> (module in app/handlers, group)
HANDLERS: dict[str, tuple[str, str]] = {
    "add_to_cart": ("cart", "cart"), "update_cart_item": ("cart", "cart"),
    "remove_from_cart": ("cart", "cart"), "clear_cart": ("cart", "cart"),
    "apply_coupon": ("coupons", "coupons"), "remove_coupon": ("coupons", "coupons"),
    "checkout": ("checkout", "checkout"),
    "get_order": ("orders", "orders"), "list_orders": ("orders", "orders"),
    "cancel_order": ("orders", "orders"),
    "share_cart": ("share", "share"), "view_shared_cart": ("share", "share"),
    "import_shared_cart": ("share", "share"),
}

# What the Build section says each handler changes, as "resource:op". Read-only handlers change nothing.
PLANNED: dict[str, list[str]] = {
    "add_to_cart": ["carts:insert", "cart_items:insert", "cart_items:update"],
    "update_cart_item": ["cart_items:update", "cart_items:delete"],
    "remove_from_cart": ["cart_items:delete"],
    "clear_cart": ["cart_items:delete", "carts:update"],
    "apply_coupon": ["carts:update"],
    "remove_coupon": ["carts:update"],
    "checkout": ["orders:insert", "order_items:insert", "inventory:update", "coupon_redemptions:insert",
                 "carts:update", "cart_items:delete", "outbox:emit"],
    "get_order": [], "list_orders": [],
    "cancel_order": ["orders:update", "inventory:update", "coupon_redemptions:update", "outbox:emit"],
    "share_cart": ["share_links:insert", "outbox:emit"],
    "view_shared_cart": [],
    "import_shared_cart": ["carts:insert", "cart_items:insert", "cart_items:update"],
}


def _reference():
    """The reference package lives in shop/, which is not on the path by default."""
    if str(SHOP) not in sys.path:
        sys.path.insert(0, str(SHOP))
    from reference import task_bugs  # noqa: PLC0415
    return task_bugs


def behaviours(spec_text: str) -> dict[str, str]:
    """handler -> its 'Behaviour' cell from the Build section's tables."""
    out = {}
    for line in split_spec(spec_text).build.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`(\w+)`", cells[0]) if cells else None
        if m and len(cells) >= 3:
            out[m.group(1)] = cells[2]
    return out


def _names(text: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(textwrap.dedent(text))) if isinstance(n, ast.Name)}


def compose_start(bug) -> str:
    """The patch task's starting file: the module's stubs, with this one handler (and the helpers it needs) written."""
    task_bugs = _reference()
    stub_path = SHOP / "app" / "handlers" / f"{bug.module}.py"
    stub_lines = stub_path.read_text().splitlines()
    target = next(n for n in ast.parse(stub_path.read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == bug.handler)
    doc = stub_lines[target.body[0].lineno - 1: target.body[0].end_lineno]

    patched = task_bugs.patched_source(bug).rstrip("\n").splitlines()
    assert patched[0].rstrip().endswith(":"), "handler signature must fit on one line"
    patched_text = "\n".join([patched[0], *doc, *patched[1:]])

    # helpers the handler needs (open_cart, emit, ...), copied so the file stands alone
    pool: dict[str, str] = {}
    for path in (SHOP / "reference" / "handlers" / f"{bug.module}.py", SHOP / "reference" / "handlers" / "_util.py"):
        src = path.read_text()
        lines = src.splitlines()
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name not in HANDLERS:
                pool[node.name] = "\n".join(lines[node.lineno - 1: node.end_lineno])
    seen: set[str] = set()
    stack = list(_names(patched_text) & pool.keys())
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack += list(_names(pool[name]) & pool.keys())
    blocks = [pool[n] for n in sorted(seen)] + [patched_text]

    used = set().union(*(_names(b) for b in blocks))
    imports = [i for name, i in (("secrets", "import secrets"), ("Jsonb", "from psycopg.types.json import Jsonb"))
               if name in used]

    new = stub_lines[: target.lineno - 1] + "\n\n\n".join(blocks).splitlines() + stub_lines[target.end_lineno:]
    at = next(i for i, ln in enumerate(new) if ln.startswith("from app.errors import ShopError")) + 1
    return "\n".join(new[:at] + imports + new[at:]) + "\n"


def build_tasks(spec_text: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """(tasks, {start path: file text}). Pure function of the spec and the reference code."""
    task_bugs = _reference()
    rows = behaviours(spec_text if spec_text is not None else SPEC.read_text())
    missing = sorted(set(HANDLERS) - set(rows))
    if missing:
        raise ValueError(f"the Build section has no row for: {missing}")
    bugs = {b.handler: b for b in task_bugs.BUGS}
    tasks: list[dict] = []
    starts: dict[str, str] = {}

    def signature(handler: str) -> str:
        module = HANDLERS[handler][0]
        text = (SHOP / "app" / "handlers" / f"{module}.py").read_text()
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == handler)
        return text.splitlines()[node.lineno - 1].rstrip(":").removeprefix("def ")

    for handler, (module, group) in HANDLERS.items():
        tasks.append({
            "id": f"implement-{handler}", "kind": "implement", "handler": handler, "module": module,
            "group": group, "target": f"app/handlers/{module}.py", "start": None, "start_sha256": None,
            "planned_effects": PLANNED[handler],
            "prompt": (f"Implement `{signature(handler)}` in `app/handlers/{module}.py`.\n\n"
                       f"What it must do: {rows[handler]}\n\n"
                       "The module docstring explains the conventions and `app/schema.sql` has the tables. "
                       "Run the visible tests, then finish."),
        })
    for handler, (module, group) in HANDLERS.items():
        if handler not in bugs:
            continue
        path = f"starts/patch-{handler}.py"  # relative to tasks/
        text = compose_start(bugs[handler])
        starts[path] = text
        tasks.append({
            "id": f"patch-{handler}", "kind": "patch", "handler": handler, "module": module,
            "group": group, "target": f"app/handlers/{module}.py", "start": path,
            "start_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "planned_effects": PLANNED[handler],
            "prompt": (f"`{signature(handler)}` in `app/handlers/{module}.py` already has an implementation, "
                       "but it may not behave as required.\n\n"
                       f"What it must do: {rows[handler]}\n\n"
                       "Read it, fix any problem you find, run the visible tests, then finish."),
        })
    return tasks, starts


def tasks_text(tasks: list[dict]) -> str:
    return "".join(json.dumps(t, sort_keys=True) + "\n" for t in tasks)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_tasks(root: Path = TASKS_DIR, spec_text: str | None = None) -> list[dict]:
    """(Re)generate tasks.jsonl and the start files. Refuses once the split is frozen."""
    root = Path(root)
    if (root / "split.json").exists():
        raise RuntimeError("the split is frozen; tasks must not change after it (delete split.json first, "
                           "and only if no baseline run has happened)")
    tasks, starts = build_tasks(spec_text)
    (root / "starts").mkdir(parents=True, exist_ok=True)
    for old in (root / "starts").glob("*.py"):
        old.unlink()
    for rel, text in starts.items():
        (root / rel).write_text(text)
    (root / "tasks.jsonl").write_text(tasks_text(tasks))
    return tasks
