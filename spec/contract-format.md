# Holdout contract format (v1)

A contract states what a handler call must durably change, given a starting state.
The verifier (V4) compares it against the effects captured during the call
and settles on one of three verdicts:

- **PASS**: every required effect is present, with direct evidence.
- **FAIL**: an effect is contradicted, or a closure or atomic set is broken.
- **UNKNOWN**: evidence is missing. UNKNOWN never counts as PASS.

## Fields

```yaml
- id: C-NN                 # unique; never quoted in the Build section
  handler: <handler name>
  trap: <optional, which board trap this covers>
  given: <starting state in words, or a fixture name>
  input: { ...handler inputs... }
  effects:                 # what MUST happen
    - id: e1
      resource: <logical resource from ## Resources>
      op: insert | update | delete | emit
      count: 1             # cardinality: an exact number, or "0..1", ">=1"
      match: { field: value or $input.x }      # which rows
      change: { field: "+N" | "-N" | value }   # for an update: the after-value or a delta
      event: <name>        # emit only
  atomic: [e1, e2]         # these all happen or none do (optional)
  closure: true            # any write not listed above is FAIL: undeclared write
  expect_error: <code>     # failure path: the handler raises this, and the effects list is empty
```

Notes:

- `closure: true` is how a trap like "touched a resource it must not" is caught. Use it on almost every contract.
- A failure-path contract has `expect_error` and `effects: []` with `closure: true`, which means nothing may change.
- `$input.x` refers to a handler input. `$given.x` refers to a value from the starting state.

## Worked example (toy domain, NOT the shop)

A wallet service with `transfer_credit(from, to, amount)`:

```yaml
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
```

How this example would settle:

- The handler debits A but crashes before crediting B: atomic set broken, so **FAIL**.
- The handler also writes to an `audit` table that isn't listed: **FAIL**, undeclared write.
- Capture lost the WAL for the step: **UNKNOWN**.
