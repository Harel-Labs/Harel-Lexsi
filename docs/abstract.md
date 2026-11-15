# Harel: the verification layer inside a software factory that its agents cannot fool

AI agents can now write, test and ship software with little human help. Teams call these setups "software factories." But they
have a weak spot: the same agents that write the code also write the tests, and they share the same blind spots. All the tests
pass, and the product is still broken. When an agent's own checks decide what counts as success, retraining the agent only teaches
it to pass those checks better.

Harel is a verification layer built into the factory but kept separate from the agents. It judges their work by what the software
actually does. Alongside the specification, a hidden set of contracts is written. Each contract says what a feature should change in
the system, such as the rows it writes and the events it sends, and what it must never touch. The agents building the
software never see these contracts. As an agent works, Harel records every change each step makes to the database and events, then
compares those changes against the contracts and gives a clear verdict: pass, fail, or unknown when the evidence is missing. No model
is involved, and unknown never counts as a pass.

For this hackathon we connect Harel to AgentTune (problem statements 8 and 9). We build a small shop application on Postgres and a
builder agent (Tiny Aya Global) that implements its features. When the agent fails, Harel says which step went wrong and why, for
example "step 3 wrote to inventory, which this task must not touch." Answers Harel passes and answers it fails, for the same task,
become preference pairs, so the training signal comes from the contracts, not from the visible tests. The agent is retrained with
AgentTune, and the new version is deployed only if it does better on held-out tasks that Harel checks. A governance agent may propose
prompt changes, each stating its expected effect before any test.

We load and compare runs with AuditKIT and measure verified success before and after retraining, broken versions caught compared
with a standard LLM judge, and wrong or wasted tool calls.

What we found, on small samples (24 tasks, 8 held out). The untrained agent passed the visible tests on 11 of 24 tasks, but
Harel verified only 4. Harel caught all 19 deliberately broken handlers and passed every correct one. Retraining raised the visible-test
pass rate on held-out tasks from 45% to 55% but left verified success at 12%, so the gate kept the old model: training had taught the
agent to pass the tests, not to be right, which is the failure Harel exists to catch. A strong LLM judge gave 23 of 25 handlers the
same score, broken or correct; Harel separated them exactly.

The goal: a software factory whose output you can trust, because it is checked by something its agents never see and
cannot edit.
