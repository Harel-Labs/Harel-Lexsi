# T3: first short GRPO run, and the switch to T8

Issue #24. **Decision (2026-11-14): switch to T8 (#29), DPO/BCO on verified pairs.** GRPO works end to end,
but a step takes about 5 minutes on any GPU we tried, so a run long enough to move the reward does not fit
the time left. The plan's own rule for T3 is "no change after the run: switch to T8".

## The run

| | |
|---|---|
| job | HF Jobs `6af8049d29dd05ff03668cbc`, `a100-large` (A100-SXM4-80GB), 2026-11-14 06:50 to 07:11 UTC |
| model | `CohereLabs/tiny-aya-global`, LoRA r=16, alpha=32, all-linear |
| data | the 16 train tasks only (frozen split, sha `5c98b1b98721…`); the held-out split is never read |
| GRPO | 3 steps, 4 generations per prompt, micro-batch 1, gradient accumulation 4, lr 2e-5, temperature 0.9 |
| episode | at most 3 turns; the model writes a ```python block, gets the visible-test result, may repair |
| reward | `harel_settlement_reward` on `spec/spec.md`: the verifier's verdict on the final files |
| harness | the T2 baseline's prompt and rules (`train_rollout`), so training and measurement match |
| code | `scripts/train_t3.py`, launched with `scripts/t3_job.py train` (entry `scripts/job_entry.py`) |

## Result

| step | prompt (4 samples) | rewards | mean | grad norm | step time |
|---|---|---|---|---|---|
| 1 | checkout | −0.92 −0.92 −0.83 −0.92 | −0.90 | 0.076 | 305 s |
| 2 | list_orders | −1.00 ×4 | −1.00 | 0 | 311 s |
| 3 | apply_coupon | −1.00 −0.71 −1.00 −1.00 | −0.93 | 0.058 | 306 s |

- **Step time (the T3 measurement): about 5 minutes on an A100**, and about 4.5 minutes on an L40S in an earlier
  attempt. A bigger GPU does not help: rollouts run one after another through Hugging Face `generate` on a
  LoRA-wrapped model, which is limited by per-token overhead. 100 steps would take about 8.5 hours.
- **Reward did not move.** Three steps on three different prompts cannot show a trend either way. The updates
  were tiny (KL about 6e-5); step 2 had no signal at all because all four samples failed the same way.
- 11 of 12 episodes failed completely.

## What the log shows, for T8 and any later GRPO run

1. **Invalid Python in 5 of 12 episodes.** Every write in those episodes was refused (`files=[]`), so the
   verifier scored the untouched stub. The refusal messages were not logged; log them next time.
2. **The loss sees only the start of an episode.** Every step reports completions clipped at 1,024 tokens: an
   episode's turns are joined into one sequence, so the repair turns are mostly cut off from training.
3. **Most prompts give identical failures.** GRPO needs prompts where some samples pass. In T2 only 4 of 24
   tasks verified. Training prompts should favour tasks the model sometimes solves (mostly the patch tasks).

## Getting the job to run (bugs found on cheap CPU jobs and fixed)

- Postgres in the container with `synchronous_commit=off`: logical decoding missed commits, the capture test
  failed. Removed.
- The verifier's scorer runs in a subprocess with a clean environment: `harel_lexsi` must be installed, not just
  on `PYTHONPATH`, or every reward is UNKNOWN (0.0) and GRPO looks flat for the wrong reason.
- Libraries installed by the job are not importable in the same process: the training script runs in a fresh one.
- The code volume is read-only: it is copied to a writable folder before editable installs.
- Out of memory at the backward pass with micro-batches of 4 (Tiny Aya's vocabulary is 262k): micro-batch 1.
- The full test suite passed in the container (390 passed, 1 skipped, 1 xfailed) before the GPU runs.

Compute spent: about $2 in total (the CPU jobs cost cents; two failed L40S runs and this A100 run).

## Why T8 next

T8 trains offline on preference pairs, so the slow part (generation) can run on the hosted endpoint, which is
fast: many samples per task, each scored by the verifier, a passing and a failing sample for the same task make
a pair. The DPO run itself is short. The reward source stays the verifier, as #29 requires. GRPO stays possible
later if generation gets faster (vLLM), and the training wiring here is reusable for it.
