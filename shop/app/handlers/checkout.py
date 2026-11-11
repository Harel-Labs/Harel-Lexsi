"""Checkout handler. Implement the function body. See cart.py for the conventions."""
from app.errors import ShopError  # noqa: F401


def checkout(conn, user_id: int) -> dict:
    """Turn the open cart into a placed order.

    It does all of the following, or none of them: creates the order and its lines at current
    prices; applies the coupon discount; takes the ordered quantities out of stock; records the
    coupon redemption; closes the cart; publishes an `order.placed` event (insert a row into
    `outbox` with event `order.placed`).

    Fails with `empty_cart`, `out_of_stock` or `coupon_already_used`.
    """
    raise NotImplementedError
