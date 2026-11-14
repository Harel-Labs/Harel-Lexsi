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

## Results so far

| What | Result | Source |
|---|---|---|
| **Does the verifier have teeth?** (V7 gate, real holdout) | 43/43 correct handlers PASS; **2/2 traps and 17/17 mutants caught** with the expected failure class; UNKNOWN rate 0% | [docs/day1-gate.md](docs/day1-gate.md) |
| **Baseline** (untrained Tiny Aya Global, 24 tasks, hosted, 1 sample) | **4/24 (17%) verified** by Harel vs **11/24 (46%) passing the visible tests**; 7 tasks pass their visible tests and fail verification | [docs/t2-baseline.md](docs/t2-baseline.md) |
| **LLM-judge baseline** (`command-a-03-2025` on the same 24 episodes) | at a strict line (score 1.0) the judge approves the same 4 tasks the verifier passes; at 0.5 it approves 13. **Does not yet show the verifier beating a judge**; that claim is about broken mutants (A2, not run) | [docs/a1-judge.md](docs/a1-judge.md) |
| **First GRPO run** (3 steps, A100) | works end to end, **reward did not move** (3 steps cannot show a trend); about 5 min per step, so the plan switched to offline DPO on verified pairs (T8) | [docs/t3-first-run.md](docs/t3-first-run.md) |
| **After training** (the before-and-after the hackathon asks for) | **not available yet**: *fill in from T8 or the factory A/B (G3) before submitting* | |

Read the numbers with the caveats in each document: n = 24 tasks, one sample each at temperature 0.9, hosted rather than
the pinned weights, and the harness does a lot of the work.

## What is in the repo

| Path | What |
|---|---|
| `spec/spec.md` | the shop spec: `## Build` (what the agent sees), `## Resources`, `## Holdout` (43 hidden contracts) |
| `shop/` | the shop on Postgres: handler stubs the agent edits (`app/`), correct reference handlers, 17 mutants, S4 traps, fixtures |
| `src/harel_lexsi/verifier/` | the model-free verifier: capture, contract loader, settlement, failure classes, failure line, gate, coverage |
| `src/harel_lexsi/adapters/` | the AgentTune side: harness, reward, baseline runner, judge, closed-loop hooks (the only place that imports AgentTune) |
| `src/harel_lexsi/factory/` | factory versions (G1), governor and frozen proposals (G2), L2 router (T7), deployment gate (T6) |
| `src/harel_lexsi/tasks/`, `tasks/` | the 24-task set (13 implement, 11 patch) and its frozen train / held-out split |
| `scripts/` | baseline, judge and training-job entry points |
| `docs/` | decisions and run records (model choice, baseline, judge, GRPO run, gate log, submission rules) |

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
  --ignore=tests/test_train_rollout.py                 # verifier, gate, tasks, governor (no AgentTune needed)
uv run python -m harel_lexsi.verifier.gate --spec spec/spec.md      # V7: does the verifier have teeth?
uv run python -m harel_lexsi.tasks check --spec spec/spec.md        # every task is scorable, split intact
uv run python -m harel_lexsi.verifier.coverage --spec spec/spec.md  # every holdout effect is planned
```

Parts that need more:

| To run | You need |
|---|---|
| the **whole** test suite: `uv run pytest tests -q` (the 7 files ignored above import AgentTune, so they fail to collect without it) | local checkouts of **AgentTune** and **AuditKIT** (noncommercial; `uv pip install -e ../AgentTune -e ../AuditKIT`) |
| `scripts/baseline.py` (T2 baseline) | `HF_TOKEN` in `.env` (gated Tiny Aya weights, Inference Providers) |
| `scripts/judge_run.py` (A1) | `HF_INF_TOKEN` |
| `scripts/t3_job.py` (GRPO on HF Jobs) | `HF_JOB_TOKEN` with the Jobs permission and credits; it costs money |

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
- The verifier imports no model client or AgentTune (a test guards it). AgentTune and AuditKIT are noncommercial and live
  only behind the adapters.

## Models and tools

Builder: `CohereLabs/tiny-aya-global` (Cohere Labs, dense, open weights). Judge baseline: `CohereLabs/c4ai-command-a-03-2025`
(supplementary). Lexsi stack: **AgentTune** (harness, reward, training buffer), **AuditKIT** (run loading, paired
comparison, tool-call metrics). Also: PostgreSQL logical decoding, Hugging Face Inference Providers and Jobs.
