# T8 evaluation: trained against base

| set | tasks (n) | base | trained | change | p (paired bootstrap over tasks) |
|---|---|---|---|---|---|
| held-out, verified success | 8 | 12% | 12% | +0% | 1.000 |
| mutation set (patch tasks), verified success | 11 | 35% | 35% | +0% | 1.000 |
| held-out, visible tests pass | 8 | 45% | 55% | +10% | 0.075 |
| mutation set, visible tests pass | 11 | 75% | 82% | +7% | 0.256 |

| episodes | base | trained |
|---|---|---|
| heldout: episodes, share of writes refused as not Python | 40, 14% | 40, 5% |
| mutation: episodes, share of writes refused as not Python | 55, 12% | 55, 8% |

| task | n | base verified | trained verified |
|---|---|---|---|
| implement-cancel_order | 5 | 0% | 0% |
| implement-import_shared_cart | 5 | 0% | 0% |
| implement-remove_coupon | 5 | 0% | 0% |
| implement-update_cart_item | 5 | 0% | 0% |
| patch-add_to_cart | 5 | 40% | 20% |
| patch-apply_coupon | 5 | 60% | 100% |
| patch-cancel_order | 5 | 100% | 100% |
| patch-checkout | 5 | 0% | 0% |
| patch-clear_cart | 5 | 60% | 80% |
| patch-get_order | 5 | 100% | 80% |
| patch-import_shared_cart | 5 | 0% | 0% |
| patch-remove_coupon | 5 | 0% | 0% |
| patch-remove_from_cart | 5 | 0% | 0% |
| patch-share_cart | 5 | 20% | 0% |
| patch-update_cart_item | 5 | 0% | 0% |

# DeploymentGate: KEEP THE OLD MODEL

- held-out verified success: 12% -> 12% (n=8)
- mutation set: 35% -> 35% (n=11)
- held-out gain +0% is below the required +10%
- thresholds sha256: 4fd061d678c3a2ff61d8366ecfd6db17c4208f956722d38b6b004b5100f38b06
