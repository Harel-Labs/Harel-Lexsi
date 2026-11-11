import secrets

from app.errors import ShopError

from ._util import emit, open_cart


def _live_link(conn, token: str):
    link = conn.execute(
        "SELECT * FROM share_links WHERE token = %s AND NOT revoked", (token,)
    ).fetchone()
    if link is None:
        raise ShopError("not_found")
    return link


def _lines(conn, cart_id: int) -> list[dict]:
    return conn.execute(
        "SELECT product_id, qty FROM cart_items WHERE cart_id = %s ORDER BY product_id",
        (cart_id,),
    ).fetchall()


def share_cart(conn, user_id: int) -> dict:
    with conn.transaction():
        cart = open_cart(conn, user_id)
        if cart is None:
            raise ShopError("empty_cart")
        token = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO share_links (token, cart_id, created_by) VALUES (%s, %s, %s)",
            (token, cart["id"], user_id),
        )
        emit(conn, "cart.shared", {"cart_id": cart["id"], "user_id": user_id})
        return {"token": token, "cart_id": cart["id"]}


def view_shared_cart(conn, token: str) -> dict:
    link = _live_link(conn, token)
    return {"token": token, "lines": _lines(conn, link["cart_id"])}


def import_shared_cart(conn, user_id: int, token: str) -> dict:
    with conn.transaction():
        link = _live_link(conn, token)
        lines = _lines(conn, link["cart_id"])
        cart = open_cart(conn, user_id, create=True)
        for l in lines:
            conn.execute(
                """INSERT INTO cart_items (cart_id, product_id, qty) VALUES (%s, %s, %s)
                   ON CONFLICT (cart_id, product_id) DO UPDATE SET qty = cart_items.qty + EXCLUDED.qty""",
                (cart["id"], l["product_id"], l["qty"]),
            )
        return {"cart_id": cart["id"], "imported_lines": len(lines)}
