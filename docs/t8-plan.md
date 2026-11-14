# T8: rules written before any trained model is judged

Written 2026-11-15, after the training data was collected and before any trained model has generated a single
answer. Committed first so that T6 (#27, "thresholds written before the run") and G3 (#32, "win rule written before
the run") mean something. If a rule below has to change after results exist, the change is made in a new commit that
says why, and the result states that the rule moved.

## What is being tested

Does a LoRA adapter, trained with DPO on preference pairs that the Harel verifier labelled, make the builder solve
more tasks it has never seen? The builder is `CohereLabs/tiny-aya-global` (revision `af89d219…`), the same model as
the T2 baseline. Tasks are the frozen set (`tasks/split.json`, sha `5c98b1b98721…`). The verifier and spec are the
frozen ones (`spec/spec.md`).

## Training data (already collected)

`scripts/t8_collect.py`: the pinned weights behind a batching server, 270 episodes over the 16 TRAIN tasks (24 planned,
stopped at a 100-minute cap, sampling interleaved across tasks), top-p 0.75, temperature 0.9, 768 tokens a turn. 842
distinct answers, scored by the verifier: 52 PASS, 790 FAIL. Pairs: chosen = a PASS, rejected = a FAIL for the same
task, hardest negatives first, at most 40 per task. The held-out tasks are never sampled, never in a pair, never in
a training prompt (checked twice in code, `pairs.assert_train_only`).

## The measurement

Base and trained models are measured in one GPU job, with the same serving code (`scripts/serve_hf.py`, the trained
one with the adapter merged), the same harness (`adapters/hosted_rollout.EpisodeRunner`) and the same settings
(temperature 0.9, top-p 0.75, 768 tokens a turn, at most 8 steps). Samples are not seeded, so the two models see the
same prompts and the same settings, not the same random draws.

| set | tasks | samples each | role |
|---|---|---|---|
| **held-out** | the 8 held-out tasks (4 implement, 4 patch) | 5 | the decision (T6, G3, A4) |
| **mutation set** | the 11 patch tasks: 4 held-out (also in the row above) and 7 train | 5 | "no regression" (T6) |

A task's score is the share of its samples whose final files the verifier PASSES (every contract holds). The set score is
the mean over its tasks. Visible-test pass rate and the share of writes that were not valid Python are reported beside
it for both models.

**What "mutation set" means here.** V7's mutants test the verifier (it caught 19 of 19) and training cannot change
that, so re-running V7 would say nothing about the model. The mutation-derived tasks the builder actually faces are the
patch tasks (a reference handler with one injected bug, `shop/reference/task_bugs.py`). "No regression on the mutation
set" is therefore the trained model's verified rate on those 11 tasks, before and after. Seven of the 11 were seen in
training, so this check can show the model got worse at fixing bugs, but not that it generalises; the held-out row does that.

## The win rule (T6 and G3)

The trained model **wins** only if both hold:

1. held-out verified success rises by at least **+0.10** absolute (`deploy_thresholds.json`: `min_heldout_gain`), with
   n = 8 tasks (at least `min_heldout_n` = 6);
2. the mutation-set verified rate does **not fall** (`max_mutation_regression` = 0.0), with n = 11 (at least 6).

Otherwise the decision is **keep the old model**. Significance is not required (`require_significance: false`), because
n = 8 tasks cannot reach it unless the effect is huge. It is still computed (A4, paired bootstrap over tasks) and shown
beside every number, with n, and the write-up says plainly when the sample is too small to separate a real gain from noise.

`deploy_thresholds.json` is committed unchanged; the gate records its sha256 in the decision.

## A4

Table for the held-out tasks: base rate, trained rate, difference, the paired-bootstrap interval over tasks (`compare_runs`),
and n beside every number.

## What would not count

- A gain on the 16 train tasks (the model saw them).
- A gain that appears only when invalid Python is counted as UNKNOWN. Invalid Python is a failure, as in T2.
- A rise on held-out that comes with a fall on the mutation set. That is "keep the old model".

## Known weaknesses, stated now

- Eleven of 16 train tasks have both a PASS and a FAIL, so five contribute no pairs (the model never solved them).
- Only 52 distinct passing answers exist; one task's passes may repeat across many pairs.
- Samples come from the same model that is trained (on-policy), at one sampling setting.
- n = 8 held-out tasks, 5 samples each: one task moving changes the headline by about 6 points.
