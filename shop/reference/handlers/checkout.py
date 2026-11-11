from app.errors import ShopError

from ._util import emit, open_cart


def checkout(conn, user_id: int) -> dict:
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
        if any(l["qty"] > l["on_hand"] for l in lines):
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
