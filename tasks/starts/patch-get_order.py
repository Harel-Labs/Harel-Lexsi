"""Order handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401


def get_order(conn, user_id: int, order_id: int) -> dict:
    """Return the order and its lines. Read-only.

    Fails with `not_found` if the order is not this user's.
    """
    order = conn.execute(
        "SELECT * FROM orders WHERE id = %s AND %s IS NOT NULL", (order_id, user_id)
    ).fetchone()
    if order is None:
        raise ShopError("not_found")
    order["items"] = conn.execute(
        """SELECT product_id, qty, unit_price_cents FROM order_items
           WHERE order_id = %s ORDER BY product_id""",
        (order_id,),
    ).fetchall()
    return order


def list_orders(conn, user_id: int) -> list[dict]:
    """Return the user's orders, newest first. Read-only."""
    raise NotImplementedError


def cancel_order(conn, user_id: int, order_id: int) -> dict:
    """Cancel a placed order.

    It does all of the following, or none of them: marks the order cancelled; puts the ordered
    quantities back in stock; releases the coupon redemption so the user may use that coupon
    again; publishes an `order.cancelled` event (insert a row into `outbox` with event
    `order.cancelled`).

    Fails with `not_cancellable` if the order is not in `placed` status.
    """
    raise NotImplementedError
