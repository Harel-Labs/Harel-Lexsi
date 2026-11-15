# Day 1 gate (V7): the verifier has teeth

**Result: PASS**

- contracts in the pool: 43
- reference handlers passing: 43/43
- traps caught with the expected class: 2/2
- mutants caught with the expected class: 17/17
- kill rate: 100.0% (19/19)
- UNKNOWN rate: 0.0% (0/62)

| kind | case | handler | verdict | classes found | ok | note |
|---|---|---|---|---|---|---|
| reference | C-01 | add_to_cart | pass | - | yes |  |
| reference | C-02 | add_to_cart | pass | - | yes |  |
| reference | C-03 | add_to_cart | pass | - | yes |  |
| reference | C-04 | add_to_cart | pass | - | yes |  |
| reference | C-05 | add_to_cart | pass | - | yes |  |
| reference | C-06 | add_to_cart | pass | - | yes |  |
| reference | C-07 | update_cart_item | pass | - | yes |  |
| reference | C-08 | update_cart_item | pass | - | yes |  |
| reference | C-09 | update_cart_item | pass | - | yes |  |
| reference | C-10 | update_cart_item | pass | - | yes |  |
| reference | C-11 | remove_from_cart | pass | - | yes |  |
| reference | C-12 | remove_from_cart | pass | - | yes |  |
| reference | C-13 | clear_cart | pass | - | yes |  |
| reference | C-14 | clear_cart | pass | - | yes |  |
| reference | C-15 | apply_coupon | pass | - | yes |  |
| reference | C-16 | apply_coupon | pass | - | yes |  |
| reference | C-17 | apply_coupon | pass | - | yes |  |
| reference | C-18 | apply_coupon | pass | - | yes |  |
| reference | C-19 | apply_coupon | pass | - | yes |  |
| reference | C-20 | apply_coupon | pass | - | yes |  |
| reference | C-21 | apply_coupon | pass | - | yes |  |
| reference | C-22 | remove_coupon | pass | - | yes |  |
| reference | C-23 | remove_coupon | pass | - | yes |  |
| reference | C-24 | checkout | pass | - | yes |  |
| reference | C-25 | checkout | pass | - | yes |  |
| reference | C-26 | checkout | pass | - | yes |  |
| reference | C-27 | checkout | pass | - | yes |  |
| reference | C-28 | checkout | pass | - | yes |  |
| reference | C-29 | checkout | pass | - | yes |  |
| reference | C-30 | get_order | pass | - | yes |  |
| reference | C-31 | get_order | pass | - | yes |  |
| reference | C-32 | list_orders | pass | - | yes |  |
| reference | C-33 | cancel_order | pass | - | yes |  |
| reference | C-34 | cancel_order | pass | - | yes |  |
| reference | C-35 | cancel_order | pass | - | yes |  |
| reference | C-36 | cancel_order | pass | - | yes |  |
| reference | C-37 | share_cart | pass | - | yes |  |
| reference | C-38 | share_cart | pass | - | yes |  |
| reference | C-39 | view_shared_cart | pass | - | yes |  |
| reference | C-40 | view_shared_cart | pass | - | yes |  |
| reference | C-41 | import_shared_cart | pass | - | yes |  |
| reference | C-42 | import_shared_cart | pass | - | yes |  |
| reference | C-43 | import_shared_cart | pass | - | yes |  |
| trap | reserve_on_add | add_to_cart | fail | undeclared_write | yes |  |
| trap | checkout_no_event | checkout | fail | missing_event | yes |  |
| mutant | add-freeze | add_to_cart | fail | missing_write, wrong_final_state | yes |  |
| mutant | add-divert | add_to_cart | fail | broken_atomicity, missing_write, undeclared_write, wrong_final_state | yes |  |
| mutant | add-drop-line | add_to_cart | fail | broken_atomicity, missing_write | yes |  |
| mutant | add-stale-qty | add_to_cart | fail | wrong_final_state | yes |  |
| mutant | remove-freeze | remove_from_cart | fail | missing_write, wrong_final_state | yes |  |
| mutant | apply-divert-coupon | apply_coupon | fail | undeclared_write | yes |  |
| mutant | checkout-freeze | checkout | fail | missing_event, missing_write | yes |  |
| mutant | checkout-orphan-order | checkout | fail | broken_atomicity | yes |  |
| mutant | checkout-drop-stock | checkout | fail | broken_atomicity | yes |  |
| mutant | checkout-stale-subtotal | checkout | fail | wrong_final_state | yes |  |
| mutant | checkout-drop-redemption | checkout | fail | broken_atomicity | yes |  |
| mutant | checkout-stale-discount | checkout | fail | wrong_final_state | yes |  |
| mutant | cancel-drop-restock | cancel_order | fail | broken_atomicity | yes |  |
| mutant | cancel-stale-restock | cancel_order | fail | wrong_final_state | yes |  |
| mutant | cancel-orphan-event | cancel_order | fail | missing_event | yes |  |
| mutant | share-orphan-event | share_cart | fail | missing_event | yes |  |
| mutant | share-divert-cart | share_cart | fail | undeclared_write | yes |  |

## Known open items
- Settlement counts each write but not the net result: a handler that writes and then undoes the write in the same step still PASSes (xfail test `test_write_then_undo_should_fail`).
