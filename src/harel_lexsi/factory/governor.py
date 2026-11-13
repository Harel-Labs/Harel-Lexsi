"""G2: the factory governor. It proposes ONE change to L1's prompt or memory, and says what it expects first.

Pure rules, no model and no file access here (the stores that write records are passed in, see proposals.py).

  Trigger    a failure class that repeats in the TRAINING episodes of a baseline summary
  Change     one fixed lesson, written from the class name alone, appended to L1's memory
  Guardrails the governor can only touch `prompt` or `memory`, and its text may never mention the verifier,
             the contracts, the benchmark or the thresholds

It never reads the held-out split, so the benchmark that G3 uses to judge the change stays clean.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from harel_lexsi.factory.proposals import Proposal, ProposalStore
from harel_lexsi.factory.versions import FactoryVersion, VersionStore
from harel_lexsi.verifier.types import FailureClass

DEFAULT_MIN_COUNT = 3
MAX_CHANGE_CHARS = 400
EDITABLE_FIELDS = ("prompt", "memory")

# Tie-break order when two classes repeat equally often: the order FailureClass defines them in.
CLASS_ORDER = tuple(c.value for c in FailureClass)

# Generic coding advice, derived from the class name alone (the V8 rule: a class and a step, never contract
# content). No table, column or event name appears in them.
LESSONS: dict[str, str] = {
    "undeclared_write": ("Change only what the task description says to change. Do not write to anything "
                         "else, even if it seems helpful."),
    "missing_write": "Before you finish, check that every change the description lists is actually made.",
    "missing_event": ("When the description says to publish an event, add it in the same transaction as the "
                      "change it describes."),
    "broken_atomicity": ("Do all the changes of one call inside a single transaction, so that either all of "
                         "them happen or none do."),
    "wrong_cardinality": ("Check how many records each change touches: one change per record the description "
                          "names, no more and no fewer."),
    "wrong_final_state": ("Check the values you store: quantities, totals and status flags must match the "
                          "description exactly."),
}

# Things the governor must never mention or touch: the verifier, the contracts, the benchmark, the thresholds.
_PROTECTED = re.compile(
    r"holdout|contracts?\b|thresholds?|benchmark|verifier|settle|mutants?|\bgate\b|\bsplit\b|"
    r"\breference\b|spec/|tasks/|shop/|src/|docs/|\.json|\.yaml",
    re.IGNORECASE,
)


class GuardrailError(ValueError):
    """The governor tried to do something it is not allowed to do."""


@dataclass(frozen=True)
class Trigger:
    failure_class: str
    episodes: int          # training episodes in which the class occurred
    of: int                # training episodes in total
    split: str = "train"   # always train; the held-out split is the benchmark and is never read


@dataclass(frozen=True)
class Change:
    field: str             # prompt | memory
    added_text: str


def choose_trigger(summary: Mapping, *, min_count: int = DEFAULT_MIN_COUNT) -> Trigger | None:
    """The failure class to react to, or None when nothing repeats enough.

    `summary` is the output of baseline.summarise(); only its "train" block is read. A class is a trigger when
    at least `min_count` training episodes failed with it. The most frequent wins; a tie goes to the class
    that comes first in CLASS_ORDER, so the choice is deterministic.
    """
    if min_count < 1:
        raise ValueError("min_count must be >= 1")
    train = summary["train"] if "train" in summary else {}
    counts = train.get("failure_classes") or {}
    repeated = {c: n for c, n in counts.items()
                if c in CLASS_ORDER and isinstance(n, int) and not isinstance(n, bool) and n >= min_count}
    if not repeated:
        return None
    top = max(repeated.values())
    chosen = next(c for c in CLASS_ORDER if repeated.get(c) == top)
    return Trigger(failure_class=chosen, episodes=top, of=int(train.get("n", 0)))


def validate_change(change: Change) -> Change:
    """Refuse anything but a short text change to the prompt or memory that stays away from the checkers."""
    if change.field not in EDITABLE_FIELDS:
        raise GuardrailError(f"the governor may only change {EDITABLE_FIELDS}, not {change.field!r}")
    text = change.added_text.strip()
    if not text:
        raise GuardrailError("an empty change is not a change")
    if len(text) > MAX_CHANGE_CHARS:
        raise GuardrailError(f"a change may be at most {MAX_CHANGE_CHARS} characters")
    hit = _PROTECTED.search(text)
    if hit:
        raise GuardrailError(f"a change may not mention {hit.group(0)!r}: that is off limits to the governor")
    return Change(change.field, text)


def lesson_for(failure_class: str) -> Change:
    """The one change the governor makes for a failure class (already checked by the guardrails)."""
    if failure_class not in LESSONS:
        raise GuardrailError(f"no lesson for failure class {failure_class!r}")
    return validate_change(Change("memory", LESSONS[failure_class]))


def expected_effect(failure_class: str) -> dict:
    """Written BEFORE the A/B run, so the result cannot be read into it afterwards. Judged on the held-out
    benchmark by G3, which writes its own win rule before it runs."""
    return {
        "judged_on": "heldout",
        "expectations": [
            {"metric": "episodes_with_failure_class", "failure_class": failure_class, "direction": "decrease"},
            {"metric": "verified_success", "direction": "not_decrease"},
        ],
    }


def apply_change(base: FactoryVersion, change: Change) -> tuple[str, str]:
    """(prompt, memory) of the candidate: the base with the change appended. Anything else is untouched."""
    change = validate_change(change)
    if change.field == "prompt":
        joined = (base.prompt.rstrip() + "\n" if base.prompt.strip() else "") + change.added_text
        return joined, base.memory
    joined = (base.memory.rstrip() + "\n" if base.memory.strip() else "") + "- " + change.added_text
    return base.prompt, joined


def propose(versions: VersionStore, proposals: ProposalStore, summary: Mapping, *,
            min_count: int = DEFAULT_MIN_COUNT) -> Proposal | None:
    """One round: react to the repeated failure class, if any, with one change and its expected effect.

    Creates a candidate factory version (parent = the current version) and a frozen proposal record. The
    candidate is NOT promoted: only the A/B result (G3) may do that. Asking again for the same base version and
    failure class returns the existing proposal and creates nothing, so there is one honest round, not a search.
    Raises NoCurrentVersion if no factory version has been promoted yet.
    """
    trigger = choose_trigger(summary, min_count=min_count)
    if trigger is None:
        return None
    base = versions.current()
    existing = proposals.find(base.id, trigger.failure_class)
    if existing is not None:
        return existing
    change = lesson_for(trigger.failure_class)
    prompt, memory = apply_change(base, change)
    candidate = versions.create(
        prompt, memory, parent=base.id,
        note=f"G2: {trigger.failure_class} in {trigger.episodes} of {trigger.of} training episodes",
    )
    return proposals.create(
        base_version=base.id, candidate_version=candidate.id, trigger=asdict(trigger),
        change=asdict(change), expected_effect=expected_effect(trigger.failure_class),
    )
