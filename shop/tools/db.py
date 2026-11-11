"""Database helpers: connect, migrate, reset, and checks. Not visible to the builder agent."""
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "app" / "schema.sql"
SEED_SQL = ROOT / "db" / "seed.sql"

DSN = os.environ.get("SHOP_DSN", "postgresql://shop:shop@localhost:5433/shop")

TABLES = [
    "products", "inventory", "coupons", "carts", "cart_items", "orders",
    "order_items", "coupon_redemptions", "share_links", "outbox",
]


def connect() -> psycopg.Connection:
    """Autocommit connection with dict rows. Handlers open their own transactions."""
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def migrate(conn: psycopg.Connection) -> None:
    """Drop everything and rebuild the schema, then seed it."""
    with conn.transaction():
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(SCHEMA_SQL.read_text())
        conn.execute(SEED_SQL.read_text())


def reset(conn: psycopg.Connection) -> None:
    """Return every table to the seeded state, in one transaction."""
    with conn.transaction():
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.execute(SEED_SQL.read_text())


def assert_wal_logical(conn: psycopg.Connection) -> None:
    """Raise unless wal_level is logical and every table keeps full before/after rows."""
    level = conn.execute("SELECT current_setting('wal_level') AS v").fetchone()["v"]
    if level != "logical":
        raise RuntimeError(f"wal_level is {level!r}, expected 'logical'")
    rows = conn.execute(
        """SELECT c.relname, c.relreplident FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = 'r'"""
    ).fetchall()
    found = {r["relname"] for r in rows}
    if found != set(TABLES):
        raise RuntimeError(f"unexpected tables: {sorted(found ^ set(TABLES))}")
    bad = [r["relname"] for r in rows if r["relreplident"] != "f"]
    if bad:
        raise RuntimeError(f"REPLICA IDENTITY is not FULL on: {bad}")


def state_hash(conn: psycopg.Connection) -> dict[str, str]:
    """One md5 per table over all its rows, so two database states can be compared."""
    out = {}
    for t in TABLES:
        out[t] = conn.execute(
            f"SELECT md5(coalesce(string_agg(to_jsonb(x)::text, ',' "
            f"ORDER BY to_jsonb(x)::text), '')) AS h FROM {t} x"
        ).fetchone()["h"]
    return out
