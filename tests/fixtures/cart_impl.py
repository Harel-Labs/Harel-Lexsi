"""A scripted builder answer for app/handlers/cart.py, used by the manual-episode test."""
from app.errors import ShopError


def _open_cart(conn, user_id, create=False):
    cart = conn.execute("SELECT * FROM carts WHERE user_id = %s AND status = 'open'", (user_id,)).fetchone()
    if cart is None and create:
        cart = conn.execute("INSERT INTO carts (user_id) VALUES (%s) RETURNING *", (user_id,)).fetchone()
    return cart


def _product(conn, product_id):
    row = conn.execute(
        "SELECT p.id, i.on_hand FROM products p JOIN inventory i ON i.product_id = p.id "
        "WHERE p.id = %s AND p.active", (product_id,)).fetchone()
    if row is None:
        raise ShopError("not_found")
    return row


def _check_qty(qty, allow_zero=False):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < (0 if allow_zero else 1):
        raise ShopError("invalid_qty")


def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:
    _check_qty(qty)
    with conn.transaction():
        product = _product(conn, product_id)
        cart = _open_cart(conn, user_id, create=True)
        line = conn.execute("SELECT qty FROM cart_items WHERE cart_id = %s AND product_id = %s",
                            (cart["id"], product_id)).fetchone()
        total = (line["qty"] if line else 0) + qty
        if total > product["on_hand"]:
            raise ShopError("out_of_stock")
        conn.execute(
            "INSERT INTO cart_items (cart_id, product_id, qty) VALUES (%s, %s, %s) "
            "ON CONFLICT (cart_id, product_id) DO UPDATE SET qty = EXCLUDED.qty",
            (cart["id"], product_id, total))
        return {"cart_id": cart["id"], "product_id": product_id, "qty": total}


def update_cart_item(conn, user_id: int, product_id: int, qty: int) -> dict:
    _check_qty(qty, allow_zero=True)
    with conn.transaction():
        cart = _open_cart(conn, user_id)
        line = cart and conn.execute("SELECT qty FROM cart_items WHERE cart_id = %s AND product_id = %s",
                                     (cart["id"], product_id)).fetchone()
        if not line:
            raise ShopError("not_in_cart")
        if qty == 0:
            conn.execute("DELETE FROM cart_items WHERE cart_id = %s AND product_id = %s", (cart["id"], product_id))
        else:
            if qty > _product(conn, product_id)["on_hand"]:
                raise ShopError("out_of_stock")
            conn.execute("UPDATE cart_items SET qty = %s WHERE cart_id = %s AND product_id = %s",
                         (qty, cart["id"], product_id))
        return {"cart_id": cart["id"], "product_id": product_id, "qty": qty}


def remove_from_cart(conn, user_id: int, product_id: int) -> dict:
    with conn.transaction():
        cart = _open_cart(conn, user_id)
        removed = cart and conn.execute(
            "DELETE FROM cart_items WHERE cart_id = %s AND product_id = %s RETURNING qty",
            (cart["id"], product_id)).fetchone()
        if not removed:
            raise ShopError("not_in_cart")
        return {"cart_id": cart["id"], "product_id": product_id, "removed_qty": removed["qty"]}


def clear_cart(conn, user_id: int) -> dict:
    with conn.transaction():
        cart = _open_cart(conn, user_id)
        if cart is None:
            return {"cart_id": None, "cleared": 0}
        n = conn.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart["id"],)).rowcount
        conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = %s", (cart["id"],))
        return {"cart_id": cart["id"], "cleared": n}
