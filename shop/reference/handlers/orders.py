from app.errors import ShopError

from ._util import emit


def get_order(conn, user_id: int, order_id: int) -> dict:
    order = conn.execute(
        "SELECT * FROM orders WHERE id = %s AND user_id = %s", (order_id, user_id)
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
    return conn.execute(
        "SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC", (user_id,)
    ).fetchall()


def cancel_order(conn, user_id: int, order_id: int) -> dict:
    with conn.transaction():
        order = conn.execute(
            "SELECT * FROM orders WHERE id = %s AND user_id = %s FOR UPDATE",
            (order_id, user_id),
        ).fetchone()
        if order is None:
            raise ShopError("not_found")
        if order["status"] != "placed":
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
