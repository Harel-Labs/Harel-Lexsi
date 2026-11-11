"""Trap: checkout places the order but never publishes order.placed."""
from reference.handlers import *  # noqa: F401,F403
from reference.handlers import checkout as _checkout


def checkout(conn, user_id: int) -> dict:
    with conn.transaction():
        result = _checkout(conn, user_id)
        conn.execute("DELETE FROM outbox WHERE event = 'order.placed'")
        return result
