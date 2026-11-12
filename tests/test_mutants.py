"""V6: every mutant FAILs with the class it is labelled with; the reference handlers PASS the same contracts."""
import importlib
import sys
from pathlib import Path

import psycopg
import pytest

from harel_lexsi.verifier import Verdict, classify, load_binding, load_holdout, replay, settle
from harel_lexsi.verifier.replay import SHOP

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(SHOP))

from reference import mutants as M  # noqa: E402

BINDING = load_binding(ROOT / "spec" / "binding.yaml")
CONTRACTS = {c.id: c for f in ("shop_contracts.yaml", "mutant_contracts.yaml")
             for c in load_holdout(ROOT / "tests" / "fixtures" / f, BINDING)}


@pytest.fixture(scope="module", autouse=True)
def postgres():
    """These tests prove the verifier has teeth, so a missing database is a failure, not a skip."""
    try:
        psycopg.connect("postgresql://shop:shop@localhost:5433/shop", connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.fail("Postgres is not reachable; run: cd shop && docker compose up -d")


def judge(contract_id, handlers):
    c = CONTRACTS[contract_id]
    s = replay(c, handlers)
    return settle(c, s.capture, s.error, BINDING)


# ---------- the mutation set itself ----------

def test_size_and_operators():
    assert 10 <= len(M.MUTANTS) <= 20
    assert {m.operator for m in M.MUTANTS} == set(M.OPERATORS)
    assert len({m.id for m in M.MUTANTS}) == len(M.MUTANTS)


def test_every_label_is_a_real_class_and_every_contract_exists():
    for m in M.MUTANTS:
        assert m.expect and m.expect <= set(M.CLASSES), m.id
        assert m.contract in CONTRACTS, m.id


@pytest.mark.parametrize("m", M.MUTANTS, ids=lambda m: m.id)
def test_patch_changes_the_reference_source(m):
    module = importlib.import_module(f"reference.handlers.{m.module}")
    import inspect
    assert M.source(m) != inspect.getsource(getattr(module, m.handler))


def test_mutants_are_kept_away_from_the_agent():
    app = SHOP / "app"
    for path in app.rglob("*.py"):
        assert "mutants" not in path.read_text(), path
    assert not (app / "mutants.py").exists()


# ---------- the verifier has teeth ----------

@pytest.mark.parametrize("cid", sorted({m.contract for m in M.MUTANTS}))
def test_reference_handlers_pass_every_contract_used_here(cid):
    s = judge(cid, importlib.import_module("reference.handlers"))
    assert s.verdict is Verdict.PASS, s


@pytest.mark.parametrize("m", M.MUTANTS, ids=lambda m: m.id)
def test_mutant_fails_with_its_label(m):
    s = judge(m.contract, M.as_handlers(m))
    assert s.verdict is Verdict.FAIL, f"{m.id} ({m.why}) was not caught: {s}"
    assert {f.failure_class.value for f in classify(s)} == m.expect, f"{m.id}: {s}"


# ---------- known gaps in the verifier (found while building V6) ----------

@pytest.mark.xfail(strict=True, reason="settlement counts each write but not the net result: "
                   "a write that is undone later in the same step still PASSes")
def test_write_then_undo_should_fail():
    import types
    ref = importlib.import_module("reference.handlers")

    def checkout_that_restores_stock(conn, user_id):
        with conn.transaction():
            out = ref.checkout(conn, user_id)
            conn.execute("UPDATE inventory SET on_hand = on_hand + 2 WHERE product_id = 1")
            return out

    s = judge("F-02", types.SimpleNamespace(checkout=checkout_that_restores_stock))
    assert s.verdict is Verdict.FAIL
