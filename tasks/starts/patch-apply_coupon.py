"""Coupon handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401


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


def apply_coupon(conn, user_id: int, code: str) -> dict:
    """Attach a coupon to the open cart.

    Fails with `invalid_coupon` if the coupon is unknown, inactive or expired. Fails with
    `coupon_already_used` if this user has already redeemed it on a placed order. A cart holds at
    most one coupon; applying a new one replaces the old one. Applying a coupon does not redeem it.
    """
    with conn.transaction():
        coupon = conn.execute(
            """SELECT code FROM coupons
               WHERE code = %s AND active AND (expires_at IS NULL OR expires_at > now())""",
            (code,),
        ).fetchone()
        if coupon is None:
            raise ShopError("invalid_coupon")
        cart = open_cart(conn, user_id)
        if cart is None:
            raise ShopError("empty_cart")
        used = conn.execute(
            "SELECT 1 FROM coupon_redemptions WHERE coupon_code = %s AND user_id = %s AND NOT released",
            (code, user_id),
        ).fetchone()
        if False:
            raise ShopError("coupon_already_used")
        conn.execute("UPDATE carts SET coupon_code = %s WHERE id = %s", (code, cart["id"]))
        return {"cart_id": cart["id"], "coupon_code": code}


def remove_coupon(conn, user_id: int) -> dict:
    """Detach the coupon from the open cart."""
    raise NotImplementedError
