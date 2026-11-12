"""T7: L2 routes, never judges. Pure rules over settlements; no database, no model."""
import inspect
import itertools
import pathlib

import pytest

from harel_lexsi.factory import Action, route
from harel_lexsi.factory import router as router_module
from harel_lexsi.verifier.types import Settlement, Verdict, Violation

PASS = Settlement("C-1", Verdict.PASS, "ok")
UNKNOWN = Settlement("C-2", Verdict.UNKNOWN, "no markers")


def fail(step=2, resource="inventory", cid="C-3"):
    v = Violation("closure_breach", step, resource=resource, detail="update on inventory")
    return Settlement(cid, Verdict.FAIL, "1 violation(s)", (v,))


def test_all_pass_merges():
    d = route([PASS, PASS], attempt=0)
    assert d.action is Action.MERGE and d.message == ""


def test_fail_retries_with_the_failure_line_only():
    d = route([PASS, fail()], attempt=0, visible_resources={"inventory"})
    assert d.action is Action.RETRY
    assert d.message == "step 2 wrote to inventory, which this task must not touch"


def test_retry_message_is_the_only_thing_l1_gets_and_leaks_no_contract_text():
    s = Settlement("ZZ-SECRET-9", Verdict.FAIL, "1 violation(s)",
                   (Violation("effect_absent", 4, "secret_e1", "emit", "secretres", "needs secret.event"),))
    d = route([s], attempt=0)
    assert d.action is Action.RETRY and d.message == "step 4 did not publish an event this task requires"
    for text in (d.message, d.reason):
        assert "secret" not in text.lower() and "zz-" not in text.lower()


def test_retry_limit_is_enforced():
    assert route([fail()], attempt=1, max_retries=2).action is Action.RETRY
    assert route([fail()], attempt=2, max_retries=2).action is Action.REJECT
    assert route([fail()], attempt=0, max_retries=0).action is Action.REJECT


def test_default_retry_limit_is_set():
    assert route([fail()], attempt=0).action is Action.RETRY
    assert route([fail()], attempt=router_module.DEFAULT_MAX_RETRIES).action is Action.REJECT


def test_unknown_escalates_and_never_merges_even_with_passes_and_many_attempts():
    for attempt in (0, 1, 5, 100):
        assert route([UNKNOWN], attempt).action is Action.ESCALATE
        assert route([PASS, PASS, UNKNOWN], attempt).action is Action.ESCALATE


def test_fail_beats_unknown():
    assert route([UNKNOWN, fail()], attempt=0).action is Action.RETRY
    assert route([UNKNOWN, fail()], attempt=9).action is Action.REJECT


def test_nothing_settled_escalates():
    assert route([], attempt=0).action is Action.ESCALATE


def test_negative_inputs_are_refused():
    with pytest.raises(ValueError):
        route([PASS], attempt=-1)
    with pytest.raises(ValueError):
        route([PASS], attempt=0, max_retries=-1)


def test_merge_happens_only_when_every_settlement_passed():
    """Exhaustive over every verdict combination up to size 4 and several attempt counts."""
    pool = {Verdict.PASS: PASS, Verdict.UNKNOWN: UNKNOWN, Verdict.FAIL: fail()}
    for n in range(5):
        for combo in itertools.product(Verdict, repeat=n):
            for attempt in (0, 1, 2, 3):
                d = route([pool[v] for v in combo], attempt)
                assert (d.action is Action.MERGE) == (n > 0 and all(v is Verdict.PASS for v in combo))


def test_no_llm_anywhere_on_the_merge_path():
    src = pathlib.Path(inspect.getsourcefile(router_module)).read_text()
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    banned = ("openai", "httpx", "huggingface_hub", "agenttune", "auditkit", "litellm", "anthropic", "adapters")
    assert not [ln for ln in imports if any(b in ln.lower() for b in banned)]
    # the router takes data only: no callable (judge, model, approver) parameter
    for name, p in inspect.signature(route).parameters.items():
        assert "callable" not in str(p.annotation).lower() and name not in {"judge", "model", "approve", "llm"}
