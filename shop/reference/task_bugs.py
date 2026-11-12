"""S5 patch-task bugs: reference handlers with one fresh bug, used as the starting file of a "fix this" task.

Never shown to the builder agent, except as the one handler a patch task hands it. These bugs are NOT the
V6 mutants (the test set) or the S4 traps: the mutants must stay out of training data, so a test checks that
no patch here equals a mutant or a trap. They are patches of the reference source, so each is the correct
handler with exactly one thing wrong.

Read-only handlers have no patch task: the verifier judges database effects and errors, not return values, so
a wrong return value cannot be scored (list_orders), and view_shared_cart's logic lives in a helper.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import mutants


@dataclass(frozen=True)
class Bug:
    id: str
    module: str     # reference.handlers.<module> that defines the handler
    handler: str
    old: str        # exact text in the reference source (must occur exactly once)
    new: str
    why: str        # one line, for maintainers only


BUGS: tuple[Bug, ...] = (
    Bug("add_to_cart", "cart", "add_to_cart",
        'total = (line["qty"] if line else 0) + qty', "total = qty",
        "forgets what is already in the cart"),
    Bug("update_cart_item", "cart", "update_cart_item",
        'if qty > product["on_hand"]:', "if False:",
        "never checks stock"),
    Bug("remove_from_cart", "cart", "remove_from_cart",
        '        return {"cart_id": cart["id"], "product_id": product_id, "removed_qty": removed["qty"]}',
        '        conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = %s", (cart["id"],))\n'
        '        return {"cart_id": cart["id"], "product_id": product_id, "removed_qty": removed["qty"]}',
        "also detaches the coupon"),
    Bug("clear_cart", "cart", "clear_cart",
        'if cart["coupon_code"] is not None:', "if False:",
        "leaves the coupon on the cart"),
    Bug("apply_coupon", "coupons", "apply_coupon",
        "if used is not None:", "if False:",
        "lets a coupon be used twice"),
    Bug("remove_coupon", "coupons", "remove_coupon",
        '"UPDATE carts SET coupon_code = NULL WHERE id = %s"',
        '"UPDATE carts SET coupon_code = NULL, status = \'checked_out\' WHERE id = %s"',
        "also closes the cart"),
    Bug("checkout", "checkout", "checkout",
        'if any(l["qty"] > l["on_hand"] for l in lines):', "if False:",
        "never checks stock"),
    Bug("get_order", "orders", "get_order",
        '"SELECT * FROM orders WHERE id = %s AND user_id = %s", (order_id, user_id)',
        '"SELECT * FROM orders WHERE id = %s AND %s IS NOT NULL", (order_id, user_id)',
        "shows any user's order"),
    Bug("cancel_order", "orders", "cancel_order",
        'if order["status"] != "placed":', "if False:",
        "cancels an order twice"),
    Bug("share_cart", "share", "share_cart",
        '(token, cart["id"], user_id),', '(token, cart["id"], 0),',
        "records no owner for the link"),
    Bug("import_shared_cart", "share", "import_shared_cart",
        "DO UPDATE SET qty = cart_items.qty + EXCLUDED.qty", "DO UPDATE SET qty = EXCLUDED.qty",
        "overwrites quantities instead of adding"),
)


def patched_source(bug: Bug) -> str:
    """The reference handler's source with the bug applied exactly once."""
    return mutants.source(bug)  # same patch machinery as the V6 mutants (it only reads these fields)
