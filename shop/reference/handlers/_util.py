from psycopg.types.json import Jsonb

from app.errors import ShopError


def check_qty(qty, allow_zero: bool = False) -> None:
    low = 0 if allow_zero else 1
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < low:
        raise ShopError("invalid_qty")


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


def active_product(conn, product_id: int):
    """An active product with its stock on hand, or ShopError(not_found)."""
    row = conn.execute(
        """SELECT p.id, p.price_cents, i.on_hand FROM products p
           JOIN inventory i ON i.product_id = p.id
           WHERE p.id = %s AND p.active""",
        (product_id,),
    ).fetchone()
    if row is None:
        raise ShopError("not_found")
    return row


def emit(conn, event: str, payload: dict) -> None:
    conn.execute("INSERT INTO outbox (event, payload) VALUES (%s, %s)", (event, Jsonb(payload)))
