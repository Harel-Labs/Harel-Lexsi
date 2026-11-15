# Harel: a verifier that catches what small coding agents get wrong

Lexsi AI Safety and Alignment Hackathon · **Track 3, Agentic Fine-tuning** · problem statement **#8 Self-healing
agents**, with **#9 Reward design and reward-hacking defences** in support.

A small builder agent writes backend code for a shop. Its own tests say it is fine, but it quietly breaks the
database: it takes stock when an item is only added to a cart, it places an order and forgets to publish the event, it
half-commits a checkout. **Harel checks what a tool call actually changed in the database**, against contracts the
agent never sees, and answers PASS, FAIL or UNKNOWN with no model involved. That verdict is the reward and the
gate for retraining the agent, and it is the one thing an LLM judge reading the code cannot be trusted to give.

```
spec (Build / Resources / hidden Holdout) ──► 43 hidden contracts
                                                   │
agent writes a handler ──► Postgres logical decoding records every row change per tool call
                                                   │
                       verifier settles contracts: PASS · FAIL (class + step) · UNKNOWN (never a pass)
                          │                 │                       │
                    reward (AgentTune)   failure line to L1    rule-based L2 router
                          │                                         │
                 retrain the builder  ◄──  verified failure + repair pairs
                          │
          factory governance: versioned prompt/memory, one proposed change with its expected effect,
          A/B on held-out tasks scored by the verifier (AuditKIT reads the runs)
```

## The showcase

The full account (what we built, every result, what we learned, the limits) is in
**[docs/project-report.md](docs/project-report.md)**. Three things it demonstrates:

1. **Tests that pass are not handlers that work.** The untrained agent passes its visible tests on 11 of 24 tasks (46%),
   but Harel verifies only 4 (17%). Seven tasks look right and are wrong, and the verifier names the failure class and step.
2. **The verifier stopped a model that only looked better.** After DPO training on verifier-labelled pairs, the agent passed
   more visible tests on held-out tasks (45% → 55%) and wrote less invalid Python (14% → 5%), but its verified success did not
   move (12% → 12%), and answers that pass the visible tests but fail the verifier rose from 23 to 28. The deployment gate,
   with thresholds written before the run, said **keep the old model**.
3. **An LLM judge could not tell broken from correct; the verifier could.** On 19 deliberately broken handlers and 6 correct
   ones, both rewards approved 0 broken handlers at the line agreed in advance, but the judge scored 23 of the 25 items the same
   (0.5) and approved only 2 of 6 correct ones; the verifier passed 6 of 6 and failed 19 of 19, with no threshold to choose.

## Results

| What | Result | Source |
|---|---|---|
| **Does the verifier have teeth?** (V7 gate, the 43 hidden contracts) | the correct reference handlers PASS all 43 contracts; **2/2 traps and 17/17 mutants caught** with the expected failure class; UNKNOWN 0/62 | [docs/gate-holdout.md](docs/gate-holdout.md) (fixture run: [docs/day1-gate.md](docs/day1-gate.md)) |
| **Baseline** (untrained Tiny Aya Global, 24 tasks, hosted, 1 sample) | **4/24 (17%) verified** by Harel vs **11/24 (46%) passing the visible tests**; 7 tasks pass their visible tests and fail verification | [docs/t2-baseline.md](docs/t2-baseline.md) |
| **LLM-judge baseline** (`command-a-03-2025` on the same 24 episodes) | at score 1.0 the judge approves exactly the 4 tasks the verifier passes; at 0.5 it approves 13 | [docs/a1-judge.md](docs/a1-judge.md) |
| **First GRPO run** (3 steps, A100) | works end to end; reward did not move; **about 5 min per step on any GPU**, so the plan switched to offline DPO on verified pairs (T8) | [docs/t3-first-run.md](docs/t3-first-run.md) |
| **Training** (T8: 270 episodes sampled, 842 answers scored by the verifier, 333 pairs, LoRA DPO) | converged (loss 0.69 → about 0.05, 93% of held-back pairs ranked right); adapter saved with provenance | [docs/t8-result.md](docs/t8-result.md) |
| **After training** (held-out, n = 8 tasks, 5 samples each; rules written before the run) | verified **12% → 12%**; visible tests 45% → 55%; mutation set 35% → 35%. **Gate: keep the old model** | [docs/t8-result.md](docs/t8-result.md), [docs/t8-run/](docs/t8-run/) |
| **Reward hacking** (A2: 19 broken handlers, 6 correct) | broken approved: verifier **0/19**, judge **0/19** at score 1.0 (19/19 at 0.5). Correct approved: verifier **6/6**, judge **2/6** | [docs/a2-result.md](docs/a2-result.md) |

Read the numbers with the caveats in each document: small samples (24 tasks, 8 held out), one sample per task for the
baseline, the hosted model for T2 and the pinned weights for T8, and a harness that does a lot of the work.

## What is in the repo

| Path | What |
|---|---|
| `spec/spec.md` | the shop spec: `## Build` (what the agent sees), `## Resources`, `## Holdout` (43 hidden contracts) |
| `shop/` | the shop on Postgres: handler stubs the agent edits (`app/`), correct reference handlers, 17 mutants, S4 traps, fixtures |
| `src/harel_lexsi/verifier/` | the model-free verifier: capture, contract loader, settlement, failure classes, failure line, gate, coverage |
| `src/harel_lexsi/adapters/` | the AgentTune side: harness, reward, baseline runner, judge, closed-loop hooks, preference pairs, evaluation, mutant-judge comparison (the only place that imports AgentTune or AuditKIT) |
| `src/harel_lexsi/factory/` | factory versions (G1), governor and frozen proposals (G2), L2 router (T7), deployment gate (T6) |
| `src/harel_lexsi/tasks/`, `tasks/` | the 24-task set (13 implement, 11 patch) and its frozen train / held-out split |
| `scripts/` | baseline, judge, the HF Jobs launcher (`hf_job.py`), a batching model server (`serve_hf.py`), the T8 chain (`t8_collect`, `t8_pairs`, `train_t8`, `t8_eval`), A2 (`a2_run`), the gate in a job (`gate_job`) |
| `docs/` | the project report, decisions, rules written before each run, and run records with their raw data (`*-run/`) |

