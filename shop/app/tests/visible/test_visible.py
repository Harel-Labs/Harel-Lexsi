"""Visible tests: a quick check of what each handler returns and which error it raises.

They do not inspect the database, so passing them is necessary but not sufficient.
Seed data: product 1 Notebook 500c (50 in stock), 2 Pen 150c, 5 Desk Lamp 2500c (5 in stock),
6 is inactive. Coupons: SAVE10 (10%), HALF (50%), OLD20 expired, OFF15 inactive. Users 1 and 2.
"""
import pytest

from app.errors import ShopError

U, V = 1, 2


def code(fn, *args):
    with pytest.raises(ShopError) as e:
        fn(*args)
    return e.value.code


# ---------- cart ----------

def test_add_to_cart_returns_quantity(conn, h):
    assert h.add_to_cart(conn, U, 1, 2)["qty"] == 2
    assert h.add_to_cart(conn, U, 1, 3)["qty"] == 5


def test_add_to_cart_out_of_stock(conn, h):
    assert code(h.add_to_cart, conn, U, 5, 6) == "out_of_stock"


def test_add_to_cart_invalid_qty(conn, h):
    assert code(h.add_to_cart, conn, U, 1, 0) == "invalid_qty"


def test_add_to_cart_unknown_or_inactive_product(conn, h):
    assert code(h.add_to_cart, conn, U, 6, 1) == "not_found"
    assert code(h.add_to_cart, conn, U, 99, 1) == "not_found"


def test_update_cart_item(conn, h):
    h.add_to_cart(conn, U, 1, 2)
    assert h.update_cart_item(conn, U, 1, 7)["qty"] == 7
    assert h.update_cart_item(conn, U, 1, 0)["qty"] == 0


def test_update_cart_item_not_in_cart(conn, h):
    assert code(h.update_cart_item, conn, U, 1, 2) == "not_in_cart"


def test_remove_from_cart(conn, h):
    h.add_to_cart(conn, U, 1, 2)
    assert h.remove_from_cart(conn, U, 1)["removed_qty"] == 2
    assert code(h.remove_from_cart, conn, U, 1) == "not_in_cart"


def test_clear_cart(conn, h):
    h.add_to_cart(conn, U, 1, 2)
    h.add_to_cart(conn, U, 2, 1)
    assert h.clear_cart(conn, U)["cleared"] == 2


# ---------- coupons ----------

def test_apply_coupon(conn, h):
    h.add_to_cart(conn, U, 1, 1)
    assert h.apply_coupon(conn, U, "SAVE10")["coupon_code"] == "SAVE10"


@pytest.mark.parametrize("bad", ["NOPE", "OLD20", "OFF15"])
def test_apply_coupon_invalid(conn, h, bad):
    h.add_to_cart(conn, U, 1, 1)
    assert code(h.apply_coupon, conn, U, bad) == "invalid_coupon"


def test_apply_coupon_without_cart(conn, h):
    assert code(h.apply_coupon, conn, U, "SAVE10") == "empty_cart"


def test_remove_coupon(conn, h):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    assert h.remove_coupon(conn, U)["removed"] is True


# ---------- checkout ----------

def test_checkout_totals(conn, h):
    h.add_to_cart(conn, U, 1, 2)  # 1000
    h.add_to_cart(conn, U, 2, 2)  # 300
    h.apply_coupon(conn, U, "SAVE10")
    r = h.checkout(conn, U)
    assert (r["subtotal_cents"], r["discount_cents"], r["total_cents"]) == (1300, 130, 1170)


def test_checkout_empty_cart(conn, h):
    assert code(h.checkout, conn, U) == "empty_cart"


def test_checkout_coupon_already_used(conn, h):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    h.checkout(conn, U)
    h.add_to_cart(conn, U, 1, 1)
    assert code(h.apply_coupon, conn, U, "SAVE10") == "coupon_already_used"


# ---------- orders ----------

def test_get_and_list_orders(conn, h):
    h.add_to_cart(conn, U, 1, 1)
    oid = h.checkout(conn, U)["order_id"]
    assert h.get_order(conn, U, oid)["id"] == oid
    assert [o["id"] for o in h.list_orders(conn, U)] == [oid]
    assert code(h.get_order, conn, V, oid) == "not_found"


def test_cancel_order(conn, h):
    h.add_to_cart(conn, U, 1, 1)
    oid = h.checkout(conn, U)["order_id"]
    assert h.cancel_order(conn, U, oid)["status"] == "cancelled"
    assert code(h.cancel_order, conn, U, oid) == "not_cancellable"


# ---------- share ----------

def test_share_view_import(conn, h):
    h.add_to_cart(conn, U, 1, 2)
    token = h.share_cart(conn, U)["token"]
    assert h.view_shared_cart(conn, token)["lines"] == [{"product_id": 1, "qty": 2}]
    assert h.import_shared_cart(conn, V, token)["imported_lines"] == 1


def test_share_cart_without_cart(conn, h):
    assert code(h.share_cart, conn, U) == "empty_cart"


def test_shared_cart_unknown_token(conn, h):
    assert code(h.view_shared_cart, conn, "nope") == "not_found"
    assert code(h.import_shared_cart, conn, V, "nope") == "not_found"
