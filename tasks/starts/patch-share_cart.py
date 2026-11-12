"""Share handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401
import secrets
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


def share_cart(conn, user_id: int) -> dict:
    """Create a share link for the open cart and return its token. The cart is not changed.

    Publishes a `cart.shared` event (insert a row into `outbox` with event `cart.shared`).
    """
    with conn.transaction():
        cart = open_cart(conn, user_id)
        if cart is None:
            raise ShopError("empty_cart")
        token = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO share_links (token, cart_id, created_by) VALUES (%s, %s, %s)",
            (token, cart["id"], 0),
        )
        emit(conn, "cart.shared", {"cart_id": cart["id"], "user_id": user_id})
        return {"token": token, "cart_id": cart["id"]}


def view_shared_cart(conn, token: str) -> dict:
    """Return the shared cart's lines. Read-only.

    Fails with `not_found` if the token is unknown or revoked.
    """
    raise NotImplementedError


def import_shared_cart(conn, user_id: int, token: str) -> dict:
    """Copy the shared cart's lines into the caller's open cart.

    Quantities are added to lines already there. The owner's cart and the share link are not
    changed. The coupon is not copied. No stock is reserved.
    """
    raise NotImplementedError
