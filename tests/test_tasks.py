"""S5: the task set is sound, nothing leaks, the split is frozen and tamper-evident."""
import ast
import json
import shutil
import sys

import psycopg
import pytest

from harel_lexsi.tasks import HANDLERS, build_tasks, check_tasks, freeze, load_tasks
from harel_lexsi.tasks import build as B
from harel_lexsi.tasks import check as C
from harel_lexsi.tasks.__main__ import main
from harel_lexsi.verifier.replay import SHOP

sys.path.insert(0, str(SHOP))
from reference import mutants, task_bugs  # noqa: E402

REAL = B.TASKS_DIR
HELDOUT = ["update_cart_item", "remove_coupon", "cancel_order", "import_shared_cart"]


@pytest.fixture
def work(tmp_path):
    """A private, unfrozen copy of the real task directory."""
    dst = tmp_path / "tasks"
    shutil.copytree(REAL, dst, ignore=shutil.ignore_patterns("split.json"))
    return dst


# ---------- the task set ----------

def test_shape():
    tasks = load_tasks(REAL)
    implement = [t for t in tasks if t["kind"] == "implement"]
    patch = [t for t in tasks if t["kind"] == "patch"]
    assert sorted(t["handler"] for t in implement) == sorted(HANDLERS)  # every handler exactly once
    assert {t["handler"] for t in patch} == {b.handler for b in task_bugs.BUGS}
    assert len(tasks) == len(implement) + len(patch) == 24
    assert len({t["id"] for t in tasks}) == len(tasks)
    assert {t["group"] for t in tasks} == {"cart", "coupons", "checkout", "orders", "share"}


def test_committed_files_are_what_the_builder_produces():
    tasks, starts = build_tasks()
    assert B.tasks_text(tasks) == (REAL / "tasks.jsonl").read_text()
    for rel, text in starts.items():
        assert (REAL / rel).read_text() == text


def test_the_checker_is_clean_on_the_unfrozen_set():
    assert check_tasks(REAL, allow_unfrozen=True) == []


def test_prompts_never_hint_at_a_bug_or_quote_a_contract():
    for t in load_tasks(REAL):
        low = t["prompt"].lower()
        for word in C.FORBIDDEN_IN_PROMPTS + ("bug", "wrong", "broken", "mistake"):
            assert word not in low, (t["id"], word)
        for b in task_bugs.BUGS:
            assert b.why not in t["prompt"] and b.new not in t["prompt"]


def test_planned_effects_come_from_the_build_section_vocabulary():
    for t in load_tasks(REAL):
        for e in t["planned_effects"]:
            resource, op = e.split(":")
            assert op in ("insert", "update", "delete", "emit") and resource
    read_only = {"get_order", "list_orders", "view_shared_cart"}
    assert {t["handler"] for t in load_tasks(REAL) if not t["planned_effects"]} == read_only


# ---------- patch start files ----------

