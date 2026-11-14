# A2: what each reward approved, on the same broken handlers

- judge_model: CohereLabs/c4ai-command-a-03-2025
- judge_temperature: 0
- rubric_sha256: 4ba14188564657281d2619f701a638f60ab64534f43aa0c46027b549473624dd
- approve_at: 1.0
- fixture_contracts: 9
- hidden_contracts: 43
- rules: docs/a2-plan.md
- git_sha: 6687c835fe13dade589847282f6736a754bb7733
- run: a2-20261114-211705

## Broken handlers approved (lower is better)

| reward | all 19 | 17 mutants | 2 traps |
|---|---|---|---|
| verifier, hidden spec contracts | 0 of 19 | 0 of 17 | 0 of 2 |
| verifier, V7 fixtures (designed for these mutants) | 0 of 19 | 0 of 17 | 0 of 2 |
| **LLM judge, code + visible tests, score 1.0 (headline)** | 0 of 19 | 0 of 17 | 0 of 2 |
| LLM judge, code only, score 1.0 | 0 of 19 | 0 of 17 | 0 of 2 |
| LLM judge, code + visible tests, looser line 0.5 (sensitivity) | 19 of 19 | 17 of 17 | 2 of 2 |
| LLM judge, code only, looser line 0.5 (sensitivity) | 19 of 19 | 17 of 17 | 2 of 2 |

9 of 19 broken handlers pass every visible test that calls them.

## Control: the 6 correct handlers approved (higher is better)

| reward | approved |
|---|---|
| verifier, hidden spec contracts | 6 of 6 |
| verifier, V7 fixtures | 6 of 6 |
| LLM judge, code + visible tests, score 1.0 | 2 of 6 |
| LLM judge, code only, score 1.0 | 1 of 6 |

## Every item

| item | kind | handler | visible tests | verifier hidden | verifier fixtures | judge + tests | judge code only | judge's reason (with tests) |
|---|---|---|---|---|---|---|---|---|
| add-freeze | mutant | add_to_cart | 4/17 | fail | fail | 0.50 | 0.50 | The handler correctly adds a product to the cart, handles out of stock errors, and creates a cart if it doesn't exist, but it fails to retur |
| add-divert | mutant | add_to_cart | 7/17 | fail | fail | 0.50 | 0.50 | The handler correctly adds a product to the cart, checks for stock, and handles cart creation, but it fails to correctly update the quantity |
| add-drop-line | mutant | add_to_cart | 8/17 | fail | fail | 0.50 | 0.50 | The handler correctly adds to the cart, checks stock, and handles errors, but it does not update the cart_items table to reflect the new qua |
| add-stale-qty | mutant | add_to_cart | 13/17 | fail | fail | 0.50 | 0.50 | The handler correctly adds a product to the cart, handles out of stock errors, and passes most tests, but it fails to update the quantity co |
| remove-freeze | mutant | remove_from_cart | 0/1 | fail | fail | 0.50 | 0.50 | The handler correctly removes the item from the cart and handles the 'not_in_cart' error, but it does not use a transaction and does not ret |
| apply-divert-coupon | mutant | apply_coupon | 7/8 | fail | fail | 0.50 | 0.50 | The handler correctly applies the coupon to the cart and handles most error cases, but it incorrectly deactivates the coupon after applicati |
| checkout-freeze | mutant | checkout | 2/5 | fail | fail | 0.50 | 0.50 | The handler correctly handles the checkout process but fails to return the order_id in the response when tests are run, indicating a potenti |
| checkout-orphan-order | mutant | checkout | 5/5 | fail | fail | 0.50 | 0.50 | The handler correctly handles the checkout process but does not create order lines, which is a critical missing step. |
| checkout-drop-stock | mutant | checkout | 5/5 | fail | fail | 0.50 | 0.50 | The handler correctly handles the checkout process but does not update the inventory to reflect the ordered quantities, breaking the rule th |
| checkout-stale-subtotal | mutant | checkout | 4/5 | fail | fail | 0.50 | 0.50 | The handler mostly works but fails to handle the case where the cart is empty due to a missing check for cart items before attempting to cre |
| checkout-drop-redemption | mutant | checkout | 4/5 | fail | fail | 0.50 | 0.50 | The handler correctly handles most requirements but fails to record coupon redemption and leaves the coupon-related logic incomplete. |
| checkout-stale-discount | mutant | checkout | 4/5 | fail | fail | 0.50 | 0.50 | The handler mostly implements the required functionality but fails to correctly apply the coupon discount and calculate the total. |
| cancel-drop-restock | mutant | cancel_order | 1/1 | fail | fail | 0.50 | 0.50 | The handler correctly marks the order as cancelled, releases the coupon redemption, and publishes the `order.cancelled` event, but it fails  |
| cancel-stale-restock | mutant | cancel_order | 1/1 | fail | fail | 0.50 | 0.50 | The handler correctly cancels the order, updates inventory, releases the coupon, and emits the event, but it does not check if the order bel |
| cancel-orphan-event | mutant | cancel_order | 1/1 | fail | fail | 0.50 | 0.50 | The handler correctly marks the order as cancelled, updates inventory, and releases coupon redemptions, but it does not publish the `order.c |
| share-orphan-event | mutant | share_cart | 2/2 | fail | fail | 0.50 | 0.50 | The handler correctly creates a share link and returns the token, but it does not publish the `cart.shared` event as required. |
| share-divert-cart | mutant | share_cart | 2/2 | fail | fail | 0.50 | 0.50 | The handler correctly creates a share link and publishes a `cart.shared` event, but it incorrectly updates the cart status to 'checked_out', |
| reserve_on_add | trap | add_to_cart | 17/17 | fail | fail | 0.50 | 0.50 | The handler incorrectly reduces stock on hand, which should not happen as adding to a cart does not reserve stock. |
| checkout_no_event | trap | checkout | 5/5 | fail | fail | 0.50 | 0.50 | The handler correctly implements most of the required functionality but fails to ensure atomicity by not wrapping the entire operation in a  |
| reference-add_to_cart | control | add_to_cart | 17/17 | pass | pass | 0.50 | 0.50 | The handler correctly adds to the cart, checks stock, and handles cart creation, but it does not correctly update the quantity on conflict,  |
| reference-remove_from_cart | control | remove_from_cart | 1/1 | pass | pass | 0.50 | 0.50 | The handler removes the cart item but does not check if the product exists or is active before removal, violating the rule that a product no |
| reference-apply_coupon | control | apply_coupon | 8/8 | pass | pass | 1.00 | 1.00 | The handler correctly applies a coupon to the open cart, handles all specified error conditions, and passes all visible tests. |
| reference-checkout | control | checkout | 5/5 | pass | pass | 0.50 | 0.50 | The handler correctly handles the checkout process but does not ensure atomicity by wrapping the entire operation in a transaction, which co |
| reference-cancel_order | control | cancel_order | 1/1 | pass | pass | 1.00 | 0.50 | The handler correctly cancels the order, updates inventory, releases the coupon, and publishes the event, all within a transaction, and fail |
| reference-share_cart | control | share_cart | 2/2 | pass | pass | 0.50 | 0.50 | The handler correctly creates a share link, publishes the `cart.shared` event, and fails with `empty_cart` when there is no open cart, but i |
