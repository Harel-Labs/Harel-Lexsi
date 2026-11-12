# Day 1 gate (V7): the verifier has teeth

**Result: PASS**

- contracts in the pool: 9
- reference handlers passing: 9/9
- traps caught with the expected class: 2/2
- mutants caught with the expected class: 17/17
- kill rate: 100.0% (19/19)
- UNKNOWN rate: 0.0% (0/28)

| kind | case | handler | verdict | classes found | ok | note |
|---|---|---|---|---|---|---|
| reference | F-01 | add_to_cart | pass | - | yes |  |
| reference | F-02 | checkout | pass | - | yes |  |
| reference | F-03 | add_to_cart | pass | - | yes |  |
| reference | M-01 | checkout | pass | - | yes |  |
| reference | M-02 | checkout | pass | - | yes |  |
| reference | M-03 | cancel_order | pass | - | yes |  |
| reference | M-04 | apply_coupon | pass | - | yes |  |
| reference | M-05 | share_cart | pass | - | yes |  |
| reference | M-06 | remove_from_cart | pass | - | yes |  |
| trap | reserve_on_add | add_to_cart | fail | undeclared_write | yes |  |
| trap | checkout_no_event | checkout | fail | missing_event | yes |  |
| mutant | add-freeze | add_to_cart | fail | missing_write, wrong_final_state | yes |  |
| mutant | add-divert | add_to_cart | fail | broken_atomicity, undeclared_write | yes |  |
| mutant | add-drop-line | add_to_cart | fail | broken_atomicity | yes |  |
| mutant | add-stale-qty | add_to_cart | fail | wrong_final_state | yes |  |
| mutant | remove-freeze | remove_from_cart | fail | missing_write | yes |  |
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
