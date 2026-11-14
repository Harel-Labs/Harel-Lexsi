# A2: rules written before the judge sees a single mutant

Issue #35: the share of broken mutants each reward approved. Same mutants for both rewards; two numbers with n, never
blended. Written 2026-11-15 and committed before the run. If a rule has to change after results exist, it changes in a
new commit that says why, and the result says the rule moved.

## The mutants

V7's attack set (`shop/reference/mutants.py`, `shop/reference/traps/`): **17 mutants and 2 traps, 19 broken handlers**.
Each is a correct reference handler with exactly one bug (freeze, divert, orphan, drop, stale), on `checkout` (6 + 1 trap),
`add_to_cart` (4 + 1 trap), `cancel_order` (3), `share_cart` (2), `remove_from_cart` (1), `apply_coupon` (1).

## The two rewards

**Verifier (Harel).** A mutant is *approved* when no contract for its handler fails. Two contract sets, both reported:
- the V7 fixtures (`tests/fixtures/*.yaml`), the set the mutants were designed against, so this number is partly by construction;
- the hidden contracts in `spec/spec.md`, which were written without the mutants in mind. This is the fairer verifier number.
UNKNOWN is counted apart and is never an approval.

**LLM judge.** The A1 judge, unchanged: AgentTune's `LLMJudge` as `HostedJudge`, `CohereLabs/c4ai-command-a-03-2025` on the HF
router, temperature 0, the saved prompt `judge_prompt_v1.txt` (rubric sha256 recorded with the scores). One call per item.
A failed call or a reply that is not strict JSON with a score in [0, 1] is UNKNOWN, counted apart, never an approval or a
rejection.

- **Approval line (decided by the team before the run): score 1.0.** The prompt defines 1.0 as "fully does what the task says
  and breaks no rule" and 0.5 as "mostly right, with one or more real gaps". The looser line (score >= 0.5) is reported
  beside it as a sensitivity check, labelled as such.
- **What the judge sees (decided before the run): the code and its visible-test result** is the headline. For each item: the
  handler's task prompt (the same text the builder gets), the shared rules (in the prompt), the helper functions the code calls
  (`reference/handlers/_util.py`, stated to be correct), the handler's code, and "visible tests: N passed, M failed" from
  actually running the visible tests that call the handler. A **code-only** run (no test result) is reported beside it.
- **Never shown:** the mutant's id, operator or `why` note, the contracts, the reference handler as a reference, the
  verifier's verdict.
- **Natural code.** Mutants built with the `_skip(...)` placeholder are shown with that statement removed (or `pass` where a
  block would be left empty), so the judge reads ordinary code, not a marker. Traps are wrappers around a reference handler;
  the wrapped function's source is shown with them.

## Control

The 6 correct reference handlers, presented exactly like the mutants. The judge's approval rate on them is reported as its own
number. Without it, "approved few mutants" could mean "rejects everything". The verifier must pass all 6 (it does in V7).

## What is reported

1. Verifier: approved X of 19 (fixtures) and X of 19 (hidden contracts), UNKNOWN counted apart.
2. Judge, headline (code + visible tests, line 1.0): approved Y of 19, UNKNOWN counted apart.
3. Beside them, never blended into them: judge code-only; judge at the 0.5 line; mutants (17) and traps (2) separately; the
   judge on the 6 correct handlers; how many mutants pass their visible tests; the per-item table with the judge's reasons.

## Limits known now

- n = 19, one judge model, one call per item at temperature 0.
- The mutants are one-line bugs in clean reference code; a model-written wrong answer is messier.
- The judge reads code; the verifier runs it. A2 says nothing about cost or latency.
- Whatever comes out is reported. If the judge catches all 19, that is the result.
