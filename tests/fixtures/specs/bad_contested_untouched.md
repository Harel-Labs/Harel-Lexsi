# Toy spec (test fixture, not the shop)

## Build

A tiny service. `transfer` moves credit between two wallets and publishes an event.

## Resources

| Resource | Key fields | Written by | Contested |
|---|---|---|---|
| `wallets` | id, balance | transfer | **yes** |
| `ledger` | id, amount | transfer | no |
| `outbox` | id, event | transfer | no |

## Holdout

```yaml
contracts:
  - id: X-01
    handler: transfer
    given: wallet A has 100, wallet B has 0
    input: { from: A, to: B, amount: 30 }
    effects:
      - { id: e2, resource: ledger, op: insert, count: 1 }
      - { id: e3, resource: outbox, op: emit, count: 1, event: credit.transferred }
    atomic: [e2, e3]
    closure: true
```
