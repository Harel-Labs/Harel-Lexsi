import pathlib

from harel_lexsi.adapters.rewards import verdict_to_reward
from harel_lexsi.verifier import Contract, Verdict, verify

C = Contract("c1", required=frozenset({"orders.insert"}), forbidden=frozenset({"inventory.update"}))


def test_pass():
    assert verify(C, frozenset({"orders.insert"})).verdict is Verdict.PASS


def test_fail_forbidden():
    assert verify(C, frozenset({"orders.insert", "inventory.update"})).verdict is Verdict.FAIL


def test_fail_missing():
    assert verify(C, frozenset()).verdict is Verdict.FAIL


def test_unknown_never_passes():
    r = verify(C, None)
    assert r.verdict is Verdict.UNKNOWN and verdict_to_reward(r) == 0.0


def test_verifier_is_model_free():
    banned = ("openai", "httpx", "huggingface_hub", "agenttune", "auditkit", "harel_lexsi.adapters")
    root = pathlib.Path(__file__).parents[1] / "src/harel_lexsi/verifier"
    for f in root.glob("*.py"):
        imports = [l for l in f.read_text().splitlines() if l.startswith(("import ", "from "))]
        assert not [l for l in imports if any(b in l.lower() for b in banned)], f
