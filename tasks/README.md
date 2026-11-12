# Task set (S5)

`tasks.jsonl` is what the builder agent is trained and evaluated on: 13 *implement* tasks (one per shop
handler) and 11 *patch* tasks (a handler already written but with one fresh bug, in `starts/`). A task's
prompt comes from the Build section of `spec/spec.md` and never mentions a bug or a contract.

```bash
uv run python -m harel_lexsi.tasks build                       # regenerate (refused once frozen)
uv run python -m harel_lexsi.tasks check --allow-unfrozen      # structure, no leaks, bugs are not mutants
uv run python -m harel_lexsi.tasks check --spec spec/spec.md   # + every handler has a holdout contract and
                                                               #   every patch bug is caught (needs Postgres)
uv run python -m harel_lexsi.verifier.coverage --spec spec/spec.md   # V9: every holdout effect is planned
```

## Freezing the split (once, before any baseline run)

Do this after S1 is merged and `check --spec` is clean, and **before the first baseline run (T2)**:

```bash
uv run python -m harel_lexsi.tasks freeze --heldout update_cart_item,remove_coupon,cancel_order,import_shared_cart
uv run python -m harel_lexsi.tasks check --spec spec/spec.md
```

- The split is by **handler**: a held-out handler's implement and patch tasks both stay out of training.
- `split.json` records both id lists and the SHA-256 of `tasks.jsonl`; `check` fails if either moves.
- After the freeze, `build` refuses to run. Changing a task means deleting `split.json` in a visible commit,
  which is only acceptable if no baseline run has happened.

## What is kept out of training

The patch bugs (`shop/reference/task_bugs.py`) are not the V6 mutants or the S4 traps; those are the test set
(`check` fails if a bug equals one). Each patch start file keeps every other handler in its module as a stub.

## Open follow-up

`ShopSandbox.reset` (S6 harness) must place a patch task's `start` file into the episode workspace at its
`target` path before the agent starts. Implement tasks need nothing: the stub is already there.
