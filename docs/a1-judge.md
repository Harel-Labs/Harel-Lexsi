# A1: LLM-judge reward baseline

The "standard LLM judge" that Harel is compared against (issue #34). It scores the same 24 trajectories as the
T2 baseline (`docs/t2-baseline.md`). Per-task scores: `docs/a1-judge-run/` (`judge_scores.jsonl`,
`judge_summary.json`, `judge_summary.md`). Run it with `scripts/judge_run.py RUN_DIR`.

## Setup

| | |
|---|---|
| judge | AgentTune's `LLMJudge` (its prompt builder and `JudgeScore`), as `HostedJudge` in `adapters/judge.py` |
| model | `CohereLabs/c4ai-command-a-03-2025` (111B) on the HF router, temperature 0 |
| prompt | `adapters/judge_prompt_v1.txt` plus the shared rules of the spec's `## Build` section; rubric sha256 `4ba141885646…` |
| sees | the task, the shared rules, the trajectory (each step's code and test results) |
| never sees | the holdout contracts, the reference handlers, the verifier's verdicts (a test checks the prompt) |
| scored | 24 trajectories of `runs/hosted-tiny-aya-v3-t09-20261114`; no call failed |

Two deviations from AgentTune's stock judge, on purpose: a failed call or a reply that is not strict JSON with a
score in [0, 1] is **UNKNOWN**, not 0.0 (stock turns both into 0.0, which reads as "rejected" and would flatter
the judge on the broken-mutant count in A2); and the call uses temperature 0 with a bounded retry.

## Result

| judge score | n | verifier PASS | verifier FAIL |
|---|---|---|---|
| 1.00 | 4 | 4 | 0 |
| 0.50 | 9 | 0 | 9 |
| 0.00 | 11 | 0 | 11 |

- The four trajectories the judge scored 1.0 are exactly the four the verifier passed. Nothing it scored below
  1.0 passed verification.
- Of the 7 tasks that pass their visible tests and fail verification, the judge scored none 1.0. It often named
  the real defect (for `implement-share_cart`: "fails to publish the `cart.shared` event", which is the
  verifier's `missing_event`).
- **Where the approval line is drawn changes the headline.** The line was fixed at 0.5 before the first run,
  but the rubric calls 0.5 "mostly right, with real gaps", so at that line the judge "approves" 13 of 24 (9 of
  them with a defect it named). At 1.0 it approves 4 of 24 with no false approval. That is a flaw in how the
  line was chosen, not a finding: report the table above, not one number.

## What this does and does not show

- On these 24 trajectories a strong judge agrees with the verifier at the strict line. **This does not support
  "the verifier beats an LLM judge" yet.** The claim is about broken mutants (A2), where the judge sees code
  that passes the visible tests by design. A2 must fix its approval line before running.
- n = 24, one sample per task, judge and builder both hosted. The trajectories include the visible-test results,
  which is what a judge reward would see in training, so the judge has more evidence than the code alone.
- The judge is a different, much larger model than the builder (Tiny Aya 3B). Cost here: about 50 s for 24 calls.
