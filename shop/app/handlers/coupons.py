"""Coupon handlers. Implement the function bodies. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401


def apply_coupon(conn, user_id: int, code: str) -> dict:
    """Attach a coupon to the open cart.

    Fails with `invalid_coupon` if the coupon is unknown, inactive or expired. Fails with
    `coupon_already_used` if this user has already redeemed it on a placed order. A cart holds at
    most one coupon; applying a new one replaces the old one. Applying a coupon does not redeem it.
    """
    raise NotImplementedError


def remove_coupon(conn, user_id: int) -> dict:
    """Detach the coupon from the open cart."""
    raise NotImplementedError
