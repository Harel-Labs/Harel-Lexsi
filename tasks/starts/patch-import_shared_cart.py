"""Share handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401


def share_cart(conn, user_id: int) -> dict:
    """Create a share link for the open cart and return its token. The cart is not changed.

    Publishes a `cart.shared` event (insert a row into `outbox` with event `cart.shared`).
    """
    raise NotImplementedError


def view_shared_cart(conn, token: str) -> dict:
    """Return the shared cart's lines. Read-only.

    Fails with `not_found` if the token is unknown or revoked.
    """
    raise NotImplementedError


def _lines(conn, cart_id: int) -> list[dict]:
    return conn.execute(
        "SELECT product_id, qty FROM cart_items WHERE cart_id = %s ORDER BY product_id",
        (cart_id,),
    ).fetchall()


def _live_link(conn, token: str):
    link = conn.execute(
        "SELECT * FROM share_links WHERE token = %s AND NOT revoked", (token,)
    ).fetchone()
    if link is None:
        raise ShopError("not_found")
    return link


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


def import_shared_cart(conn, user_id: int, token: str) -> dict:
    """Copy the shared cart's lines into the caller's open cart.

    Quantities are added to lines already there. The owner's cart and the share link are not
    changed. The coupon is not copied. No stock is reserved.
    """
    with conn.transaction():
        link = _live_link(conn, token)
        lines = _lines(conn, link["cart_id"])
        cart = open_cart(conn, user_id, create=True)
        for l in lines:
            conn.execute(
                """INSERT INTO cart_items (cart_id, product_id, qty) VALUES (%s, %s, %s)
                   ON CONFLICT (cart_id, product_id) DO UPDATE SET qty = EXCLUDED.qty""",
                (cart["id"], l["product_id"], l["qty"]),
            )
        return {"cart_id": cart["id"], "imported_lines": len(lines)}
