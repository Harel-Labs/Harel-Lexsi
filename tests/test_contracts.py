"""V3: the contract loader."""
import pytest
import yaml

from harel_lexsi.verifier import Binding, ContractError, load_binding, parse_contracts
from harel_lexsi.verifier.contracts import parse_cardinality

WALLET = yaml.safe_load("""
- id: X-01
  handler: transfer_credit
  given: wallet A has 100, wallet B has 0
  input: { from: A, to: B, amount: 30 }
  effects:
    - { id: e1, resource: wallets, op: update, count: 1, match: { id: A }, change: { balance: "-30" } }
    - { id: e2, resource: wallets, op: update, count: 1, match: { id: B }, change: { balance: "+30" } }
    - { id: e3, resource: ledger,  op: insert, count: 1, match: { from: A, to: B, amount: 30 } }
    - { id: e4, resource: outbox,  op: emit,   count: 1, event: credit.transferred }
  atomic: [e1, e2, e3, e4]
  closure: true
- id: X-02
  handler: transfer_credit
  given: wallet A has 10
  input: { from: A, to: B, amount: 30 }
  expect_error: insufficient_funds
  effects: []
  closure: true
""")
BINDING = Binding({"wallets": "wallets", "ledger": "ledger", "outbox": "outbox"})


def test_contract_format_worked_example_loads():
    cs = parse_contracts(WALLET, BINDING)
    assert [c.id for c in cs] == ["X-01", "X-02"]
    assert cs[0].effects[3].event == "credit.transferred" and cs[0].closure
    assert cs[1].expect_error == "insufficient_funds" and cs[1].effects == ()


def test_wrapped_and_empty_forms():
    assert len(parse_contracts({"contracts": WALLET})) == 2
    assert parse_contracts({"contracts": None}) == [] and parse_contracts(None) == []


@pytest.mark.parametrize("mutate, fragment", [
    (lambda c: c[0].update(surprise=1), "unknown field"),
    (lambda c: c[0]["effects"][0].update(surprise=1), "unknown field"),
    (lambda c: c[0]["effects"][0].update(op="upsert"), "op must be one of"),
    (lambda c: c[0]["effects"][0].update(count="many"), "bad count"),
    (lambda c: c[0]["effects"][3].pop("event"), "needs an event"),
    (lambda c: c[0]["effects"][0].update(resource="nope"), "no binding"),
    (lambda c: c[0].update(atomic=["e1", "zz"]), "unknown effect"),
    (lambda c: c[1].update(effects=[{"id": "e1", "resource": "wallets", "op": "insert"}]), "no effects"),
    (lambda c: c[1].update(id="X-01"), "duplicate contract ids"),
    (lambda c: c[0].pop("handler"), "missing 'handler'"),
])
def test_bad_contracts_are_rejected(mutate, fragment):
    data = yaml.safe_load(yaml.safe_dump(WALLET))
    mutate(data)
    with pytest.raises(ContractError, match=fragment):
        parse_contracts(data, BINDING)


def test_cardinality_forms():
    assert parse_cardinality(1).contains(1) and not parse_cardinality(1).contains(2)
    assert parse_cardinality("0..1").contains(0) and not parse_cardinality("0..1").contains(2)
    assert parse_cardinality(">=1").contains(5) and not parse_cardinality(">=1").contains(0)
    for bad in ("2..1", "abc", True, -1):
        with pytest.raises(ContractError):
            parse_cardinality(bad)


def test_binding_patterns_and_repo_binding_file(tmp_path):
    assert Binding({"order*": "orders"}).table_for("order_items") == "orders"
    shop = load_binding(__import__("pathlib").Path(__file__).parents[1] / "spec" / "binding.yaml")
    assert shop.table_for("outbox") == "outbox" and shop.resource_for("carts") == "carts"
    bad = tmp_path / "b.yaml"
    bad.write_text("resources: {a: b}\nextra: 1\n")
    with pytest.raises(ContractError):
        load_binding(bad)