## Reproduce

Needs **Python 3.12** with [uv](https://docs.astral.sh/uv/), and **Docker** for Postgres. The verifier, the shop, the task
set and the governor run without any API key.

```bash
git clone https://github.com/Harel-Labs/Harel-Lexsi.git && cd Harel-Lexsi
cd shop && docker compose up -d                       # Postgres 16 on :5433 with wal_level=logical
uv sync && uv run python -m tools.migrate             # schema + seed
uv run python -m tools.check_wal                      # fails unless logical decoding is possible
uv run pytest -q                                      # the shop's reference handlers (the S3 gate)
cd ..
uv sync --extra dev
uv run pytest tests -q \
  --ignore=tests/test_shop_harness.py --ignore=tests/test_baseline.py --ignore=tests/test_builder_harness.py \
  --ignore=tests/test_closed_loop.py --ignore=tests/test_judge.py --ignore=tests/test_settlement_reward.py \
  --ignore=tests/test_train_rollout.py --ignore=tests/test_pairs.py --ignore=tests/test_evaluation.py \
  --ignore=tests/test_mutant_judge.py                  # verifier, gate, tasks, governor (no AgentTune needed)
uv run python -m harel_lexsi.verifier.gate --spec spec/spec.md      # V7: does the verifier have teeth?
uv run python -m harel_lexsi.tasks check --spec spec/spec.md        # every task is scorable, split intact
uv run python -m harel_lexsi.verifier.coverage --spec spec/spec.md  # every holdout effect is planned
```

Parts that need more:

| To run | You need |
|---|---|
| the **whole** test suite: `uv run pytest tests -q` (the 10 files ignored above need AgentTune, AuditKIT or a model tokenizer) | local checkouts of **AgentTune** and **AuditKIT** (noncommercial; `uv pip install -e ../AgentTune -e ../AuditKIT`), or run it in a job: `uv run python scripts/hf_job.py tests` |
| `scripts/baseline.py` (T2 baseline) | `HF_TOKEN` in `.env` (gated Tiny Aya weights, Inference Providers) |
| `scripts/judge_run.py` (A1) | `HF_INF_TOKEN` |
| anything through `scripts/hf_job.py` (the gate in a job, T3, the T8 chain, A2) | `HF_JOB_TOKEN` with the Jobs permission and credits; GPU jobs cost money and refuse to submit without `--yes`. The commands are listed in [docs/project-report.md](docs/project-report.md#running-it) |

The commands above are also documented in `shop/README.md`, `tasks/README.md` and `factory/README.md`; the scripts
explain their own usage at the top of each file.

## Honest limits

- **The verifier scores database effects and errors, not return values.** A handler that returns wrong data without
  touching the database is not caught. That is why `list_orders` and `view_shared_cart` have no patch task.
- **Known gap:** a handler that does the required writes and then undoes them inside the same step still passes
  (`tests/test_mutants.py::test_write_then_undo_should_fail`, an expected failure). Settlement should judge the net result.
- **The 43 hidden contracts were drafted by an agent and accepted by the team** (recorded in `spec/spec.md`). The mutants
  and traps they are tested against are separate from the training tasks.
- Small samples: 24 tasks, 8 held out. Treat every number as a demonstration, not proof.
- **Training did not improve verified success.** One untuned DPO round with few hard negatives (4% of pairs). What it shows
  is that the gate caught a gain on the visible tests that the verifier did not confirm, not that the agent got better.
- **One judge model and one prompt** for A1 and A2; a different prompt might separate correct from broken code better.
- The verifier imports no model client or AgentTune (a test guards it). AgentTune and AuditKIT are noncommercial and live
  only behind the adapters.

## On Hugging Face

| | |
|---|---|
| Collection | [Harel: verifier-gated fine-tuning](https://huggingface.co/collections/bhumiheda/harel-verifier-gated-fine-tuning-6af9eef871680167f708585f) |
| Model | [bhumiheda/harel-tiny-aya-dpo-lora](https://huggingface.co/bhumiheda/harel-tiny-aya-dpo-lora): the T8 DPO LoRA adapter with its provenance. It did not improve held-out verified success and was not deployed; the model card says so first |
| Dataset | [bhumiheda/harel-shop-verifier-runs](https://huggingface.co/datasets/bhumiheda/harel-shop-verifier-runs): T2 baseline trajectories, the T8 collection, pairs and evaluation, A1, A2, the gate log and the task set (no hidden contracts) |

Published with `scripts/hf_publish.py` (it refuses to upload anything containing hidden-contract text or a token).

## Models and tools

Builder: `CohereLabs/tiny-aya-global` (Cohere Labs, dense, open weights). Judge baseline: `CohereLabs/c4ai-command-a-03-2025`
(supplementary). Lexsi stack: **AgentTune** (harness, reward, GRPO and DPO trainers, training buffer), **AuditKIT** (run
loading, paired bootstrap, tool-call metrics). Also: PostgreSQL logical decoding, TRL and PEFT (LoRA), Hugging Face Inference
Providers and Jobs.
