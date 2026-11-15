# Claims sheet (K3)

Every claim we make in the README, the abstract, the Hugging Face article and the report, with the ticket that produced it and the file that
backs it. Numbers here are copied from those files; `docs/report.html` renders the same data from the records.

Checked on 2026-11-16: the README, `docs/abstract.md`, `docs/hf-article.md` and `docs/project-report.md` contain no "kills the
harness" or "any repo" claim and no adoption figure, so none needs a citation.

| # | Claim | Ticket | Artifact |
|---|---|---|---|
| 1 | The correct reference handlers pass all 43 hidden contracts; 2 of 2 traps and 17 of 17 mutants are caught with the expected failure class; 0 of 62 UNKNOWN | V7 | `docs/gate-holdout.md` |
| 2 | The untrained agent passes the visible tests on 11 of 24 tasks and is verified on 4 of 24 | T2 | `docs/t2-baseline.md`, `docs/t2-baseline-run/summary.json` |
| 3 | Seven tasks pass the visible tests and fail verification | T2 | `docs/t2-baseline-run/results.jsonl` |
| 4 | At score 1.0 the LLM judge approves exactly the 4 tasks the verifier passes; at 0.5 it approves 13 | A1 | `docs/a1-judge.md`, `docs/a1-judge-run/` |
| 5 | A GRPO step took about 5 minutes on an L40S and on an A100; reward did not move in 3 steps | T3 | `docs/t3-first-run.md` |
| 6 | 270 episodes sampled, 842 answers scored, 333 preference pairs (strict rule), 4% hard negatives | T8 | `docs/t8-result.md`, `docs/t8-run/pairs_summary.json` |
| 7 | DPO converged (loss 0.69 to about 0.05, 93% of held-back pairs ranked right) | T8, T5 | `docs/t8-result.md`, `docs/t8-run/adapter_provenance.json` |
| 8 | Held-out verified success 12% to 12%; visible tests 45% to 55%; invalid Python writes 14% to 5% (8 tasks, 5 samples each) | T6, A4 | `docs/t8-run/eval_summary.json` |
| 9 | Answers that pass the visible tests but fail the verifier rose from 23 to 28 (75 episodes each) | T6 | `docs/t8-run/eval_rows.jsonl` |
| 10 | The deployment gate said "keep the old model"; thresholds were written before the run | T6, G3 | `docs/t8-plan.md`, `docs/t8-run/gate_decision.json` |
| 11 | Broken handlers approved: verifier 0 of 19, judge 0 of 19 at score 1.0 (19 of 19 at 0.5) | A2 | `docs/a2-result.md`, `docs/a2-run/summary.json` |
| 12 | Correct handlers approved: verifier 6 of 6, judge 2 of 6; the judge gave 0.5 to 23 of 25 items | A2 | `docs/a2-run/rows.jsonl` |
| 13 | The factory-graph row (trigger, diff, expected, observed, decision) is generated from the records | G4 | `docs/factory-row.json`, `scripts/build_report.py` |
| 14 | The verifier imports no model client and no AgentTune | V-series | `tests/` (import-guard test) |
| 15 | The adapter and the run data are public on Hugging Face under bhumiheda; hidden contracts are not published | K7 | `scripts/hf_publish.py`, README "On Hugging Face" |
| 16 | Compute for the whole project was about $13 | T3, T8 | `docs/project-report.md` (an estimate from run times and published prices, not an invoice) |

## Struck or softened

- "Training improved the agent" is not claimed anywhere. The claim is that the visible-test rate rose and the verified rate did not.
- "The judge is fooled by broken code" is not claimed. At the agreed line the two rewards tie on broken handlers (0 of 19 each); the
  difference is the correct handlers and the unvarying score.
- The DPO result is one round, untuned, on 8 held-out tasks; nothing here says the method cannot work.
- The 43 contracts were drafted by an agent and accepted by the team; the claim is that the verifier enforces them, not that they are complete.
- The verifier checks database effects and errors, not return values, and one pattern (a write undone in the same step) passes. Both are
  in the README's limits.
