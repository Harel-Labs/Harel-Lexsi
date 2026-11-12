"""V6 mutation set: reference handlers with exactly one thing broken. Never shown to the builder agent.

Each mutant is the reference handler's own source with one patch applied, so the only difference
from a correct handler is the patch. The verifier must FAIL every mutant with the class in `expect`.

Operators:
  freeze  the handler reports success but changes nothing
  divert  a write lands somewhere it must not (wrong row, or a resource the task must not touch)
  orphan  the main change is made but its companion (a line, an event) is left out
  drop    one write of a set that must happen together is skipped
  stale   a write happens but with a wrong value (ignores quantity, discount, ...)

`expect` holds failure-class names (the values of the verifier's FailureClass). `contract` names the
test-fixture contract the mutant is judged against (tests/fixtures/*.yaml); those fixtures check the
verifier machinery and are not the holdout.
"""
from __future__ import annotations

import importlib
import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass

OPERATORS = ("freeze", "divert", "orphan", "drop", "stale")
CLASSES = ("undeclared_write", "missing_write", "missing_event",
           "broken_atomicity", "wrong_cardinality", "wrong_final_state")


class MutantError(Exception):
    """A patch no longer applies exactly once, e.g. because the reference handler changed."""


@dataclass(frozen=True)
class Mutant:
    id: str
    operator: str
    module: str          # reference.handlers.<module> that defines the handler
    handler: str
    contract: str        # fixture contract id it is judged against
    expect: frozenset[str]
    old: str             # exact text in the reference source
    new: str             # what replaces it
    why: str             # one line: what is wrong, in plain words


def _m(id, operator, module, handler, contract, expect, old, new, why):  # noqa: A002
    return Mutant(id, operator, module, handler, contract, frozenset(expect), old, new, why)


