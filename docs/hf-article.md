---
title: "Harel: a verifier its agents cannot see, as the reward and the gate for fine-tuning a small coding agent"
thumbnail: ""
authors:
  - user: bhumiheda
---

# Harel: a verifier its agents cannot see, as the reward and the gate for fine-tuning a small coding agent

*Lexsi AI Safety and Alignment Hackathon · Track 3, Agentic Fine-tuning · problem statements #8 Self-healing agents and
#9 Reward design and reward-hacking defences.*

**Links:** [code (GitHub)](https://github.com/Harel-Labs/Harel-Lexsi) ·
[model collection](https://huggingface.co/collections/bhumiheda/harel-models-6af9f717120d5e7caeff954e) ·
[dataset collection](https://huggingface.co/collections/bhumiheda/harel-datasets-traces-and-benchmarks-6af9f71839fca752615a4306)

## The problem

When AI agents write the code, the tests that decide "done" usually come from the same place, and they share its blind spots.
All the tests pass and the database is still wrong: stock is taken when an item is only added to a cart, an order is placed and
the event announcing it is never sent, a checkout half-commits. If those tests are also the training reward, fine-tuning teaches
the agent to pass them, not to be right.

## What we built

**Harel** is a verification layer kept outside the agents. A hidden set of contracts says what each feature must change in the
database, what it must never touch, that a call is all or nothing, and which error a bad input raises. The agent never sees them.
While it works, Harel records every row each tool call changes (PostgreSQL logical decoding) and settles the contracts against
those changes: **PASS, FAIL** (with a failure class such as `undeclared_write` or `missing_event`, and the step that caused it) or
**UNKNOWN** (missing evidence; never counted as a pass). No model is involved in the verdict.

We use that verdict four ways: to measure the agent, to label training data, to train it, and to gate whether a retrained agent is
deployed.

## Tasks, data and models, and why

- **Builder: `CohereLabs/tiny-aya-global`** (3.35B), pinned to one revision. The hackathon asks for a Cohere Labs open-weights
  model, and a small model makes the failure modes we care about frequent enough to measure.
- **Tasks: a 24-task shop benchmark** on Postgres (13 "implement this handler", 11 "fix this buggy handler"), frozen into 16 train
  and 8 held-out tasks by handler, so the held-out handlers are never seen in training. Each task comes with visible tests (return
  values and error codes) and is judged by 43 hidden contracts (database effects).
- **Lexsi tooling:** **AgentTune** runs the agent and trains it (GRPO, then DPO, with LoRA); **AuditKIT** loads the runs and does
  the paired bootstrap for before/after comparisons.
- **Judge baseline:** `CohereLabs/c4ai-command-a-03-2025` (111B) as a standard LLM judge, used only for comparison.

## Metrics, and why

| Metric | Why |
|---|---|
| **Verified success**: share of tasks whose final code passes every hidden contract | the outcome that matters: does the handler do the right thing to the data |
| **Visible-test pass rate**, reported beside it, never instead of it | the proxy an agent can see; the gap between the two is the point |
| **Broken handlers approved** by each reward (19 deliberate bugs) and **correct handlers approved** (6) | whether a reward can be fooled, and whether it can tell right from wrong at all |
| Wrong or wasted tool calls; share of writes that are not valid Python | cost and reliability of the agent's work |

Every number is reported with its sample size. Rules for judging the trained model were committed before it produced an answer.

## Results

**1. Tests that pass are not handlers that work.** The untrained agent passed the visible tests on **11 of 24** tasks, but Harel
verified only **4**. Seven tasks looked right and were wrong: one never published its event, another touched rows it must not.

**2. The verifier has teeth.** On its own gate, Harel caught **19 of 19** deliberately broken handlers (17 one-line mutants and 2
traps) with the expected failure class, the correct reference handlers passed all 43 contracts, and nothing was UNKNOWN.

**3. Did fine-tuning make the model better? By the tests it could see, yes. By the verifier, no.** We sampled 270 episodes from the
agent, scored 842 distinct answers with Harel, built 333 preference pairs (an answer Harel passes against one it fails, for the same
task) and trained a LoRA adapter with DPO. Then we measured base against trained on the held-out tasks (8 tasks, 5 samples each):

| held-out | base | trained |
|---|---|---|
| visible tests pass | 45% | 55% |
| writes that are not valid Python | 14% | 5% |
| **verified by Harel** | **12%** | **12%** |
| answers that pass the visible tests but fail the verifier (all 75 episodes) | 23 | 28 |

The trained agent wrote cleaner code and passed more visible tests, but it was not more often right, and its "looks right, is
wrong" answers went up. The deployment gate, with thresholds written before the run, said **keep the old model**. That is the
honest answer to "is the model better in practice": it is not, and the system caught that before it shipped. A reward built on the
visible tests, or a judge that trusted them, would have deployed it.

**4. An LLM judge could not tell broken from correct.** On the 19 broken and 6 correct handlers, both the judge and Harel approved
0 broken handlers at the approval line we fixed in advance. But the judge gave the same score (0.5) to 23 of the 25 handlers and
approved only 2 of 6 correct ones, often citing faults that were not there; Harel failed all 19 broken ones and passed all 6
correct ones, with no threshold to choose. As a reward, a score that does not move between right and wrong code carries no signal.

## What we learned getting a 3B agent to run at all

The first real baseline scored 0 of 24 with 15 UNKNOWN, and almost none of it was the model's fault: a tool wrapper hid argument
names, the scorer read the wrong trajectory format, whole-file writes deleted neighbouring functions, and sampling from the full
262k-token vocabulary made half of all writes invalid Python (top-p 0.75 brought that to 13%). A GRPO step took about five minutes
on any GPU we tried, because the agent's own rollouts were sequential, so we switched to offline DPO with a small batching server
for sampling. Each fix came from reading the traces, and each is recorded in the repository.

## Limits

Small samples (24 tasks, 8 held out; 19 broken and 6 correct handlers), so treat every number as a demonstration. The held-out set
has little room to move: seven of its eight tasks are at 0% for the base model. Harel checks database effects and errors, not return
values, and one known pattern (a write undone in the same step) gets past it. One DPO round, untuned, with few hard negatives (4% of
pairs). One judge model and one prompt.

## Next

Pairs built from hard negatives (both answers pass the visible tests, only one passes the verifier), more samples per held-out task,
and the governance loop in which an agent proposes a prompt change with its expected effect and Harel's A/B decides.

The full report, every run record and the code to reproduce it are in the [GitHub repository](https://github.com/Harel-Labs/Harel-Lexsi)
(`docs/project-report.md`).
