# T2 baseline: untrained Tiny Aya on the 24 tasks

Run: `runs/hosted-tiny-aya-v3-t09-20261114` (2026-11-14). The per-task results and summary are copied to
`docs/t2-baseline-run/`. The trajectories (0.9 MB) stay in `runs/`, which is not committed.

## Result

| set | n | verified (Harel) | visible tests pass | gap |
|---|---|---|---|---|
| train | 16 | 3 (19%) | 7 (44%) | +25 pts |
| held-out | 8 | 1 (12%) | 4 (50%) | +38 pts |
| all | 24 | 4 (17%) | 11 (46%) | +29 pts |
| implement | 13 | 1 (8%) | 3 (23%) | +15 pts |
| patch | 11 | 3 (27%) | 8 (73%) | +45 pts |

- **Verified** means every hidden contract for the task's handler settles PASS. Nothing was UNKNOWN,
  no episode crashed or overflowed the context, and no tool call was malformed.
- **Visible tests pass** means every visible test that calls the task's handler passes.
- **The gap is the point of the project.** Seven tasks pass their visible tests and fail verification.
  Examples: `implement-share_cart` passes both visible tests and never publishes its `cart.shared`
  event (`missing_event`); `implement-clear_cart` returns the right value and changes things it must
  not (`broken_atomicity`, `undeclared_write`).
- Verified passes: `implement-get_order`, `patch-checkout`, `patch-get_order`, `patch-cancel_order`
  (held-out).
- Failure classes across the 20 failed tasks: `wrong_final_state` 17, `missing_write` 11,
  `undeclared_write` 7, `missing_event` 3, `broken_atomicity` 1.
- Tool calls (A5): 111 calls, 6 wrong (5.4%), 0 redundant. AuditKIT loads the run (A3): 24 episodes.

## How it was measured

| | |
|---|---|
| model | `CohereLabs/tiny-aya-global` (3.35B), base weights, no training |
| where | Hugging Face Inference Providers (served by Cohere), `scripts/baseline.py --backend api` |
| sampling | temperature 0.9, one sample per task, at most 8 steps, 1024 new tokens per turn |
| scoring | `spec/spec.md` Holdout contracts, spec sha256 `2e3720f63a46…`; split sha256 `5c98b1b98721…` |
| harness | system prompt (`builder_prompt`), merge writes, focused visible tests, `EpisodeCoach`, stop on green |

## Read these numbers with care

1. **Hosted, not the pinned weights.** The plan pins revision `af89d219…` and runs locally. The hosted
   copy is not pinned to a revision. A local run on the Mac was not possible: the model exhausts 24 GB
   on MPS. A run on an HF GPU with the pinned revision should confirm these numbers.
2. **One sample per task at temperature 0.9.** Outcomes change from run to run (in single traces,
   `remove_from_cart`, `clear_cart` and `remove_coupon` each passed their visible tests once and failed
   another time). Treat 17% as roughly "a few tasks in 24", not as a precise rate. The comparison with a
   trained model (A4) needs several samples per task for both.
3. **The harness does a lot of the work, and training must use the same one.** With the first harness
   the same model scored 0 of 24 with 15 UNKNOWN. What changed is listed below. A trained model has to
   be measured under exactly this harness, or the comparison means nothing.
4. **Hosted-only pieces.** Reading a python block as a write and stopping on green live in
   `hosted_engine`. The local/training rollout path does not have them yet.

## What the first runs got wrong (and what was changed)

| Symptom | Cause | Change |
|---|---|---|
| Model never used `path`/`content` | tool wrapper hid the signatures | `functools.wraps` |
| Every episode "wrote nothing" | scorer expected one call per step | read AgentTune's real step shape |
| UNKNOWN for covered handlers | baseline scored against test fixtures | score against `spec/spec.md` |
| UNKNOWN for broken code | import failure counted as "no verdict" | FAIL, class `does_not_load` |
| Sibling handlers deleted | `write_file` replaced the whole module | merge writes into the module |
| Invented FastAPI/SQLAlchemy code | the model writes before it reads | system prompt with rules, style, schema |
| Visible tests could never pass | other handlers are stubs, tests call them | run the handler's tests with the reference behind the rest |
| Context overflow | code sent twice per turn, history grows | python block is the write; one compact repair request per turn |
| Same failing code resent | greedy decoding copies its own history | temperature 0.9; repeated attempts are withheld |
| A passing function overwritten | the model does not stop | the episode ends when the handler's visible tests pass |

## Decision for training (plan step 6)

Verified success is above the "near zero" line (4 of 24), so no narrowing of the task set is proposed.
The signal for training is there: 11 of 24 tasks already reach passing visible tests, and the verifier
separates 4 of them from the other 7.
