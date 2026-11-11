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


def update_cart_item(conn, user_id: int, product_id: int, qty: int) -> dict:
    """Set the quantity of a line in the open cart. A quantity of 0 removes the line.

    Same stock check as `add_to_cart`.
    """
    raise NotImplementedError


def remove_from_cart(conn, user_id: int, product_id: int) -> dict:
    """Remove the line from the open cart. Fails with `not_in_cart` if the line is not there."""
    raise NotImplementedError


def clear_cart(conn, user_id: int) -> dict:
    """Remove every line and any applied coupon from the open cart. The cart itself remains."""
    raise NotImplementedError
