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
- Money is in integer cents. Quantities are positive integers.

### Cart

| Handler | Inputs | Behaviour |
|---|---|---|
| `add_to_cart` | user_id, product_id, qty | Adds the product to the user's open cart. If the cart does not exist, it is created first. If the product is already in the cart, its quantity increases. Fails with `out_of_stock` if the requested total exceeds the stock on hand. Adding to a cart does **not** reserve stock. |
| `update_cart_item` | user_id, product_id, qty | Sets the quantity of a line in the open cart. A quantity of 0 removes the line. Same stock check as `add_to_cart`. |
| `remove_from_cart` | user_id, product_id | Removes the line from the open cart. Fails with `not_in_cart` if the line is not there. |
| `clear_cart` | user_id | Removes every line and any applied coupon from the open cart. The cart itself remains. |

### Coupons

| Handler | Inputs | Behaviour |
|---|---|---|
| `apply_coupon` | user_id, code | Attaches a coupon to the open cart. Fails with `invalid_coupon` if the coupon is unknown, inactive or expired. Fails with `coupon_already_used` if this user has already redeemed it on a placed order. A cart holds at most one coupon, and applying a new one replaces the old one. Applying a coupon does **not** redeem it. |
| `remove_coupon` | user_id | Detaches the coupon from the open cart. |

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
| `share_cart` | user_id | Creates a share link for the open cart and returns its token. The cart is not changed. Publishes a `cart.shared` event. |
| `view_shared_cart` | token | Returns the shared cart's lines. Read-only. Fails with `not_found` if the token is unknown or revoked. |
| `import_shared_cart` | user_id, token | Copies the shared cart's lines into the caller's open cart, adding quantities to lines already there. The owner's cart and the share link are not changed. The coupon is not copied. No stock is reserved. |

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

Written by the team, never by an agent. See `spec/contract-format.md` for the format.
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
  # --- cart ---

  # --- coupons ---

  # --- checkout ---

  # --- orders ---

  # --- share ---
```
