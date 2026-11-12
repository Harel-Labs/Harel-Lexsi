from collections.abc import Sequence

from harel_lexsi.verifier import Settlement, Verdict, VerdictResult


def verdict_to_reward(result: VerdictResult) -> float:
    """Reward for training. UNKNOWN is not a pass."""
    return 1.0 if result.verdict is Verdict.PASS else 0.0


def settlement_reward(
    settlements: Sequence[Settlement],
    *,
    fail_reward: float = -1.0,
    step_penalty: float = 0.0,
) -> float:
    """T1: reward for one attempt at a task, from the verifier's settlements.

    One settlement: PASS +1, UNKNOWN 0, FAIL `fail_reward` (negative). Several (a task has several
    contracts): with p the fraction that PASS,
      - no FAIL:  reward = p                          (UNKNOWN adds nothing, so it is never a pass)
      - any FAIL: reward = fail_reward * (1 - p / 2)  (always negative; more passes hurt less)
    so a broken contract can never be averaged away by passing the others. `step_penalty` (optional,
    default off) then subtracts that much per distinct failing step, a small process reward. Pure and
    deterministic. Nothing settled means nothing proved, so 0.
    """
    if fail_reward >= 0:
        raise ValueError("fail_reward must be negative")
    if step_penalty < 0:
        raise ValueError("step_penalty must be >= 0")
    if not settlements:
        return 0.0
    p = sum(s.verdict is Verdict.PASS for s in settlements) / len(settlements)
    any_fail = any(s.verdict is Verdict.FAIL for s in settlements)
    reward = fail_reward * (1 - p / 2) if any_fail else p
    failing_steps = {v.step for s in settlements if s.verdict is Verdict.FAIL for v in s.violations}
    return reward - step_penalty * len(failing_steps)