# `_skip(...)` is injected by build(): it accepts any arguments and does nothing, so replacing
# `conn.execute(` or `emit(` with it removes exactly that statement.
MUTANTS: tuple[Mutant, ...] = (
    # ----- add_to_cart (judged by F-01: new cart + one line of 2) -----
    _m("add-freeze", "freeze", "cart", "add_to_cart", "F-01", ["missing_write"],
       '        product = active_product(conn, product_id)\n',
       '        return {"cart_id": 0, "product_id": product_id, "qty": qty}\n'
       '        product = active_product(conn, product_id)\n',
       "returns success without creating the cart or the line"),
    _m("add-divert", "divert", "cart", "add_to_cart", "F-01", ["undeclared_write", "broken_atomicity"],
       "cart = open_cart(conn, user_id, create=True)",
       "cart = open_cart(conn, user_id + 1, create=True)",
       "puts the line in another user's cart"),
    _m("add-drop-line", "drop", "cart", "add_to_cart", "F-01", ["broken_atomicity"],
       '        conn.execute(\n            """INSERT INTO cart_items',
       '        _skip(\n            """INSERT INTO cart_items',
       "creates the cart but never writes the line"),
    _m("add-stale-qty", "stale", "cart", "add_to_cart", "F-01", ["wrong_final_state"],
       '(cart["id"], product_id, total),',
       '(cart["id"], product_id, 1),',
       "always stores quantity 1"),

    # ----- remove_from_cart (judged by M-06) -----
    _m("remove-freeze", "freeze", "cart", "remove_from_cart", "M-06", ["missing_write"],
       "        removed = None\n",
       '        return {"cart_id": cart["id"], "product_id": product_id, "removed_qty": 1}\n'
       "        removed = None\n",
       "says the line was removed but leaves it in the cart"),

    # ----- apply_coupon (judged by M-04) -----
    _m("apply-divert-coupon", "divert", "coupons", "apply_coupon", "M-04", ["undeclared_write"],
       '        return {"cart_id": cart["id"], "coupon_code": code}',
       '        conn.execute("UPDATE coupons SET active = false WHERE code = %s", (code,))\n'
       '        return {"cart_id": cart["id"], "coupon_code": code}',
       "applying a coupon also switches the coupon off for everyone"),

    # ----- checkout (judged by F-02, M-01, M-02) -----
    _m("checkout-freeze", "freeze", "checkout", "checkout", "F-02", ["missing_write", "missing_event"],
       "        order = conn.execute(\n",
       '        return {"order_id": 0, "subtotal_cents": subtotal, "discount_cents": discount, '
       '"total_cents": total}\n        order = conn.execute(\n',
       "validates the cart, then returns without placing the order"),
    _m("checkout-orphan-order", "orphan", "checkout", "checkout", "F-02", ["broken_atomicity"],
       '            conn.execute(\n                """INSERT INTO order_items',
       '            _skip(\n                """INSERT INTO order_items',
       "creates the order but not its lines"),
    _m("checkout-drop-stock", "drop", "checkout", "checkout", "F-02", ["broken_atomicity"],
       '            conn.execute(\n                "UPDATE inventory SET on_hand = on_hand - %s',
       '            _skip(\n                "UPDATE inventory SET on_hand = on_hand - %s',
       "places the order but never takes the goods out of stock"),
    _m("checkout-stale-subtotal", "stale", "checkout", "checkout", "M-01", ["wrong_final_state"],
       'subtotal = sum(l["qty"] * l["price_cents"] for l in lines)',
       'subtotal = sum(l["price_cents"] for l in lines)',
       "prices every line as if the quantity were 1"),
    _m("checkout-drop-redemption", "drop", "checkout", "checkout", "M-02", ["broken_atomicity"],
       '            conn.execute(\n                "INSERT INTO coupon_redemptions',
       '            _skip(\n                "INSERT INTO coupon_redemptions',
       "applies the discount but never records the coupon as used"),
    _m("checkout-stale-discount", "stale", "checkout", "checkout", "M-02", ["wrong_final_state"],
       "discount = subtotal * pct // 100",
       "discount = 0",
       "records the coupon as used but gives no discount"),

    # ----- cancel_order (judged by M-03) -----
    _m("cancel-drop-restock", "drop", "orders", "cancel_order", "M-03", ["broken_atomicity"],
       '        conn.execute(\n            """UPDATE inventory i SET',
       '        _skip(\n            """UPDATE inventory i SET',
       "cancels the order but never puts the goods back"),
    _m("cancel-stale-restock", "stale", "orders", "cancel_order", "M-03", ["wrong_final_state"],
       "SET on_hand = i.on_hand + oi.qty",
       "SET on_hand = i.on_hand + 1",
       "puts back one item per line whatever the quantity"),
    _m("cancel-orphan-event", "orphan", "orders", "cancel_order", "M-03", ["missing_event"],
       '        emit(conn, "order.cancelled"',
       '        _skip(conn, "order.cancelled"',
       "cancels the order without announcing it"),

    # ----- share_cart (judged by M-05) -----
    _m("share-orphan-event", "orphan", "share", "share_cart", "M-05", ["missing_event"],
       '        emit(conn, "cart.shared"',
       '        _skip(conn, "cart.shared"',
       "creates the link without announcing it"),
    _m("share-divert-cart", "divert", "share", "share_cart", "M-05", ["undeclared_write"],
       '        return {"token": token, "cart_id": cart["id"]}',
       '        conn.execute("UPDATE carts SET status = \'checked_out\' WHERE id = %s", (cart["id"],))\n'
       '        return {"token": token, "cart_id": cart["id"]}',
       "sharing a cart also closes it"),
)


def source(m: Mutant) -> str:
    """The mutant's handler source: the reference source with the patch applied exactly once."""
    module = importlib.import_module(f"reference.handlers.{m.module}")
    src = inspect.getsource(getattr(module, m.handler))
    n = src.count(m.old)
    if n != 1:
        raise MutantError(f"{m.id}: patch text found {n} times in {m.module}.{m.handler}, expected 1")
    return src.replace(m.old, m.new, 1)


def build(m: Mutant) -> Callable:
    """Compile the mutant into a callable handler."""
    module = importlib.import_module(f"reference.handlers.{m.module}")
    namespace = dict(vars(module))
    namespace["_skip"] = lambda *args, **kwargs: None
    exec(compile(source(m), f"<mutant {m.id}>", "exec"), namespace)  # noqa: S102
    return namespace[m.handler]


def as_handlers(m: Mutant) -> types.SimpleNamespace:
    """A stand-in for a handlers module exposing only the mutated handler, for replay()."""
    return types.SimpleNamespace(**{m.handler: build(m)})
