# Holdout worksheet (S1)

A list of **scenarios** to choose from. It deliberately has no effects, counts or values: you work those
out from the Build section of `spec.md` and write the contracts yourself, in `## Holdout` of `spec.md`
(format: `contract-format.md`). Pick 20 to 40. Drop any you judge low value; add any this list misses.

## The loop

```bash
# after each batch of contracts (needs Postgres: cd shop && docker compose up -d)
uv run python -m harel_lexsi.verifier.gate --spec spec/spec.md
```

- A **reference** row marked **NO** in the `ok` column means your contract is wrong, or the handler behaves
  differently from what you wrote. Check the Build section, then the reference handler.
- A **mutant or trap** row marked **NO** with `NOT CAUGHT` in the note means no contract for that handler is strong enough. Look at the
  mutant's `why` in `shop/reference/mutants.py` and make a contract check that thing.
- `no contract covers this handler` means the handler has no contract yet.
- When every row is `yes`, run the linter, then rerun the gate with `--log docs/day1-gate.md`:

```bash
uv run python -m harel_lexsi.spec spec/spec.md --out /tmp/s1-build   # prints FAIL lines until it is clean
```

## Per-contract checklist

- [ ] **Values, not just rows.** If a total, quantity, status or flag matters, put it in `change:`. A contract that
      only says "a row was inserted" lets a wrong-value bug through (the V6 work showed this).
- [ ] **`closure: true`** unless there is a reason not to. It is what catches writes to the wrong place.
- [ ] **`atomic:`** lists everything that must happen together (checkout and cancel especially).
- [ ] **Events** are `op: emit` effects with the event name; list them in `atomic` only if you want a missing
      event reported as part of the set. (A missing event is reported on its own as `missing_event`.)
- [ ] **Failure path** = `expect_error: <code>`, `effects: []`, `closure: true` (nothing may change).
- [ ] **Read-only handlers** = `effects: []`, `closure: true`, no `expect_error`.
- [ ] Use a **`given:` fixture name** (a file in `spec/fixtures/<name>.sql`) for any starting state other than the
      seed. A misspelt name silently means "seed state", so read the gate row if a contract behaves oddly.
- [ ] Avoid `$given.x` in values for now: settlement cannot resolve it yet and reports UNKNOWN.
- [ ] Do not quote a contract id or its starting state in the Build section (the linter rejects it).

## Scenarios

`seed` = the seeded database with no cart. Fixtures in **bold** do not exist yet.

### Cart
| # | Handler | Scenario | Starting state |
|---|---|---|---|
| 1 | add_to_cart | no cart yet: cart and line are created | seed |
| 2 | add_to_cart | product already in cart: quantities add | `cart_notebook_x2` |
| 3 | add_to_cart | request above stock | seed (5 lamps) |
| 4 | add_to_cart | existing line plus new quantity exceeds stock | **cart with lamps** |
| 5 | add_to_cart | quantity 0 | seed |
| 6 | add_to_cart | inactive product | seed |
| 7 | update_cart_item | set a new quantity | `cart_notebook_x2` |
| 8 | update_cart_item | quantity 0 removes the line | `cart_notebook_x2` |
| 9 | update_cart_item | line not in cart | seed |
| 10 | update_cart_item | new quantity above stock | **cart with lamps** |
| 11 | remove_from_cart | remove one of two lines | **cart with two lines** |
| 12 | remove_from_cart | line not in cart | seed |
| 13 | clear_cart | lines and coupon are removed, cart stays | `cart_notebook_x2_save10` |
| 14 | clear_cart | no open cart: nothing happens | seed |

### Coupons
| # | Handler | Scenario | Starting state |
|---|---|---|---|
| 15 | apply_coupon | first coupon is attached, not redeemed | `cart_notebook_x2` |
| 16 | apply_coupon | a second coupon replaces the first | `cart_notebook_x2_save10` |
| 17 | apply_coupon | unknown code | `cart_notebook_x2` |
| 18 | apply_coupon | expired code (OLD20) | `cart_notebook_x2` |
| 19 | apply_coupon | inactive code (OFF15) | `cart_notebook_x2` |
| 20 | apply_coupon | already redeemed by this user | **live redemption** |
| 21 | apply_coupon | no open cart | seed |
| 22 | remove_coupon | coupon is detached | `cart_notebook_x2_save10` |
| 23 | remove_coupon | nothing to remove | `cart_notebook_x2` |

### Checkout
| # | Handler | Scenario | Starting state |
|---|---|---|---|
| 24 | checkout | one line, no coupon | `cart_notebook_x2` |
| 25 | checkout | coupon: discount, and the coupon recorded as used | `cart_notebook_x2_save10` |
| 26 | checkout | several lines | **cart with two lines** |
| 27 | checkout | no cart / empty cart | seed |
| 28 | checkout | a line exceeds stock | **cart above stock** |
| 29 | checkout | coupon already redeemed | **live redemption + cart with that coupon** |

### Orders
| # | Handler | Scenario | Starting state |
|---|---|---|---|
| 30 | get_order | own order (read-only) | `placed_order_save10` |
| 31 | get_order | someone else's order | `placed_order_save10` |
| 32 | list_orders | read-only | `placed_order_save10` |
| 33 | cancel_order | order with a coupon: restock, release, event | `placed_order_save10` |
| 34 | cancel_order | order without a coupon | **placed order, no coupon** |
| 35 | cancel_order | already cancelled | **cancelled order** |
| 36 | cancel_order | unknown order | seed |

### Share
| # | Handler | Scenario | Starting state |
|---|---|---|---|
| 37 | share_cart | link and event, cart unchanged | `cart_notebook_x2` |
| 38 | share_cart | no open cart | seed |
| 39 | view_shared_cart | read-only | **shared cart** |
| 40 | view_shared_cart | unknown or revoked token | **shared cart** / revoked |
| 41 | import_shared_cart | importer has no cart: cart and lines created | **shared cart** |
| 42 | import_shared_cart | importer already has the product: quantities add | **shared cart + importer cart** |
| 43 | import_shared_cart | unknown token | seed |

## The four board traps: where they are exercised

| Trap | Already exercised by | Make sure a contract covers it |
|---|---|---|
| Inventory taken at cart-add | S4 trap `reserve_on_add` | #1, #2 (closure on `add_to_cart`) |
| Coupon reused | V6 mutant `checkout-drop-redemption` | #20, #25, #29, #33 |
| Order without its event | S4 trap `checkout_no_event`; mutants `cancel-orphan-event`, `share-orphan-event` | #24, #33, #37 |
| Checkout half-commits | mutants `checkout-drop-stock`, `checkout-orphan-order`, `checkout-freeze` | #24, #25, #26 (`atomic`) |

The six contested tables (`inventory`, `carts`, `cart_items`, `coupon_redemptions`, `orders`, `outbox`) must each be
touched by at least one contract, or the linter fails.

## Starting-state fixtures still missing

These only set up data (`spec/fixtures/<name>.sql`); they contain no rules. Ask for any you need:

- cart with two lines (notebook and pen)
- cart with a few lamps (for the stock-limit cases on an existing line)
- cart with lines above stock (6 lamps)
- live coupon redemption (user 1 redeemed SAVE10) with, optionally, an open cart that has SAVE10 attached
- placed order without a coupon
- cancelled order
- shared cart with a known token, plus a revoked token, plus a second user's cart
