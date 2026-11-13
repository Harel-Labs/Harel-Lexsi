# Factory governance (G workstream)

The "factory" is what L1 is given: its prompt and its memory. One round of governance changes it once, says
what it expects, and lets the verifier decide whether it helped.

```
G1  factory/versions/v0001.json ...   frozen versions; every run and step marker carries the version id
G2  factory/proposals/p0001.json ...  one change + the expected effect, frozen BEFORE the A/B run
G3  A/B on the held-out tasks         current vs candidate, scored by the verifier; promotes or rejects
G4  one factory-graph row             trigger, diff, expected, observed, decision, built from the records above
```

## G2 in one minute

1. Run the baseline over the **training** split too (`scripts/baseline.py --split all`), so
   `summary.json` has a `train` block with failure classes.
2. `uv run python -m harel_lexsi.factory init --prompt-file <L1 prompt>` once, to archive the first version.
3. `uv run python -m harel_lexsi.factory propose --summary runs/<run>/summary.json`

If a failure class occurred in at least 3 training episodes (`--min-count`), the governor appends one fixed
lesson for that class to L1's memory, as a **candidate** version (not promoted), and writes the proposal.
Otherwise it says there is nothing to change.

## Rules the governor cannot break

- It reads only the **training** block of the summary, never the held-out split (that is the G3 benchmark).
- It can only change `prompt` or `memory`. Its text may not mention the verifier, contracts, benchmark,
  thresholds or any protected path, and it imports no verifier code except the failure-class names.
- The proposal is created exclusively, read-only, with a SHA-256. Editing it afterwards is detected.
- One round: asking again for the same base version and failure class returns the same proposal.

## What G3 and G4 read

A proposal has `base_version`, `candidate_version`, `trigger` (class, episodes, of, split), `change`
(field, added_text) and `expected_effect`:

```json
{"judged_on": "heldout", "expectations": [
  {"metric": "episodes_with_failure_class", "failure_class": "<class>", "direction": "decrease"},
  {"metric": "verified_success", "direction": "not_decrease"}]}
```

G3 loads it with `ProposalStore.get(id)` (which raises if it is missing or altered), writes its own win rule
before running, runs both versions on the same held-out tasks, and promotes the candidate only if it wins.
G4 renders the row from the proposal, the two summaries and G3's decision; nothing is typed by hand.
