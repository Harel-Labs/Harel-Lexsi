"""Trap: add_to_cart also takes stock out of inventory (adding to a cart must not reserve stock)."""
from reference.handlers import *  # noqa: F401,F403
from reference.handlers import add_to_cart as _add_to_cart


def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:
    with conn.transaction():
        result = _add_to_cart(conn, user_id, product_id, qty)
        conn.execute(
            "UPDATE inventory SET on_hand = GREATEST(on_hand - %s, 0) WHERE product_id = %s",
            (qty, product_id),
        )
        return result
