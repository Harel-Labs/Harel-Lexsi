import psycopg
import pytest

from tools.db import connect, migrate, reset


@pytest.fixture(scope="session")
def _db():
    try:
        conn = connect()
    except psycopg.OperationalError as e:
        pytest.exit(f"Postgres is not reachable ({e}). Run: cd shop && docker compose up -d", 2)
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn(_db):
    """A connection on a freshly seeded database."""
    reset(_db)
    return _db
