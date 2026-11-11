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


def import_shared_cart(conn, user_id: int, token: str) -> dict:
    """Copy the shared cart's lines into the caller's open cart.

    Quantities are added to lines already there. The owner's cart and the share link are not
    changed. The coupon is not copied. No stock is reserved.
    """
    raise NotImplementedError
