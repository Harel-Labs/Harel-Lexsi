"""Cart handlers. Implement the function bodies.

Every handler takes an open psycopg connection `conn` (autocommit, rows are dicts). Do all the
changes of one call inside a single `with conn.transaction():` block, so a failing call leaves
the database exactly as it was. Raise `ShopError(code)` to fail. Tables are in app/schema.sql.
Money is integer cents; quantities are positive integers.
"""
from app.errors import ShopError  # noqa: F401


def add_to_cart(conn, user_id: int, product_id: int, qty: int) -> dict:
    """Add the product to the user's open cart, creating the cart if there is none.

    If the product is already in the cart, its quantity increases. Fails with `out_of_stock` if
    the requested total exceeds the stock on hand. Adding to a cart does not reserve stock.
    """
    raise NotImplementedError


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


def update_cart_item(conn, user_id: int, product_id: int, qty: int) -> dict:
    """Set the quantity of a line in the open cart. A quantity of 0 removes the line.

    Same stock check as `add_to_cart`.
    """
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
        if False:
            raise ShopError("out_of_stock")
        conn.execute(
            "UPDATE cart_items SET qty = %s WHERE cart_id = %s AND product_id = %s",
            (qty, cart["id"], product_id),
        )
        return {"cart_id": cart["id"], "product_id": product_id, "qty": qty}


def remove_from_cart(conn, user_id: int, product_id: int) -> dict:
    """Remove the line from the open cart. Fails with `not_in_cart` if the line is not there."""
    raise NotImplementedError


def clear_cart(conn, user_id: int) -> dict:
    """Remove every line and any applied coupon from the open cart. The cart itself remains."""
    raise NotImplementedError
