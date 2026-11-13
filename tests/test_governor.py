"""G2 (part 1): which failure class triggers a change, what the change says, and what the governor may never do."""
import ast
import re
from pathlib import Path

import pytest

from harel_lexsi.factory import governor as G
from harel_lexsi.verifier import FailureClass, load_binding
from harel_lexsi.verifier.replay import ROOT


def summary(train_classes=None, n=16, **other_blocks):
    out = {"train": {"n": n, "failure_classes": train_classes or {}}}
    out.update(other_blocks)
    return out


# ---------- the trigger ----------

def test_a_class_must_repeat_enough_to_trigger():
    assert G.choose_trigger(summary({"broken_atomicity": 2})) is None
    t = G.choose_trigger(summary({"broken_atomicity": 3}))
    assert t == G.Trigger("broken_atomicity", episodes=3, of=16, split="train")


def test_the_most_frequent_class_wins():
    t = G.choose_trigger(summary({"missing_event": 4, "wrong_final_state": 7, "missing_write": 3}))
    assert (t.failure_class, t.episodes) == ("wrong_final_state", 7)


def test_ties_go_to_the_class_that_comes_first_in_the_fixed_order():
    classes = {c: 5 for c in G.CLASS_ORDER}
    assert G.choose_trigger(summary(classes)).failure_class == "undeclared_write"
    assert G.choose_trigger(summary({"wrong_final_state": 5, "missing_event": 5})).failure_class == "missing_event"


def test_the_choice_is_deterministic():
    s = summary({"missing_event": 4, "missing_write": 4})
    assert len({G.choose_trigger(s) for _ in range(20)}) == 1


def test_min_count_is_configurable_and_validated():
    s = summary({"missing_write": 1})
    assert G.choose_trigger(s, min_count=1).failure_class == "missing_write"
    with pytest.raises(ValueError):
        G.choose_trigger(s, min_count=0)


def test_unknown_classes_and_odd_counts_are_ignored_not_trusted():
    s = summary({"made_up_class": 99, "missing_write": True, "missing_event": "9", "wrong_cardinality": 3})
    assert G.choose_trigger(s).failure_class == "wrong_cardinality"


def test_no_summary_content_means_no_proposal():
    assert G.choose_trigger({}) is None
    assert G.choose_trigger({"train": {}}) is None
    assert G.choose_trigger(summary()) is None


def test_only_the_training_block_is_ever_read():
    """The held-out split is the G3 benchmark; 'all' contains it too. Touching either would leak it."""
    class OnlyTrain(dict):
        def _check(self, key):
            assert key == "train", f"the governor read {key!r}"

        def __getitem__(self, key):
            self._check(key)
            return super().__getitem__(key)

        def __contains__(self, key):
            self._check(key)
            return super().__contains__(key)

        def get(self, key, default=None):
            self._check(key)
            return super().get(key, default)

        def __iter__(self):
            raise AssertionError("the governor iterated over the whole summary")

    s = OnlyTrain(summary({"missing_write": 3}, heldout={"failure_classes": {"missing_event": 99}},
                          all={"failure_classes": {"missing_event": 99}}))
    assert G.choose_trigger(s).failure_class == "missing_write"


def test_heldout_failures_never_cause_a_proposal():
    s = summary({}, heldout={"n": 8, "failure_classes": {"broken_atomicity": 8}})
    assert G.choose_trigger(s) is None


# ---------- the change ----------

def test_every_failure_class_has_a_lesson_and_only_those():
    assert set(G.LESSONS) == {c.value for c in FailureClass} == set(G.CLASS_ORDER)


@pytest.mark.parametrize("failure_class", G.CLASS_ORDER)
def test_each_lesson_is_a_valid_short_memory_change(failure_class):
    change = G.lesson_for(failure_class)
    assert change.field == "memory"
    assert 0 < len(change.added_text) <= G.MAX_CHANGE_CHARS
    assert G.validate_change(change) == change


def test_lessons_name_no_table_and_no_contract_content():
    names = set(load_binding(ROOT / "spec" / "binding.yaml").mapping) | {"coupon", "stock", "cart", "order"}
    for failure_class, text in G.LESSONS.items():
        low = text.lower()
        assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", low)], failure_class


def test_an_unknown_class_has_no_lesson():
    with pytest.raises(G.GuardrailError):
        G.lesson_for("made_up_class")


# ---------- the guardrails ----------

@pytest.mark.parametrize("field", ["verifier", "contracts", "benchmark", "thresholds", "spec", "tasks", "", "Memory"])
def test_only_prompt_and_memory_can_be_changed(field):
    with pytest.raises(G.GuardrailError, match="only change"):
        G.validate_change(G.Change(field, "Check your work."))


@pytest.mark.parametrize("field", G.EDITABLE_FIELDS)
def test_prompt_and_memory_are_allowed(field):
    assert G.validate_change(G.Change(field, "  Check your work.  ")).added_text == "Check your work."


@pytest.mark.parametrize("text", [
    "Read the holdout contracts first.",
    "Raise the threshold so more runs pass.",
    "Edit the verifier to accept this.",
    "Tune yourself on the benchmark tasks.",
    "Change spec/spec.md before you start.",
    "Write to tasks/split.json.",
    "Look in shop/reference for the answer.",
    "Mark mutants as passing.",
    "Open the gate.",
])
def test_text_that_reaches_for_the_checkers_is_refused(text):
    with pytest.raises(G.GuardrailError, match="off limits"):
        G.validate_change(G.Change("memory", text))


def test_empty_or_oversized_changes_are_refused():
    for text in ("", "   \n"):
        with pytest.raises(G.GuardrailError, match="empty"):
            G.validate_change(G.Change("memory", text))
    with pytest.raises(G.GuardrailError, match="at most"):
        G.validate_change(G.Change("memory", "a" * (G.MAX_CHANGE_CHARS + 1)))


# ---------- structural: the module cannot reach the checkers ----------

GOVERNOR = Path(G.__file__)
ALLOWED_IMPORTS = {"__future__", "re", "collections.abc", "dataclasses", "harel_lexsi.verifier.types",
                   "typing", "harel_lexsi.factory.versions", "harel_lexsi.factory.proposals"}


def test_the_governor_imports_only_read_only_types_and_its_own_stores():
    tree = ast.parse(GOVERNOR.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_the_governor_module_does_no_file_or_process_access():
    tree = ast.parse(GOVERNOR.read_text())
    banned_calls = {"open", "exec", "eval", "compile", "__import__"}
    banned_attrs = {"write_text", "write_bytes", "unlink", "rename", "replace", "rmtree", "system", "run", "Popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            assert not (isinstance(f, ast.Name) and f.id in banned_calls), f.id
            assert not (isinstance(f, ast.Attribute) and f.attr in banned_attrs), f.attr
