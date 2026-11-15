# Harel: project report

Lexsi AI Safety and Alignment Hackathon, Track 3 (Agentic Fine-tuning), problem #8 Self-healing agents, with #9 Reward design
and reward-hacking defences. This is the complete account of what was built, what was measured, and what it shows. Every number
links to the run record it came from.

## In one paragraph

A small coding agent (Tiny Aya Global, 3.35B) writes backend handlers for a Postgres shop. Its visible tests often pass while
the database is quietly wrong. **Harel is a model-free verifier**: it records every row a tool call changes (Postgres logical
decoding) and settles hidden contracts against those changes, answering PASS, FAIL (with a failure class and the step that
caused it) or UNKNOWN. We used that verdict to measure the agent, to reward it, to train it, and to gate the result. The
verifier caught every broken handler we built (19 of 19) and the correct reference handlers passed all 43 hidden contracts. It showed the untrained agent
"passing" 46% of tasks by its visible tests but only 17% by verification. And when training made the agent better at passing
visible tests without making it more correct, the gate refused to deploy it.

## The showcase: three things this project demonstrates

### 1. Tests that pass are not handlers that work, and Harel shows the difference

The untrained agent on the 24 shop tasks (`docs/t2-baseline.md`):

| | passed |
|---|---|
| visible tests (what the agent can check) | 11 of 24 (46%) |
| Harel verifier (hidden contracts, real database effects) | 4 of 24 (17%) |

Seven tasks pass every visible test and fail verification. `implement-share_cart` returns the right value and never publishes
the `cart.shared` event. `implement-clear_cart` returns the right count and also touches rows it must not. A test suite that
checks return values cannot see either; the verifier sees both, and names the class (`missing_event`, `undeclared_write`) and
the step.

### 2. The verifier stopped a model that only looked better

We trained the agent with DPO on 333 preference pairs that the verifier labelled, then measured base against trained under
rules written before the trained model answered anything (`docs/t8-plan.md`, `docs/t8-result.md`):

| held-out tasks (n = 8, 5 samples each) | base | trained |
|---|---|---|
| visible tests pass | 45% | **55%** |
| writes that are not valid Python | 14% | **5%** |
| **verified by Harel** | 12% | **12%** |
| answers that pass the visible tests but fail the verifier (all 75 episodes) | 23 | **28** |

By the tests the agent can see, training worked. By the verifier, it did not: the trained model got better at satisfying the
visible tests, not at being right, and the number of "looks right, is wrong" answers went up. The deployment gate, with
thresholds committed before the run, said **keep the old model**. That is the system doing its job: a judge that trusted the
visible tests would have shipped it.

### 3. An LLM judge reading the code could not tell broken from correct; the verifier could

On V7's 19 deliberately broken handlers and the 6 correct ones, the same judge as A1 (Command A, 111B) and the verifier
(`docs/a2-result.md`):

| | verifier | LLM judge |
|---|---|---|
| broken handlers approved (lower is better), line fixed before the run | 0 of 19 | 0 of 19 |
| correct handlers approved (higher is better) | **6 of 6** | **2 of 6** |
| distinct scores used across the 25 items | pass / fail | 0.5 for 23 of 25 |

At the agreed approval line the two rewards tie on broken handlers, and we report that plainly. The difference is that the
judge gave the same 0.5 to almost everything, broken or correct, so as a reward it carries no signal on this set; its
one-sentence reasons named the real bug in about 12 of 19 cases, but the score did not follow. The verifier separated the two
groups exactly, with no threshold to choose.

## How Harel works

```
spec/spec.md:  ## Build (what the agent sees) · ## Resources · ## Holdout (43 hidden contracts)
                                         │
agent writes a handler ─► sandbox runs it ─► Postgres logical decoding records every row change, per tool call
                                         │
             verifier replays each contract: given state + input ─► effects, closure, atomicity, expected error
                                         │
                PASS · FAIL (failure class + step) · UNKNOWN (never a pass, never a reward)
                    │                    │                         │
            reward / pair label    one-line failure note      deployment gate on held-out tasks
```

- **Contracts** (`spec/contract-format.md`) state what must change (effects), what must not (closure), that a call is all or
  nothing (atomic), and which error a bad input raises. They are written against logical resources; `spec/binding.yaml` maps
  them to tables.
- **Failure classes**: `missing_write`, `undeclared_write`, `missing_event`, `broken_atomicity`, `wrong_cardinality`,
  `wrong_final_state`, and `does_not_load` (code that does not even import). The note sent back to the agent names the class and
  the step only, never contract text, so the hidden contracts cannot leak into training.
- **Model-free and isolated**: the verifier imports no model client and no AgentTune (a test guards this). AgentTune and AuditKIT
  (noncommercial) live only in `src/harel_lexsi/adapters/`.

## What we built and measured, in order

Board IDs are the hackathon issues. Each row links to its record.

