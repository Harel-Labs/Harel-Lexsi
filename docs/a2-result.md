# A2 result: both rewards approved 0 of 19 broken handlers; the judge's score did not separate broken from correct

Issue #35. Rules: `docs/a2-plan.md`, committed before any judge call. Data: `docs/a2-run/` (`rows.jsonl` with every item's
code, verdicts, scores and the judge's reasons; `summary.json`; `summary.md`). Run: HF Job `6af8cf0923985fb9cd828f96`, 2026-11-14.

## The two numbers (broken handlers approved, lower is better)

| reward | approved | n |
|---|---|---|
| **Verifier (Harel)**, hidden contracts of `spec/spec.md` | **0** | 19 |
| **LLM judge**, code + visible-test result, approval = score 1.0 (the line fixed before the run) | **0** | 19 |

No UNKNOWN on either side. On the number A2 asks for, at the line agreed in advance, **the two rewards are tied**: neither
approved a broken handler. This does not support the claim that an LLM judge approves broken mutants the verifier catches.

## Beside them, not blended into them

| | verifier | LLM judge |
|---|---|---|
| broken approved, verifier on the V7 fixtures | 0 of 19 | n/a |
| broken approved, judge code only (no test result), score 1.0 | n/a | 0 of 19 |
| broken approved at the looser line, score >= 0.5 (sensitivity) | n/a | **19 of 19** (same with code only) |
| 17 mutants / 2 traps, headline settings | 0 of 17 / 0 of 2 | 0 of 17 / 0 of 2 |
| **control: the 6 correct handlers approved** (higher is better) | **6 of 6** (both contract sets) | **2 of 6** (1 of 6 code only) |

9 of the 19 broken handlers pass every visible test that calls them.

## What the judge actually did

- It gave **0.5 to all 19 broken handlers**, with or without the test result, and **0.5 to 4 of the 6 correct handlers**. 23 of
  the 25 items got the same score. No approval line separates broken from correct: at 1.0 it rejects every broken handler and
  four correct ones; at 0.5 it approves every broken handler.
- For three of the four correct handlers it scored 0.5, its stated reason is wrong: "does not import the `secrets` module"
  (`share_cart`; the judge is shown one function, not the file's imports), "does not ensure atomicity by wrapping the entire
  operation in a transaction" (`checkout`, which does), "overwriting [the quantity] with the new total" (`add_to_cart`, where
  the new total is the right value). The fourth is arguable: for `remove_from_cart` it applied the shared rule "a product that
  does not exist or is not active fails with `not_found`", which the spec states for all handlers but neither the reference nor
  the hidden contracts require there. That one may be a gap in the spec, not a judge error.
- Its one-sentence reason often does name the real bug. By my reading of the 19 reasons (a judgement call, the texts are in
  `rows.jsonl`): about 12 name the actual defect (for example `checkout-drop-stock`: "does not update the inventory",
  `share-orphan-event`: "does not publish the `cart.shared` event"), one is vague, and 6 name something else, including the
  `checkout_no_event` trap, where it missed the line that deletes the event and complained about transactions.
- So the judge often sees the defect but does not turn it into a score that differs from its score for correct code.
- The verifier failed all 19 on the hidden contracts, which were written without these mutants in mind, and passed all 6
  correct handlers. Its result does not depend on a threshold.

## How to state this honestly

- **Do not say** "the judge approved broken mutants and the verifier did not" as the headline. At the pre-registered line
  both approved none.
- **What the data supports:** on these 25 handlers the verifier separated broken from correct perfectly (0 of 19 approved,
  6 of 6 passed), and the judge's score did not separate them at any line (0.5 for 23 of 25). As a training reward, a judge
  that scores correct and broken code alike gives no signal; that is the measured difference, and it shows in the control, not
  in the approval count.
- The looser-line number (19 of 19) is a sensitivity check that was labelled as such before the run. It is not the result.

## Limits

- n = 19 broken and 6 correct; one judge model (`CohereLabs/c4ai-command-a-03-2025`), one call per item at temperature 0,
  one prompt (`judge_prompt_v1.txt`, written for A1's trajectories and reused unchanged). A different prompt or a 0-to-1 scale
  with more anchors might spread the scores; that was not tested.
- The same prompt gave 0, 0.5 and 1.0 across A1's 24 trajectories, where its 1.0s matched the verifier's passes exactly. Here
  it used 0.5 almost everywhere. The inputs differ: A1 showed multi-step trajectories of a weak model, A2 shows one clean
  write of near-correct reference code.
- The mutants are one-line bugs in clean code; model-written wrong answers are messier.
- The controls are only 6 handlers, so "2 of 6" is a small number.
- The judge reads code and the verifier runs it; nothing here measures cost or latency.

## Checks

- Tests for how mutants are shown and counted: `tests/test_mutant_judge.py` (the judge is never shown a mutant's id, operator
  or note; every mutant reads as ordinary code; UNKNOWN is neither approval nor rejection). Full suite in a CPU Job before the
  run: 438 passed, 1 skipped, 1 xfailed.
- A first attempt of the run crashed before any judge call (the fresh database had not been migrated); the fix is in
  `scripts/a2_run.py`. No judge output existed before the rules were committed.