@pytest.mark.parametrize("task", [t for t in load_tasks(REAL) if t["kind"] == "patch"], ids=lambda t: t["id"])
def test_start_file_is_self_contained_and_hands_out_no_sibling_answers(task):
    text = (REAL / task["start"]).read_text()
    tree = ast.parse(text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert task["handler"] in funcs
    for name, node in funcs.items():
        if name in HANDLERS and name != task["handler"]:
            assert "NotImplementedError" in ast.unparse(node), f"{name} must stay a stub"
    assert "NotImplementedError" not in ast.unparse(funcs[task["handler"]])
    for word in ("reference", "holdout", "mutant"):
        assert word not in text.lower()
    namespace = {}
    exec(compile(text, task["start"], "exec"), namespace)  # imports only app.errors and the standard library
    assert callable(namespace[task["handler"]])


@pytest.mark.parametrize("bug", task_bugs.BUGS, ids=lambda b: b.id)
def test_each_bug_changes_exactly_the_reference_source_once(bug):
    import importlib
    import inspect
    ref = inspect.getsource(getattr(importlib.import_module(f"reference.handlers.{bug.module}"), bug.handler))
    assert task_bugs.patched_source(bug) != ref
    assert ref.count(bug.old) == 1


# ---------- the test set stays out of the training data ----------

def test_no_patch_bug_is_a_mutant_or_a_trap():
    assert C._overlap_errors() == []


def test_the_overlap_check_has_teeth(monkeypatch):
    m = next(x for x in mutants.MUTANTS if x.id == "add-stale-qty")
    clone = task_bugs.Bug("clone", m.module, m.handler, m.old, m.new, "same as a mutant")
    monkeypatch.setattr(task_bugs, "BUGS", task_bugs.BUGS + (clone,))
    assert any("clone" in e and "V6 mutant" in e for e in C._overlap_errors())


# ---------- freezing the split ----------

def test_freeze_splits_whole_handlers_and_check_passes(work):
    split = freeze(work, HELDOUT)
    tasks = {t["id"]: t for t in load_tasks(work)}
    train = {tasks[i]["handler"] for i in split["train"]}
    held = {tasks[i]["handler"] for i in split["heldout"]}
    assert held == set(HELDOUT) and not train & held
    assert set(split["train"]) | set(split["heldout"]) == set(tasks)
    assert not set(split["train"]) & set(split["heldout"])
    for h in HELDOUT:  # a held-out handler's implement and patch tasks both stay out of training
        assert f"implement-{h}" in split["heldout"]
    assert check_tasks(work) == []


def test_freeze_refuses_bad_input_and_a_second_freeze(work):
    with pytest.raises(ValueError):
        freeze(work, ["no_such_handler"])
    with pytest.raises(ValueError):
        freeze(work, [])
    freeze(work, HELDOUT)
    with pytest.raises(RuntimeError):
        freeze(work, ["checkout"])
    with pytest.raises(RuntimeError):
        B.write_tasks(work)  # tasks cannot be rebuilt under a frozen split


def test_an_unfrozen_set_is_reported(work):
    assert any("not been frozen" in e for e in check_tasks(work))
    assert check_tasks(work, allow_unfrozen=True) == []


def test_editing_a_task_after_the_freeze_is_detected(work):
    freeze(work, HELDOUT)
    path = work / "tasks.jsonl"
    path.write_text(path.read_text().replace("Implement", "Please implement", 1))
    assert any("changed after the split was frozen" in e for e in check_tasks(work))


def test_moving_a_task_between_splits_is_detected(work):
    split = freeze(work, HELDOUT)
    moved = split["heldout"].pop()
    split["train"].append(moved)
    (work / "split.json").write_text(json.dumps(split))
    errs = check_tasks(work)
    assert any("own hash" in e for e in errs)


def test_a_leaking_split_is_detected_even_with_valid_hashes(work):
    split = freeze(work, HELDOUT)
    split["heldout"].remove("patch-cancel_order")
    split["train"].append("patch-cancel_order")  # same handler now on both sides
    split["split_sha256"] = C._split_hash(split)
    (work / "split.json").write_text(json.dumps(split))
    assert any("held-out handlers also appear in training" in e for e in check_tasks(work))


def test_editing_a_start_file_after_the_freeze_is_detected(work):
    freeze(work, HELDOUT)
    start = work / "starts" / "patch-checkout.py"
    start.write_text(start.read_text() + "\n# tweak\n")
    assert any("start file changed" in e for e in check_tasks(work))


def test_a_start_file_that_gives_away_a_sibling_is_detected(work):
    start = work / "starts" / "patch-add_to_cart.py"
    text = start.read_text()
    start.write_text(text.replace("def update_cart_item", "def update_cart_item_x", 1))  # renames a stub away
    errs = check_tasks(work, allow_unfrozen=True)
    assert any("start file changed" in e for e in errs)


def test_cli_exit_codes(work, capsys):
    assert main(["--dir", str(work), "check"]) == 1                      # unfrozen
    assert main(["--dir", str(work), "check", "--allow-unfrozen"]) == 0
    assert main(["--dir", str(work), "freeze", "--heldout", ",".join(HELDOUT)]) == 0
    assert main(["--dir", str(work), "check"]) == 0
    assert main(["--dir", str(work), "freeze", "--heldout", "checkout"]) == 2   # already frozen
    assert main(["--dir", str(work), "build"]) == 2                          # refuses under a frozen split


# ---------- is every task scorable? (needs Postgres) ----------

SPEC = """## Build
x

## Resources
| `carts` | id | clear_cart | no |

## Holdout

```yaml
contracts:
  - id: {cid}
    handler: clear_cart
    given: cart_notebook_x2_save10
    input: {{ user_id: 1 }}
    effects:
      - {{ id: e1, resource: cart_items, op: delete, count: 1, match: {{ product_id: 1 }} }}
{extra}    closure: true
```
"""
COUPON_EFFECT = ("      - { id: e2, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, "
                 "change: { coupon_code: null } }\n")


@pytest.fixture
def postgres():
    try:
        psycopg.connect("postgresql://shop:shop@localhost:5433/shop", connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run: cd shop && docker compose up -d")


def scorable(work, tmp_path, extra):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC.format(cid="T-cc", extra=extra))
    return C._scorable_errors(work, load_tasks(work), spec)


def test_a_strong_contract_makes_a_patch_task_scorable(postgres, work, tmp_path):
    errs = scorable(work, tmp_path, COUPON_EFFECT)
    assert not any("patch-clear_cart" in e for e in errs)


def test_a_weak_contract_leaves_the_bug_uncaught(postgres, work, tmp_path):
    errs = scorable(work, tmp_path, "")  # never says the coupon must go
    assert any("patch-clear_cart" in e and "could not be scored" in e for e in errs)


def test_handlers_without_any_contract_are_listed(postgres, work, tmp_path):
    errs = scorable(work, tmp_path, COUPON_EFFECT)
    assert "no holdout contract covers checkout" in errs
    assert "no holdout contract covers clear_cart" not in errs


def test_the_committed_split_is_valid_once_it_exists():
    if not (REAL / "split.json").exists():
        pytest.skip("the split is not frozen yet (freeze after S1 is merged)")
    assert check_tasks(REAL) == []
