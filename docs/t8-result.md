# T8 result: DPO on verifier-labelled pairs did not raise held-out verified success

Issues #29 (T8), #26 (T5), #27 (T6), #32 (G3), #37 (A4). Rules: `docs/t8-plan.md`, committed before any trained model
produced an answer. Data: `docs/t8-run/` (per-episode rows, summary, gate decision, adapter provenance).

## Decision

**KEEP THE OLD MODEL.** The gate (`factory/deploy_gate.py`, thresholds file unchanged) says: held-out gain +0% is below the
required +10%. The trained adapter is not deployed. This is the answer the pre-written rule gives, and it is reported as
reached.

## The measurement

Base and trained models, served by the same code (`scripts/serve_hf.py`, the trained one with the adapter merged into the
pinned weights), played by the same harness and settings (temperature 0.9, top-p 0.75, 768 tokens a turn, at most 8 steps).
75 episodes per model: the 8 held-out tasks and the 7 train patch tasks, 5 samples each. Samples are not seeded: both models
get the same prompts and settings, not the same random draws. A task scores the share of its samples the verifier passes.

| set | tasks (n) | base | trained | change | p (paired bootstrap over tasks) |
|---|---|---|---|---|---|
| held-out, verified success | 8 | 12% | 12% | +0% | 1.000 |
| mutation set (patch tasks), verified success | 11 | 35% | 35% | +0% | 1.000 |
| held-out, visible tests pass | 8 | 45% | 55% | +10% | 0.075 |
| mutation set, visible tests pass | 11 | 75% | 82% | +7% | 0.256 |

| writes refused as not Python | base | trained |
|---|---|---|
| held-out (40 episodes each) | 14% | 5% |
| mutation set (55 episodes each) | 12% | 8% |

Per held-out task (verified, 5 samples): `patch-cancel_order` 5/5 for both models; the other seven held-out tasks 0/5 for both
(`implement-cancel_order`, `implement-import_shared_cart`, `implement-remove_coupon`, `implement-update_cart_item`,
`patch-import_shared_cart`, `patch-remove_coupon`, `patch-update_cart_item`). Mutation-set moves: `patch-apply_coupon` 60% to
100%, `patch-clear_cart` 60% to 80%, `patch-add_to_cart` 40% to 20%, `patch-get_order` 100% to 80%, `patch-share_cart` 20% to 0%;
at 5 samples one episode is 20 points, so these gains and losses cancel and look like noise.

## What it shows

1. **No detectable gain in verified success.** Held-out: 5 of 40 episodes pass for both models, all of them on one task
   (`patch-cancel_order`), which both models solve every time.
2. **The training moved the proxy, not the truth.** Held-out visible-test passes rose from 18 to 22 of 40 episodes
   (`patch-import_shared_cart` 4 to 5, `patch-remove_coupon` 3 to 5, `implement-remove_coupon` 1 to 2) while verified passes did
   not move. Over all 75 episodes, answers that pass the visible tests but fail the verifier went from 23 to 28. That is the
   gap this project exists to expose: the trained model got better at satisfying the visible tests, not better at being right.
3. **The pairs mostly taught the easy part.** Only 4% of the training pairs were hard negatives (a rejected answer that passes
   the visible tests and fails the verifier); most rejected answers also failed the visible tests, or were not valid Python.
   DPO therefore had little reason to learn what the hidden contracts add. Invalid Python fell (14% to 5% of held-out writes),
   which fits.
4. **Unseen handlers are out of reach for this model.** Held-out `implement` tasks: 0 verified out of 20 episodes for both models.

## Limits of this result

- **The held-out measure has almost no room to move.** Seven of the eight tasks are at 0% for the base model, and the eighth is
  at 100%. A +10 point rule needs about one more task to flip. "No detectable improvement" is what was measured; it is not
  proof that no training could help. With n = 8 tasks and 5 samples each, significance was never likely.
- The mutation-set check includes 7 train patch tasks the model saw in training, so it can show a regression, not
  generalisation.
- The training data came from the same model (on-policy), at one sampling setting: 270 episodes, 842 distinct answers, 52 that
  pass the verifier (41 of them also pass their visible tests), 333 pairs from 10 of 16 train tasks (the strict rule; the
  verifier-only rule gives 373 pairs from 11). Five train tasks produced no passing answer and contributed nothing.
- DPO hyperparameters were set once (lr 2e-5, beta 0.1, 2 epochs, LoRA r=16) and not tuned.
- Evaluation sampling is not seeded, so a rerun would move individual tasks by an episode or two.

## What was done, for the record

| step | where | result |
|---|---|---|
| speed probe | A100 | HF generate 30 tok/s for one request, 115 tok/s for a batch of 4 in the same time; vLLM 0.33 installed but did not load the model |
| collection | A100, batching server, 12 workers | 270 episodes in 100 minutes; unrestricted sampling gave 53% invalid Python in the pilot, top-p 0.75 gave 13% |
| pairs | CPU job | verifier rule 373 pairs, strict rule 333 (the winning answer must also pass its visible tests) |
| DPO training | A100, 30 minutes | loss 0.69 to about 0.05; eval loss 0.236 then 0.153; 93% of held-back pairs ranked right |
| evaluation | A100, about an hour (29 minutes for the base model, 20 for the trained one, plus setup) | the table above |

Compute spent on the T8 chain: about $11, and about $13 for the project to date, of a $20 cap (my estimate from job run times and the published prices, not a billing figure).

## What would change the answer

A second round aimed at the gap in finding 3: pairs in which chosen and rejected both pass the visible tests and differ only on
the verifier, more samples on the tasks the base model sometimes solves, and a larger held-out measure (more samples per task, or
more held-out tasks, which means a new frozen split). That is a new experiment with its own rules written first, not a rerun of
this one.
