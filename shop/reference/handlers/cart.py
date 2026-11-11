from app.errors import ShopError

from ._util import active_product, check_qty, open_cart


def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:
    check_qty(qty)
    with conn.transaction():
        product = active_product(conn, product_id)
        cart = open_cart(conn, user_id, create=True)
        line = conn.execute(
            "SELECT qty FROM cart_items WHERE cart_id = %s AND product_id = %s FOR UPDATE",
            (cart["id"], product_id),
        ).fetchone()
        total = (line["qty"] if line else 0) + qty
        if total > product["on_hand"]:
            raise ShopError("out_of_stock")
        conn.execute(
            """INSERT INTO cart_items (cart_id, product_id, qty) VALUES (%s, %s, %s)
               ON CONFLICT (cart_id, product_id) DO UPDATE SET qty = EXCLUDED.qty""",
            (cart["id"], product_id, total),
        )
        return {"cart_id": cart["id"], "product_id": product_id, "qty": total}


def update_cart_item(conn, user_id: int, product_id: int, qty: int) -> dict:
    check_qty(qty, allow_zero=True)
    with conn.transaction():
        cart = open_cart(conn, user_id)
        line = None
        if cart is not None:
            line = conn.execute(
                "SELECT qty FROM cart_items WHERE cart_id = %s AND product_id = %s FOR UPDATE",
                (cart["id"], product_id),
            ).fetchone()
        if line is None:
            raise ShopError("not_in_cart")
        if qty == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = %s AND product_id = %s",
                (cart["id"], product_id),
            )
            return {"cart_id": cart["id"], "product_id": product_id, "qty": 0}
        product = active_product(conn, product_id)
        if qty > product["on_hand"]:
            raise ShopError("out_of_stock")
        conn.execute(
            "UPDATE cart_items SET qty = %s WHERE cart_id = %s AND product_id = %s",
            (qty, cart["id"], product_id),
        )
        return {"cart_id": cart["id"], "product_id": product_id, "qty": qty}


def remove_from_cart(conn, user_id: int, product_id: int) -> dict:
    with conn.transaction():
        cart = open_cart(conn, user_id)
        removed = None
        if cart is not None:
            removed = conn.execute(
                "DELETE FROM cart_items WHERE cart_id = %s AND product_id = %s RETURNING qty",
                (cart["id"], product_id),
            ).fetchone()
        if removed is None:
            raise ShopError("not_in_cart")
        return {"cart_id": cart["id"], "product_id": product_id, "removed_qty": removed["qty"]}


def clear_cart(conn, user_id: int) -> dict:
    with conn.transaction():
        cart = open_cart(conn, user_id)
        if cart is None:
            return {"cart_id": None, "cleared": 0}
        cleared = conn.execute(
            "DELETE FROM cart_items WHERE cart_id = %s", (cart["id"],)
        ).rowcount
        if cart["coupon_code"] is not None:
            conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = %s", (cart["id"],))
        return {"cart_id": cart["id"], "cleared": cleared}
