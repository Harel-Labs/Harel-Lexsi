from harel_lexsi.verifier import Verdict, VerdictResult


def verdict_to_reward(result: VerdictResult) -> float:
    """Reward for training. UNKNOWN is not a pass."""
    return 1.0 if result.verdict is Verdict.PASS else 0.0
