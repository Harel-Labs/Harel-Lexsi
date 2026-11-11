# Shop service (S3)

The world the L1 builder agent works in. Behaviour is defined in `../spec/spec.md`.

```
app/        what L1 sees: schema.sql, errors.py, handler stubs (raise NotImplementedError)
reference/  correct handlers. NEVER inside app/; L1 must not be able to read these
db/seed.sql the seeded catalog that reset restores
tools/      connect / migrate / reset / WAL check
tests/      happy path + failure path for every reference handler
```

## Run

```bash
cd shop
docker compose up -d                 # postgres:16, wal_level=logical, on localhost:5433
uv sync                              # Python 3.12 + psycopg 3
uv run python -m tools.migrate       # build schema + seed (destroys data)
uv run python -m tools.check_wal     # fails unless wal_level=logical and REPLICA IDENTITY FULL
uv run python -m tools.reset         # back to the seeded state (~10 ms)
uv run pytest                        # the gate: reference handlers run
```

Override the connection with `SHOP_DSN`.

## Notes for later tickets

- Handlers take an autocommit `conn` and open their own `with conn.transaction():`.
- **V1:** every table is `REPLICA IDENTITY FULL` already. Start the replication slot *after*
  `reset`, because reset truncates and reseeds (lots of WAL).
- `reset` truncates and reseeds in one transaction. It does not drop and recreate the database,
  because a replication slot is tied to its database.

## Visible tests and traps (S4)

- `app/tests/visible/` is what L1 can run. It checks returned dicts and `ShopError` codes only, never
  table contents, stock, the outbox or atomicity. Pick the handlers under test with
  `SHOP_HANDLERS` (default `app.handlers`):
  `SHOP_HANDLERS=reference.handlers uv run pytest app/tests/visible`.
- `reference/traps/` holds trap variants (inventory taken at cart-add, checkout without its
  `order.placed` event). They pass the visible tests and break a hidden contract. Never shown to L1.
- `tests/test_visible_gap.py` proves the gap: visible tests pass on reference and traps, fail on the
  stubs, while effect checks catch each trap.
