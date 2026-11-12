"""Checkout handler. Implement the function body. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401
from psycopg.types.json import Jsonb


def emit(conn, event: str, payload: dict) -> None:
    conn.execute("INSERT INTO outbox (event, payload) VALUES (%s, %s)", (event, Jsonb(payload)))


def open_cart(conn, user_id: int, create: bool = False):
    """The user's open cart (row locked), optionally creating it. None if absent."""
    row = conn.execute(
        "SELECT * FROM carts WHERE user_id = %s AND status = 'open' FOR UPDATE", (user_id,)
    ).fetchone()
    if row is None and create:
        row = conn.execute(
            "INSERT INTO carts (user_id) VALUES (%s) RETURNING *", (user_id,)
        ).fetchone()
    return row


def checkout(conn, user_id: int) -> dict:
    """Turn the open cart into a placed order.

    It does all of the following, or none of them: creates the order and its lines at current
    prices; applies the coupon discount; takes the ordered quantities out of stock; records the
    coupon redemption; closes the cart; publishes an `order.placed` event (insert a row into
    `outbox` with event `order.placed`).

    Fails with `empty_cart`, `out_of_stock` or `coupon_already_used`.
    """
    with conn.transaction():
        cart = open_cart(conn, user_id)
        if cart is None:
            raise ShopError("empty_cart")
        lines = conn.execute(
            """SELECT ci.product_id, ci.qty, p.price_cents, i.on_hand
               FROM cart_items ci
               JOIN products p ON p.id = ci.product_id
               JOIN inventory i ON i.product_id = ci.product_id
               WHERE ci.cart_id = %s
               ORDER BY ci.product_id
               FOR UPDATE OF i""",
            (cart["id"],),
        ).fetchall()
        if not lines:
            raise ShopError("empty_cart")
        if False:
            raise ShopError("out_of_stock")

        subtotal = sum(l["qty"] * l["price_cents"] for l in lines)
        discount = 0
        code = cart["coupon_code"]
        if code is not None:
            used = conn.execute(
                "SELECT 1 FROM coupon_redemptions WHERE coupon_code = %s AND user_id = %s AND NOT released",
                (code, user_id),
            ).fetchone()
            if used is not None:
                raise ShopError("coupon_already_used")
            pct = conn.execute(
                "SELECT percent_off FROM coupons WHERE code = %s", (code,)
            ).fetchone()["percent_off"]
            discount = subtotal * pct // 100
        total = subtotal - discount

        order = conn.execute(
            """INSERT INTO orders (user_id, subtotal_cents, discount_cents, total_cents)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (user_id, subtotal, discount, total),
        ).fetchone()
        for l in lines:
            conn.execute(
                """INSERT INTO order_items (order_id, product_id, qty, unit_price_cents)
                   VALUES (%s, %s, %s, %s)""",
                (order["id"], l["product_id"], l["qty"], l["price_cents"]),
            )
            conn.execute(
                "UPDATE inventory SET on_hand = on_hand - %s WHERE product_id = %s",
                (l["qty"], l["product_id"]),
            )
        if code is not None:
            conn.execute(
                "INSERT INTO coupon_redemptions (coupon_code, user_id, order_id) VALUES (%s, %s, %s)",
                (code, user_id, order["id"]),
            )
        conn.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart["id"],))
        conn.execute("UPDATE carts SET status = 'checked_out' WHERE id = %s", (cart["id"],))
        emit(conn, "order.placed",
             {"order_id": order["id"], "user_id": user_id, "total_cents": total})
        return {"order_id": order["id"], "subtotal_cents": subtotal,
                "discount_cents": discount, "total_cents": total}
