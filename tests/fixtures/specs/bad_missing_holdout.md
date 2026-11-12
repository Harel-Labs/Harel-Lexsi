# Toy spec (test fixture, not the shop)

## Build

A tiny service. `transfer` moves credit between two wallets and publishes an event.

## Resources

| Resource | Key fields | Written by | Contested |
|---|---|---|---|
| `wallets` | id, balance | transfer | **yes** |
| `ledger` | id, amount | transfer | no |
| `outbox` | id, event | transfer | no |

