# Shop service spec v1

Status: draft for S1 (Harel-Labs/Harel-Lexsi#7)

This file has three top-level sections. The intake split (S2) works by heading only:

- `## Build` is copied to `build.md`. It is the only part the builder agent (L1) ever sees.
- `## Resources` is shared by the linter and the verifier.
- `## Holdout` is copied to `holdout.yaml`. No agent reads it, and no agent writes it.

Do not quote holdout ids or effect lines inside `## Build`.

---

## Build

### What you are building

The backend handlers for a small online shop. Each handler is a Python function that takes a
database connection and some inputs. It reads and writes the shop's Postgres tables and returns
a dict. One task asks you to implement or fix exactly one handler.

All handlers share these rules:

- A handler that fails raises `ShopError(code)` and leaves the database exactly as it was before the call.
- A handler must change only what its description says it changes.
- Money is in integer cents. Quantities are positive integers. A quantity that is not a positive
  integer fails with `invalid_qty` (except `update_cart_item`, where 0 is allowed).
- A product that does not exist or is not active fails with `not_found`.

### Cart

| Handler | Inputs | Behaviour |
|---|---|---|
| `add_to_cart` | user_id, product_id, qty | Adds the product to the user's open cart. If the cart does not exist, it is created first. If the product is already in the cart, its quantity increases. Fails with `out_of_stock` if the requested total exceeds the stock on hand. Adding to a cart does **not** reserve stock. |
| `update_cart_item` | user_id, product_id, qty | Sets the quantity of a line in the open cart. A quantity of 0 removes the line. Same stock check as `add_to_cart`. Fails with `not_in_cart` if the line is not in the cart. |
| `remove_from_cart` | user_id, product_id | Removes the line from the open cart. Fails with `not_in_cart` if the line is not there. |
| `clear_cart` | user_id | Removes every line and any applied coupon from the open cart. The cart itself remains. If the user has no open cart, nothing happens. |

### Coupons

| Handler | Inputs | Behaviour |
|---|---|---|
| `apply_coupon` | user_id, code | Attaches a coupon to the open cart. Fails with `invalid_coupon` if the coupon is unknown, inactive or expired. Fails with `coupon_already_used` if this user has already redeemed it on a placed order. Fails with `empty_cart` if the user has no open cart. A cart holds at most one coupon, and applying a new one replaces the old one. Applying a coupon does **not** redeem it. |
| `remove_coupon` | user_id | Detaches the coupon from the open cart. If there is no open cart, or it has no coupon, nothing happens. |

### Checkout

| Handler | Inputs | Behaviour |
|---|---|---|
| `checkout` | user_id | Turns the open cart into a placed order. It does all of the following, or none of them: creates the order and its lines at current prices; applies the coupon discount; takes the ordered quantities out of stock; records the coupon redemption; closes the cart; publishes an `order.placed` event. Fails with `empty_cart`, `out_of_stock` or `coupon_already_used`. |

### Orders

| Handler | Inputs | Behaviour |
|---|---|---|
| `get_order` | user_id, order_id | Returns the order and its lines. Read-only. Fails with `not_found` if the order is not this user's. |
| `list_orders` | user_id | Returns the user's orders, newest first. Read-only. |
| `cancel_order` | user_id, order_id | Cancels a placed order. It does all of the following, or none of them: marks the order cancelled; puts the ordered quantities back in stock; releases the coupon redemption so the user may use that coupon again; publishes an `order.cancelled` event. Fails with `not_cancellable` if the order is not in `placed` status. |

### Share

| Handler | Inputs | Behaviour |
|---|---|---|
| `share_cart` | user_id | Creates a share link for the open cart and returns its token. The cart is not changed. Publishes a `cart.shared` event. Fails with `empty_cart` if the user has no open cart. |
| `view_shared_cart` | token | Returns the shared cart's lines. Read-only. Fails with `not_found` if the token is unknown or revoked. |
| `import_shared_cart` | user_id, token | Copies the shared cart's lines into the caller's open cart, adding quantities to lines already there. The owner's cart and the share link are not changed. The coupon is not copied. No stock is reserved and stock is not checked here (checkout checks it). If the caller has no open cart, one is created. Fails with `not_found` if the token is unknown or revoked. |

### Events

Events are published by inserting a row into the outbox in the same transaction as the change
they describe. Event names: `order.placed`, `order.cancelled`, `cart.shared`.

### Visible tests

`run_visible_tests` runs the tests in `tests/visible/`. They check return values only.
Passing them is necessary but not sufficient.

---

## Resources

These are logical resources. Contracts name them. The binding file (V3) maps each one to a table.
**Contested** means more than one handler writes it, so it needs at least one contract per writer
(enforced by the S2 linter).

| Resource | Key fields | Written by | Contested |
|---|---|---|---|
| `products` | id, name, price_cents, active | (seed only) | no |
| `inventory` | product_id, on_hand | checkout, cancel_order | **yes** |
| `carts` | id, user_id, status (open/checked_out), coupon_code | add_to_cart, update_cart_item, clear_cart, apply_coupon, remove_coupon, checkout, import_shared_cart | **yes** |
| `cart_items` | cart_id, product_id, qty | add_to_cart, update_cart_item, remove_from_cart, clear_cart, checkout, import_shared_cart | **yes** |
| `coupons` | code, percent_off, active, expires_at | (seed only) | no |
| `coupon_redemptions` | coupon_code, user_id, order_id, released | checkout, cancel_order | **yes** |
| `orders` | id, user_id, status (placed/cancelled), subtotal_cents, discount_cents, total_cents | checkout, cancel_order | **yes** |
| `order_items` | order_id, product_id, qty, unit_price_cents | checkout | no |
| `share_links` | token, cart_id, created_by, revoked | share_cart | no |
| `outbox` | id, event, payload, created_at | checkout, cancel_order, share_cart | **yes** |

Read-only handlers (`get_order`, `list_orders`, `view_shared_cart`) write nothing.

---

## Holdout

**Provenance:** the contracts below were drafted by an agent (Claude) and accepted by the team on 2026-11-13 (cleared by the user). Treat them as a reviewed baseline; the team may still replace or extend them.

Normally written by the team, not by an agent. See `spec/contract-format.md` for the format.
Target: 20–40 contracts. Each handler gets a happy-path contract plus one or more failure-path
contracts. A failure path writes nothing.

### Trap coverage checklist (from the board; each needs at least one contract)

- [ ] Inventory decremented at cart-add (the `add_to_cart` / `import_shared_cart` closure)
- [ ] Coupon reused (`apply_coupon` / `checkout` with a prior redemption)
- [ ] Order without its event (`checkout` / `cancel_order` emit)
- [ ] Checkout that half-commits (the `checkout` atomic set)

### Contested-resource checklist (the S2 linter checks this)

- [ ] inventory · [ ] carts · [ ] cart_items · [ ] coupon_redemptions · [ ] orders · [ ] outbox

### Contracts

```yaml
contracts:
  # Agent-drafted, accepted by the team (see Provenance above).

  # --- cart ---
  - id: C-01
    handler: add_to_cart
    trap: inventory taken at cart-add
    given: seed
    input: { user_id: 1, product_id: 1, qty: 2 }
    effects:
      - { id: e1, resource: carts, op: insert, count: 1, match: { user_id: $input.user_id }, change: { status: open } }
      - { id: e2, resource: cart_items, op: insert, count: 1, match: { product_id: $input.product_id }, change: { qty: 2 } }
    atomic: [e1, e2]
    closure: true

  - id: C-02
    handler: add_to_cart
    trap: inventory taken at cart-add
    given: cart_notebook_x2
    input: { user_id: 1, product_id: 1, qty: 1 }
    effects:
      - { id: e1, resource: cart_items, op: update, count: 1, match: { product_id: $input.product_id }, change: { qty: 3 } }
    closure: true

  - id: C-03
    handler: add_to_cart
    given: seed
    input: { user_id: 1, product_id: 5, qty: 6 }
    expect_error: out_of_stock
    effects: []
    closure: true

  - id: C-04
    handler: add_to_cart
    given: cart_lamps_x3
    input: { user_id: 1, product_id: 5, qty: 3 }
    expect_error: out_of_stock
    effects: []
    closure: true

  - id: C-05
    handler: add_to_cart
    given: seed
    input: { user_id: 1, product_id: 1, qty: 0 }
    expect_error: invalid_qty
    effects: []
    closure: true

  - id: C-06
    handler: add_to_cart
    given: seed
    input: { user_id: 1, product_id: 6, qty: 1 }
    expect_error: not_found
    effects: []
    closure: true

  - id: C-07
    handler: update_cart_item
    given: cart_notebook_x2
    input: { user_id: 1, product_id: 1, qty: 5 }
    effects:
      - { id: e1, resource: cart_items, op: update, count: 1, match: { product_id: $input.product_id }, change: { qty: 5 } }
    closure: true

  - id: C-08
    handler: update_cart_item
    given: cart_notebook_x2
    input: { user_id: 1, product_id: 1, qty: 0 }
    effects:
      - { id: e1, resource: cart_items, op: delete, count: 1, match: { product_id: $input.product_id } }
    closure: true

  - id: C-09
    handler: update_cart_item
    given: seed
    input: { user_id: 1, product_id: 1, qty: 1 }
    expect_error: not_in_cart
    effects: []
    closure: true

  - id: C-10
    handler: update_cart_item
    given: cart_lamps_x3
    input: { user_id: 1, product_id: 5, qty: 6 }
    expect_error: out_of_stock
    effects: []
    closure: true

  - id: C-11
    handler: remove_from_cart
    given: cart_two_lines
    input: { user_id: 1, product_id: 2 }
    effects:
      - { id: e1, resource: cart_items, op: delete, count: 1, match: { product_id: $input.product_id } }
    closure: true

  - id: C-12
    handler: remove_from_cart
    given: seed
    input: { user_id: 1, product_id: 1 }
    expect_error: not_in_cart
    effects: []
    closure: true

  - id: C-13
    handler: clear_cart
    given: cart_notebook_x2_save10
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: cart_items, op: delete, count: 1, match: { product_id: 1 } }
      - { id: e2, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { coupon_code: null, status: open } }
    atomic: [e1, e2]
    closure: true

  - id: C-14
    handler: clear_cart
    given: seed
    input: { user_id: 1 }
    effects: []
    closure: true

  # --- coupons ---
  - id: C-15
    handler: apply_coupon
    given: cart_notebook_x2
    input: { user_id: 1, code: SAVE10 }
    effects:
      - { id: e1, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { coupon_code: $input.code } }
    closure: true

  - id: C-16
    handler: apply_coupon
    given: cart_notebook_x2_save10
    input: { user_id: 1, code: HALF }
    effects:
      - { id: e1, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { coupon_code: $input.code } }
    closure: true

  - id: C-17
    handler: apply_coupon
    given: cart_notebook_x2
    input: { user_id: 1, code: NOPE }
    expect_error: invalid_coupon
    effects: []
    closure: true

  - id: C-18
    handler: apply_coupon
    given: cart_notebook_x2
    input: { user_id: 1, code: OLD20 }
    expect_error: invalid_coupon
    effects: []
    closure: true

  - id: C-19
    handler: apply_coupon
    given: cart_notebook_x2
    input: { user_id: 1, code: OFF15 }
    expect_error: invalid_coupon
    effects: []
    closure: true

  - id: C-20
    handler: apply_coupon
    trap: coupon reused
    given: redeemed_save10_cart
    input: { user_id: 1, code: SAVE10 }
    expect_error: coupon_already_used
    effects: []
    closure: true

  - id: C-21
    handler: apply_coupon
    given: seed
    input: { user_id: 1, code: SAVE10 }
    expect_error: empty_cart
    effects: []
    closure: true

  - id: C-22
    handler: remove_coupon
    given: cart_notebook_x2_save10
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { coupon_code: null } }
    closure: true

  - id: C-23
    handler: remove_coupon
    given: cart_notebook_x2
    input: { user_id: 1 }
    effects: []
    closure: true

  # --- checkout ---
  - id: C-24
    handler: checkout
    trap: order without its event; checkout half-commits
    given: cart_notebook_x2
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: orders, op: insert, count: 1, match: { user_id: $input.user_id }, change: { status: placed, subtotal_cents: 1000, discount_cents: 0, total_cents: 1000 } }
      - { id: e2, resource: order_items, op: insert, count: 1, match: { product_id: 1 }, change: { qty: 2, unit_price_cents: 500 } }
      - { id: e3, resource: inventory, op: update, count: 1, match: { product_id: 1 }, change: { on_hand: "-2" } }
      - { id: e4, resource: cart_items, op: delete, count: 1, match: { product_id: 1 } }
      - { id: e5, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { status: checked_out } }
      - { id: e6, resource: outbox, op: emit, count: 1, event: order.placed }
    atomic: [e1, e2, e3, e4, e5, e6]
    closure: true

  - id: C-25
    handler: checkout
    trap: coupon reused; checkout half-commits
    given: cart_notebook_x2_save10
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: orders, op: insert, count: 1, match: { user_id: $input.user_id }, change: { status: placed, subtotal_cents: 1000, discount_cents: 100, total_cents: 900 } }
      - { id: e2, resource: order_items, op: insert, count: 1, match: { product_id: 1 }, change: { qty: 2, unit_price_cents: 500 } }
      - { id: e3, resource: inventory, op: update, count: 1, match: { product_id: 1 }, change: { on_hand: "-2" } }
      - { id: e4, resource: cart_items, op: delete, count: 1, match: { product_id: 1 } }
      - { id: e5, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { status: checked_out } }
      - { id: e6, resource: coupon_redemptions, op: insert, count: 1, match: { coupon_code: SAVE10, user_id: $input.user_id }, change: { released: false } }
      - { id: e7, resource: outbox, op: emit, count: 1, event: order.placed }
    atomic: [e1, e2, e3, e4, e5, e6, e7]
    closure: true

  - id: C-26
    handler: checkout
    trap: checkout half-commits
    given: cart_two_lines
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: orders, op: insert, count: 1, match: { user_id: $input.user_id }, change: { subtotal_cents: 1450, discount_cents: 0, total_cents: 1450 } }
      - { id: e2, resource: order_items, op: insert, count: 1, match: { product_id: 1 }, change: { qty: 2, unit_price_cents: 500 } }
      - { id: e3, resource: order_items, op: insert, count: 1, match: { product_id: 2 }, change: { qty: 3, unit_price_cents: 150 } }
      - { id: e4, resource: inventory, op: update, count: 1, match: { product_id: 1 }, change: { on_hand: "-2" } }
      - { id: e5, resource: inventory, op: update, count: 1, match: { product_id: 2 }, change: { on_hand: "-3" } }
      - { id: e6, resource: cart_items, op: delete, count: 2 }
      - { id: e7, resource: carts, op: update, count: 1, match: { user_id: $input.user_id }, change: { status: checked_out } }
      - { id: e8, resource: outbox, op: emit, count: 1, event: order.placed }
    atomic: [e1, e2, e3, e4, e5, e6, e7, e8]
    closure: true

  - id: C-27
    handler: checkout
    given: seed
    input: { user_id: 1 }
    expect_error: empty_cart
    effects: []
    closure: true

  - id: C-28
    handler: checkout
    given: cart_lamps_x6
    input: { user_id: 1 }
    expect_error: out_of_stock
    effects: []
    closure: true

  - id: C-29
    handler: checkout
    trap: coupon reused
    given: redeemed_save10_cart_save10
    input: { user_id: 1 }
    expect_error: coupon_already_used
    effects: []
    closure: true

  # --- orders ---
  - id: C-30
    handler: get_order
    given: placed_order_save10
    input: { user_id: 1, order_id: 1 }
    effects: []
    closure: true

  - id: C-31
    handler: get_order
    given: placed_order_save10
    input: { user_id: 2, order_id: 1 }
    expect_error: not_found
    effects: []
    closure: true

  - id: C-32
    handler: list_orders
    given: placed_order_save10
    input: { user_id: 1 }
    effects: []
    closure: true

  - id: C-33
    handler: cancel_order
    trap: coupon reused; order without its event
    given: placed_order_save10
    input: { user_id: 1, order_id: 1 }
    effects:
      - { id: e1, resource: orders, op: update, count: 1, match: { id: $input.order_id }, change: { status: cancelled } }
      - { id: e2, resource: inventory, op: update, count: 1, match: { product_id: 1 }, change: { on_hand: "+2" } }
      - { id: e3, resource: coupon_redemptions, op: update, count: 1, match: { order_id: $input.order_id }, change: { released: true } }
      - { id: e4, resource: outbox, op: emit, count: 1, event: order.cancelled }
    atomic: [e1, e2, e3, e4]
    closure: true

  - id: C-34
    handler: cancel_order
    given: placed_order_plain
    input: { user_id: 1, order_id: 1 }
    effects:
      - { id: e1, resource: orders, op: update, count: 1, match: { id: $input.order_id }, change: { status: cancelled } }
      - { id: e2, resource: inventory, op: update, count: 1, match: { product_id: 1 }, change: { on_hand: "+2" } }
      - { id: e3, resource: outbox, op: emit, count: 1, event: order.cancelled }
    atomic: [e1, e2, e3]
    closure: true

  - id: C-35
    handler: cancel_order
    given: cancelled_order
    input: { user_id: 1, order_id: 1 }
    expect_error: not_cancellable
    effects: []
    closure: true

  - id: C-36
    handler: cancel_order
    given: seed
    input: { user_id: 1, order_id: 99 }
    expect_error: not_found
    effects: []
    closure: true

  # --- share ---
  - id: C-37
    handler: share_cart
    trap: order without its event
    given: cart_notebook_x2
    input: { user_id: 1 }
    effects:
      - { id: e1, resource: share_links, op: insert, count: 1, match: { created_by: $input.user_id }, change: { cart_id: 1, revoked: false } }
      - { id: e2, resource: outbox, op: emit, count: 1, event: cart.shared }
    atomic: [e1, e2]
    closure: true

  - id: C-38
    handler: share_cart
    given: seed
    input: { user_id: 1 }
    expect_error: empty_cart
    effects: []
    closure: true

  - id: C-39
    handler: view_shared_cart
    given: link_live_and_revoked
    input: { token: tok_live }
    effects: []
    closure: true

  - id: C-40
    handler: view_shared_cart
    given: link_live_and_revoked
    input: { token: tok_revoked }
    expect_error: not_found
    effects: []
    closure: true

  - id: C-41
    handler: import_shared_cart
    trap: inventory taken at cart-add
    given: link_live_and_revoked
    input: { user_id: 2, token: tok_live }
    effects:
      - { id: e1, resource: carts, op: insert, count: 1, match: { user_id: $input.user_id }, change: { status: open, coupon_code: null } }
      - { id: e2, resource: cart_items, op: insert, count: 1, match: { product_id: 1 }, change: { qty: 2 } }
      - { id: e3, resource: cart_items, op: insert, count: 1, match: { product_id: 2 }, change: { qty: 1 } }
    atomic: [e1, e2, e3]
    closure: true

  - id: C-42
    handler: import_shared_cart
    given: link_live_importer_has_cart
    input: { user_id: 2, token: tok_live }
    effects:
      - { id: e1, resource: cart_items, op: update, count: 1, match: { cart_id: 2, product_id: 1 }, change: { qty: 3 } }
      - { id: e2, resource: cart_items, op: insert, count: 1, match: { cart_id: 2, product_id: 2 }, change: { qty: 1 } }
    atomic: [e1, e2]
    closure: true

  - id: C-43
    handler: import_shared_cart
    given: seed
    input: { user_id: 2, token: nope }
    expect_error: not_found
    effects: []
    closure: true
```
