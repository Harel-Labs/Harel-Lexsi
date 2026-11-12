"""V7: the Day 1 gate passes on the real verifier and FAILS when the verifier or the contracts are rigged."""
import dataclasses
import sys

import pytest

from harel_lexsi.verifier import Settlement, Verdict, load_binding, load_holdout
from harel_lexsi.verifier import gate as G
from harel_lexsi.verifier.replay import SHOP

sys.path.insert(0, str(SHOP))
from reference import mutants as M  # noqa: E402

BINDING = load_binding(G.DEFAULT_BINDING)
CONTRACTS = [c for f in G.DEFAULT_CONTRACTS for c in load_holdout(f, BINDING)]


@pytest.fixture(scope="module", autouse=True)
def postgres():
    """The gate is about teeth, so a missing database is a failure, never a skip."""
    try:
        G.check_database()
    except G.GateError as e:
        pytest.fail(str(e))


@pytest.fixture(scope="module")
def real_report():
    return G.run_gate(CONTRACTS, BINDING)


def names(report):
    return {o.case.name for o in report.failures}


# ---------- the gate passes on the real verifier ----------

def test_gate_passes_today(real_report):
    assert real_report.ok, real_report.render()


def test_gate_counts(real_report):
    assert [o.ok for o in real_report.of("reference")] == [True] * len(CONTRACTS)
    assert len(real_report.of("trap")) == len(G.TRAPS) == 2
    assert len(real_report.of("mutant")) == len(M.MUTANTS)
    assert real_report.kill_rate == 1.0
    assert real_report.unknown_rate == 0.0


def test_report_names_every_case_and_the_known_gap(real_report):
    text = real_report.render()
    assert "**Result: PASS**" in text
    assert "kill rate: 100.0%" in text and "UNKNOWN rate: 0.0%" in text
    for m in M.MUTANTS:
        assert m.id in text
    assert "write" in text.split("## Known open items")[1]


# ---------- planted checks: the gate must go red when something is rigged ----------

def test_weakened_contracts_let_mutants_through():
    """Contracts that stop checking for undeclared writes: the divert mutants are no longer caught."""
    weak = [dataclasses.replace(c, closure=False) for c in CONTRACTS]
    report = G.run_gate(weak, BINDING)
    assert not report.ok
    assert report.kill_rate < 1.0
    assert {"add-divert", "apply-divert-coupon", "share-divert-cart"} <= names(report)


def test_a_verifier_that_always_passes_is_caught():
    def always_pass(contract, capture, error, binding):
        return Settlement(contract.id, Verdict.PASS, "rigged")

    report = G.run_gate(CONTRACTS, BINDING, settle=always_pass)
    assert not report.ok
    assert report.kill_rate == 0.0
    assert {m.id for m in M.MUTANTS} <= names(report)
    assert all(o.ok for o in report.of("reference"))  # it does pass the correct handlers


def test_a_verifier_that_always_fails_is_caught():
    def always_fail(contract, capture, error, binding):
        return Settlement(contract.id, Verdict.FAIL, "rigged")

    report = G.run_gate(CONTRACTS, BINDING, settle=always_fail)
    assert not report.ok
    assert not any(o.ok for o in report.of("reference"))  # correct handlers get rejected


def test_unknown_fails_the_gate():
    def unknown(contract, capture, error, binding):
        return Settlement(contract.id, Verdict.UNKNOWN, "no evidence")

    report = G.run_gate(CONTRACTS, BINDING, settle=unknown)
    assert not report.ok
    assert report.unknown_rate == 1.0
    assert report.kill_rate == 0.0  # unknown is never a kill


def test_a_handler_no_contract_covers_is_not_killed():
    only_checkout = [c for c in CONTRACTS if c.handler == "checkout"]
    report = G.run_gate(only_checkout, BINDING)
    assert not report.ok
    uncovered = [o for o in report.of("mutant") if o.case.handler != "checkout"]
    assert uncovered and all("no contract covers" in o.note for o in uncovered)


def test_caught_with_the_wrong_class_is_not_a_kill():
    m = next(x for x in M.MUTANTS if x.id == "add-freeze")
    wrong = G.Case("mutant", "add-freeze", "add_to_cart", M.as_handlers(m),
                   frozenset({"wrong_cardinality"}))  # the real label is missing_write
    report = G.run_gate(CONTRACTS, BINDING, cases=[wrong])
    assert report.outcomes[0].verdict == "fail" and not report.outcomes[0].ok
    assert "expected" in report.outcomes[0].note


# ---------- the command line ----------

def test_cli_passes_and_writes_the_log(tmp_path, capsys):
    log = tmp_path / "gate.md"
    assert G.main(["--log", str(log)]) == 0
    assert "**Result: PASS**" in log.read_text()
    assert "Day 1 gate" in capsys.readouterr().out


def test_cli_without_a_database_exits_2_with_a_message(monkeypatch, capsys):
    monkeypatch.setenv("SHOP_DSN", "postgresql://shop:shop@localhost:5999/shop?connect_timeout=2")
    assert G.main([]) == 2
    err = capsys.readouterr().err
    assert "COULD NOT RUN" in err and "docker compose up" in err


def test_cli_with_no_contracts_exits_2(tmp_path, capsys):
    empty = tmp_path / "empty.yaml"
    empty.write_text("contracts: []\n")
    assert G.main(["--contracts", str(empty)]) == 2
    assert "no contracts" in capsys.readouterr().err
