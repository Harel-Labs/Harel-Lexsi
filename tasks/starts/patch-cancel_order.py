"""Order handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401
from psycopg.types.json import Jsonb


def get_order(conn, user_id: int, order_id: int) -> dict:
    """Return the order and its lines. Read-only.

    Fails with `not_found` if the order is not this user's.
    """
    raise NotImplementedError


def list_orders(conn, user_id: int) -> list[dict]:
    """Return the user's orders, newest first. Read-only."""
    raise NotImplementedError


def emit(conn, event: str, payload: dict) -> None:
    conn.execute("INSERT INTO outbox (event, payload) VALUES (%s, %s)", (event, Jsonb(payload)))


def cancel_order(conn, user_id: int, order_id: int) -> dict:
    """Cancel a placed order.

    It does all of the following, or none of them: marks the order cancelled; puts the ordered
    quantities back in stock; releases the coupon redemption so the user may use that coupon
    again; publishes an `order.cancelled` event (insert a row into `outbox` with event
    `order.cancelled`).

    Fails with `not_cancellable` if the order is not in `placed` status.
    """
    with conn.transaction():
        order = conn.execute(
            "SELECT * FROM orders WHERE id = %s AND user_id = %s FOR UPDATE",
            (order_id, user_id),
        ).fetchone()
        if order is None:
            raise ShopError("not_found")
        if False:
            raise ShopError("not_cancellable")
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s", (order_id,))
        conn.execute(
            """UPDATE inventory i SET on_hand = i.on_hand + oi.qty
               FROM order_items oi
               WHERE oi.order_id = %s AND i.product_id = oi.product_id""",
            (order_id,),
        )
        conn.execute(
            "UPDATE coupon_redemptions SET released = true WHERE order_id = %s AND NOT released",
            (order_id,),
        )
        emit(conn, "order.cancelled", {"order_id": order_id, "user_id": user_id})
        return {"order_id": order_id, "status": "cancelled"}
