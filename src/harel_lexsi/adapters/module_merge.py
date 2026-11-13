"""Merge an agent's write into a handler module instead of replacing the whole file.

Several handlers share one module (app/handlers/cart.py holds six). An agent that rewrites "its" handler
and sends the whole file back used to delete the rest, and the module stopped importing. With a merge,
a write only has to contain what the agent is implementing:

    merge_module(base, new)
      - a top-level function, class or assignment in `new` replaces the same name in `base`
        (in place, so the file order is kept), or is appended when `base` has no such name
      - imports in `new` that `base` does not already have are added after `base`'s imports
      - everything else in `base` stays exactly as it was (other handlers, helpers, comments)

The harness (ShopSandbox.write_file) and the reward (settlement_reward.final_files) both call this, so
what the agent sees and what is scored cannot drift apart. Pure Python, no model, deterministic.
"""
from __future__ import annotations

import ast


class NotPython(ValueError):
    """The written text does not parse. `args[0]` is the message the agent sees."""


def _names(node: ast.stmt) -> set[str]:
    """Names a top-level statement defines (function, class, simple assignment)."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _span(node: ast.stmt) -> tuple[int, int]:
    """First and last line (1-based, inclusive) of a statement, decorators included."""
    first = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
    return first, node.end_lineno or node.lineno


def _is_docstring(node: ast.stmt, index: int) -> bool:
    return (index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def parse(text: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as e:
        raise NotPython(f"not valid Python (line {e.lineno}): {e.msg}") from None


def defined_names(text: str) -> list[str]:
    """Top-level function/class/assignment names, in order."""
    return [n for node in parse(text).body for n in sorted(_names(node))]


def merge_module(base: str, new: str) -> str:
    """`base` with the definitions from `new` applied. Raises NotPython if `new` does not parse."""
    new_tree = parse(new)
    base_tree = ast.parse(base)
    base_lines = base.splitlines()
    new_lines = new.splitlines()

    base_by_name: dict[str, ast.stmt] = {}
    for node in base_tree.body:
        for n in _names(node):
            base_by_name[n] = node
    base_imports = {ast.dump(n) for n in base_tree.body if isinstance(n, ast.Import | ast.ImportFrom)}
    base_dumps = {ast.dump(n) for n in base_tree.body}

    replace: dict[int, tuple[int, int, list[str]]] = {}  # id(base node) -> (first, last, new lines)
    appended: list[list[str]] = []
    add_imports: list[list[str]] = []
    for i, node in enumerate(new_tree.body):
        first, last = _span(node)
        chunk = new_lines[first - 1:last]
        if isinstance(node, ast.Import | ast.ImportFrom):
            if ast.dump(node) not in base_imports:
                add_imports.append(chunk)
                base_imports.add(ast.dump(node))
            continue
        if _is_docstring(node, i):
            continue
        names = _names(node)
        hit = next((base_by_name[n] for n in sorted(names) if n in base_by_name), None)
        if hit is not None:
            b_first, b_last = _span(hit)
            replace[id(hit)] = (b_first, b_last, chunk)
        elif ast.dump(node) not in base_dumps:
            appended.append(chunk)

    out = list(base_lines)
    for b_first, b_last, chunk in sorted(replace.values(), reverse=True):  # bottom-up keeps line numbers valid
        out[b_first - 1:b_last] = chunk
    if add_imports:
        last_import = max((n.end_lineno or n.lineno for n in base_tree.body
                           if isinstance(n, ast.Import | ast.ImportFrom)), default=None)
        if last_import is None:  # no imports yet: after the module docstring, else at the top
            doc = base_tree.body[0] if base_tree.body and _is_docstring(base_tree.body[0], 0) else None
            last_import = (doc.end_lineno or doc.lineno) if doc else 0
        # replacements never change the number of lines above the import block, so the index holds
        shift = 0
        for b_first, b_last, chunk in replace.values():
            if b_last < last_import:
                shift += len(chunk) - (b_last - b_first + 1)
        at = last_import + shift
        out[at:at] = [line for chunk in add_imports for line in chunk]
    for chunk in appended:
        out += ["", "", *chunk]
    text = "\n".join(out) + "\n"
    try:
        ast.parse(text)
    except SyntaxError:  # cannot happen for well-formed input; never hand back a broken merge
        return new if new.endswith("\n") else new + "\n"
    return text
