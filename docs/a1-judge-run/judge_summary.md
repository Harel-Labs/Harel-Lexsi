# A1: LLM-judge reward on the T2 trajectories

- judge_model: CohereLabs/c4ai-command-a-03-2025
- base_url: https://router.huggingface.co/v1
- temperature: 0
- approve_at: 0.5
- rubric_sha256: 4ba14188564657281d2619f701a638f60ab64534f43aa0c46027b549473624dd
- prompt_file: judge_prompt_v1.txt
- run: hosted-tiny-aya-v3-t09-20261114

| set | n | judge approved | of those, verifier PASS | of those, verifier FAIL | judge UNKNOWN | verifier PASS (all) |
|---|---|---|---|---|---|---|
| train | 16 | 8 | 3 | 5 | 0 | 3 |
| heldout | 8 | 5 | 1 | 4 | 0 | 1 |
| all | 24 | 13 | 4 | 9 | 0 | 4 |

By judge score (does not depend on where the approval line is drawn):

| judge score | n | verifier PASS | verifier FAIL |
|---|---|---|---|
| 1.00 | 4 | 4 | 0 |
| 0.50 | 9 | 0 | 9 |
| 0.00 | 11 | 0 | 11 |

At the fixed line (score >= 0.5) the judge approved 13 of 24 trajectories; the verifier passed 4. Two numbers with n, never blended.

| task | split | judge | verifier | visible | why |
|---|---|---|---|---|---|
| implement-add_to_cart | train | 0.00 | fail | fail | The final handler does not check if the product is active, does not handle the case when the product is not in |
| implement-update_cart_item | heldout | 0.50 | fail | fail | The handler correctly updates the cart item quantity and removes the item when the quantity is 0, but it does  |
| implement-remove_from_cart | train | 0.00 | fail | fail | The final handler does not correctly implement the task and breaks the rules by publishing an event when the t |
| implement-clear_cart | train | 0.00 | fail | ok | The handler incorrectly deletes the entire cart and its associated coupons instead of just clearing the cart i |
| implement-apply_coupon | train | 0.00 | fail | fail | The final handler does not meet the requirements and breaks several rules. |
| implement-remove_coupon | heldout | 0.00 | fail | fail | The final handler deletes the cart and its items, which is not what the task says it should do. |
| implement-checkout | train | 0.00 | fail | fail | The final version of the handler does not compile and fails to address the task requirements. |
| implement-get_order | train | 1.00 | pass | ok | The final handler correctly fetches the order and its items, checks ownership, and returns the expected data w |
| implement-list_orders | train | 0.50 | fail | fail | The handler is read-only and returns the user's orders, but it does not include order items or handle the 'new |
| implement-cancel_order | heldout | 0.00 | fail | fail | The handler does not correctly handle the order cancellation process, including incorrect SQL queries and miss |
| implement-share_cart | train | 0.50 | fail | ok | The handler does not publish the `cart.shared` event as required by the task. |
| implement-view_shared_cart | train | 0.00 | fail | fail | The final handler does not correctly implement the task as it includes unnecessary database writes and returns |
| implement-import_shared_cart | heldout | 0.00 | fail | fail | The final handler does not implement the task correctly, as it fails to handle the case where the caller has n |
| patch-add_to_cart | train | 0.50 | fail | fail | The handler correctly handles adding a product to the cart, creating the cart if necessary, and checks for sto |
| patch-update_cart_item | heldout | 0.50 | fail | ok | The handler correctly updates the cart item quantity and handles removal when qty is 0, but it does not perfor |
| patch-remove_from_cart | train | 0.50 | fail | ok | The handler does not ensure the database is left unchanged on failure and unnecessarily resets the coupon code |
| patch-clear_cart | train | 0.00 | fail | fail | The final implementation deletes the cart itself and does not correctly handle the removal of applied coupons. |
| patch-apply_coupon | train | 0.00 | fail | fail | The final version of the handler does not compile due to a syntax error in the SQL query. |
| patch-remove_coupon | heldout | 0.50 | fail | ok | The handler does not correctly handle the case where there is no open cart or no coupon, and it incorrectly se |
| patch-checkout | train | 1.00 | pass | ok | The handler correctly implements the required functionality, including all necessary database operations and e |
| patch-get_order | train | 1.00 | pass | ok | The handler correctly checks if the order belongs to the user and returns the order with its lines, passing al |
| patch-cancel_order | heldout | 1.00 | pass | ok | The handler correctly cancels the order, updates inventory, releases the coupon, and publishes the event, all  |
| patch-share_cart | train | 0.50 | fail | ok | The handler correctly creates a share link, publishes the `cart.shared` event, and fails with `empty_cart` whe |
| patch-import_shared_cart | heldout | 0.50 | fail | ok | The handler correctly copies shared cart lines into the caller's open cart, but it does not add quantities to  |
