# T2 baseline

- model: CohereLabs/tiny-aya-global
- spec_sha256: 2e3720f63a464e8257443943c29995b41810258f262668fd24ab4eb5d5799625
- revision: None
- split: all
- tasks: 24
- split_sha256: 5c98b1b98721e58d704473de1bee709ee063e4e8c624659981f626d751d998b9
- factory_version: None
- device: hosted https://router.huggingface.co/v1
- backend: api
- api_model: openai/CohereLabs/tiny-aya-global
- max_steps: 8
- max_new_tokens: 1024
- temperature: 0.9
- date: 2026-11-14

| set | n | verified | visible | gap | unknown | mean steps | wrote nothing | malformed calls |
|---|---|---|---|---|---|---|---|---|
| train | 16 | 3 (19%) | 7 (44%) | +25% | 0 | 6.0 | 0 | 0 |
| heldout | 8 | 1 (12%) | 4 (50%) | +38% | 0 | 4.88 | 0 | 0 |
| all | 24 | 4 (17%) | 11 (46%) | +29% | 0 | 5.62 | 0 | 0 |
| implement | 13 | 1 (8%) | 3 (23%) | +15% | 0 | 7.0 | 0 | 0 |
| patch | 11 | 3 (27%) | 8 (73%) | +45% | 0 | 4.0 | 0 | 0 |

| task | split | verifier | classes | visible (pass/fail) | steps | note |
|---|---|---|---|---|---|---|
| implement-add_to_cart | train | fail | missing_write, undeclared_write, wrong_final_state | 1/16 | 9 |  |
| implement-update_cart_item | heldout | fail | missing_write, undeclared_write, wrong_final_state | 1/1 | 9 |  |
| implement-remove_from_cart | train | fail | missing_write, wrong_final_state | 0/1 | 9 |  |
| implement-clear_cart | train | fail | broken_atomicity, undeclared_write | 1/0 | 2 |  |
| implement-apply_coupon | train | fail | missing_write, wrong_final_state | 1/7 | 9 |  |
| implement-remove_coupon | heldout | fail | missing_write, wrong_final_state | 0/1 | 9 |  |
| implement-checkout | train | fail | missing_event, missing_write, wrong_final_state | 1/4 | 9 |  |
| implement-get_order | train | pass | - | 1/0 | 6 |  |
| implement-list_orders | train | fail | wrong_final_state | 0/1 | 5 |  |
| implement-cancel_order | heldout | fail | missing_event, missing_write, wrong_final_state | 0/1 | 4 |  |
| implement-share_cart | train | fail | missing_event | 2/0 | 2 |  |
| implement-view_shared_cart | train | fail | wrong_final_state | 0/2 | 9 |  |
| implement-import_shared_cart | heldout | fail | missing_write, wrong_final_state | 1/1 | 9 |  |
| patch-add_to_cart | train | fail | undeclared_write, wrong_final_state | 16/1 | 9 |  |
| patch-update_cart_item | heldout | fail | undeclared_write, wrong_final_state | 2/0 | 2 |  |
| patch-remove_from_cart | train | fail | undeclared_write, wrong_final_state | 1/0 | 2 |  |
| patch-clear_cart | train | fail | missing_write, wrong_final_state | 0/1 | 9 |  |
| patch-apply_coupon | train | fail | missing_write, wrong_final_state | 4/4 | 9 |  |
| patch-remove_coupon | heldout | fail | wrong_final_state | 1/0 | 2 |  |
| patch-checkout | train | pass | - | 5/0 | 2 |  |
| patch-get_order | train | pass | - | 1/0 | 3 |  |
| patch-cancel_order | heldout | pass | - | 1/0 | 2 |  |
| patch-share_cart | train | fail | missing_write, undeclared_write | 2/0 | 2 |  |
| patch-import_shared_cart | heldout | fail | wrong_final_state | 2/0 | 2 |  |