| Step | What | Result | Record |
|---|---|---|---|
| V1 to V7 | capture, contracts, settlement, classes, failure line, mutants, the gate | gate on the 43 hidden contracts: **the correct reference handlers pass 43/43, 2/2 traps and 17/17 mutants caught with the expected class, UNKNOWN 0/62** | `docs/gate-holdout.md` (fixture run: `docs/day1-gate.md`) |
| S1 to S6 | shop on Postgres, spec with 43 hidden contracts, 24 tasks (13 implement, 11 patch), frozen split (16 train, 8 held-out), agent harness | every task is scorable and the split is intact | `tasks/README.md`, `shop/README.md` |
| B2, B5 | model choice, AgentTune smoke test | Tiny Aya Global pinned (revision `af89d219`); one stock GRPO step and AuditKIT read the run | `docs/b2-model-choice.md` |
| T1, T4 | verifier as an AgentTune reward; closed-loop hooks that buffer only verified repairs | in place, tested | `src/harel_lexsi/adapters/settlement_reward.py`, `closed_loop.py` |
| T2 | baseline, untrained agent, 24 tasks | **4/24 verified, 11/24 visible** | `docs/t2-baseline.md` |
| A1 | LLM-judge reward on the same 24 episodes | at score 1.0 it approves exactly the 4 the verifier passes; at 0.5, 13 | `docs/a1-judge.md` |
| A3, A5 | load runs into AuditKIT, tool-call metrics | 24 episodes load; 111 calls, 6 wrong (5.4%), 0 redundant | `src/harel_lexsi/adapters/run_compare.py` |
| G2 | factory governor: one proposed prompt/memory change with its expected effect | in place (PR #67) | `factory/README.md` |
| T3 | first GRPO run, 3 steps on an A100 | works end to end; reward −0.90, −1.00, −0.93; **about 5 minutes a step on any GPU**, so switched to T8 | `docs/t3-first-run.md` |
| T8, T5 | 270 episodes sampled on the pinned weights, 842 distinct answers scored by the verifier, 333 pairs, LoRA DPO | training converged (loss 0.69 to about 0.05, 93% of held-back pairs ranked right); adapter saved with provenance | `docs/t8-result.md` |
| T6, G3, A4 | base vs trained on held-out tasks, gate decision, A/B, significance | held-out verified 12% → 12%; **gate: keep the old model** | `docs/t8-result.md`, `docs/t8-run/` |
| A2 | broken mutants approved by each reward | verifier 0/19, judge 0/19 at the agreed line; correct handlers 6/6 vs 2/6 | `docs/a2-result.md` |

## What we learned building it

Most of the effort went into making a 3B model's episodes measurable at all. These are the problems the first real runs exposed
and the fixes, each found by evidence rather than assumed:

| Symptom | Cause | Fix |
|---|---|---|
| 0 of 24 verified, 15 UNKNOWN in the first baseline | the tool wrapper hid argument names; the scorer read the wrong trajectory shape; it scored against test fixtures | `functools.wraps`; read AgentTune's real step shape; score against `spec/spec.md` |
| broken code counted as "unknown" | an import failure gave no verdict | FAIL with the new class `does_not_load` |
| the agent deleted the other handlers in a file | `write_file` replaced the whole module | writes merge into the module (`module_merge.py`) |
| invented web-framework code, context overflows, the same wrong code resent | a small model writes before it reads, and copies its own history | a system prompt with the rules and schema, focused visible tests after each write, one compact repair request per turn, stop as soon as the tests pass |
| half of all writes were not valid Python in the T8 pilot | sampling from the full 262k-token vocabulary | nucleus sampling (top-p 0.75, as the hosted provider does): 53% → 13% |
| a GRPO step took 5 minutes on an L40S and on an A100 | sequential generation, limited by per-token overhead, not the GPU | for T8, a batching server: a batch of 4 costs about the wall time of 1 |
| every reward silently 0 in the training job | the verifier's subprocess could not import the package | install it in the job; found on a CPU job before any GPU time |

Rules we kept throughout: UNKNOWN is never a pass; the held-out split is never sampled or trained on (checked twice in code);
evaluation rules are committed before the result exists; negative results are reported as results.

## Limits, stated plainly

- **Small samples.** 24 tasks, 8 held out; 19 broken and 6 correct handlers for A2. Every number carries its n. The held-out set
  has little room to move: seven of eight tasks are at 0% for the base model.
- **The verifier checks database effects and errors, not return values.** A handler that returns wrong data without touching the
  database is not caught. Known gap: writing and then undoing inside one step still passes (an expected-failure test records it).
- **Training did not improve verified success.** One DPO round, untuned, with few hard negatives (4% of pairs). The finding is
  that the gate caught a proxy gain, not that the agent got better.
- **One judge model, one prompt** for A1 and A2. A different prompt or scale might separate correct from broken better; untested.
- **The 43 hidden contracts were drafted by an agent and accepted by the team** (recorded in `spec/spec.md`).
- **Hosted versus pinned.** The T2 baseline used the hosted model; T8 sampling, training and evaluation used the pinned weights.

## Running it

The verifier, shop, task set, gate and governor run locally with Docker and no API key; see the README's Reproduce section. The
training and evaluation chain runs on Hugging Face Jobs through one launcher:

```bash
uv run python scripts/hf_job.py tests                                                        # full test suite, CPU, cents
uv run python scripts/hf_job.py run scripts/gate_job.py                                      # V7 gate on the hidden contracts
uv run python scripts/hf_job.py run scripts/t8_collect.py --gpu --yes -- --episodes 24       # sample + score + pairs (A100)
uv run python scripts/hf_job.py run scripts/t8_pairs.py -- --run <collection>                # rebuild pairs, CPU
uv run python scripts/hf_job.py run scripts/train_t8.py --gpu --yes -- --run <collection>    # LoRA DPO (A100)
uv run python scripts/hf_job.py run scripts/t8_eval.py --gpu --yes --with-auditkit -- --run <collection>   # base vs trained + gate
uv run python scripts/hf_job.py run scripts/a2_run.py --secret HF_INF_TOKEN                  # A2: verifier vs judge on mutants
```

GPU jobs refuse to submit without `--yes`. Outputs land in the private bucket `<user>/jobs-artifacts` under `harel-out/`; the
results used in this report are copied into `docs/*-run/`. Compute for the whole project, by our estimate from run times and
published prices: about $13 (T3 about $2, the T8 chain about $11, A1, A2 and the CPU jobs a few cents each).
