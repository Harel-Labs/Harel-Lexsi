"""Correct reference handlers. Never shown to the builder agent (it lives outside app/)."""
from .cart import add_to_cart, clear_cart, remove_from_cart, update_cart_item
from .checkout import checkout
from .coupons import apply_coupon, remove_coupon
from .orders import cancel_order, get_order, list_orders
from .share import import_shared_cart, share_cart, view_shared_cart

__all__ = [
    "add_to_cart", "update_cart_item", "remove_from_cart", "clear_cart",
    "apply_coupon", "remove_coupon", "checkout",
    "get_order", "list_orders", "cancel_order",
    "share_cart", "view_shared_cart", "import_shared_cart",
]
