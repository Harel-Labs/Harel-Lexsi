"""Every reference handler: a happy path and a failure path. A failing call changes nothing."""
import time

import pytest

from app.errors import ShopError
from reference import handlers as h
from tools.db import assert_wal_logical, reset, state_hash

U, V = 1, 2  # two users


def fails(conn, code, fn, *args):
    """Call fn, expect ShopError(code), and assert the database is unchanged."""
    before = state_hash(conn)
    with pytest.raises(ShopError) as e:
        fn(conn, *args)
    assert e.value.code == code
    assert state_hash(conn) == before


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def stock(conn, product_id):
    return one(conn, "SELECT on_hand FROM inventory WHERE product_id = %s", product_id)["on_hand"]


def events(conn):
    return [r["event"] for r in conn.execute("SELECT event FROM outbox ORDER BY id").fetchall()]


# ---------- environment ----------

def test_wal_level_is_logical(conn):
    assert_wal_logical(conn)


def test_reset_restores_seed_quickly(conn):
    seeded = state_hash(conn)
    h.add_to_cart(conn, U, 1, 2)
    h.apply_coupon(conn, U, "SAVE10")
    h.checkout(conn, U)
    assert state_hash(conn) != seeded
    t0 = time.perf_counter()
    reset(conn)
    assert time.perf_counter() - t0 < 3
    assert state_hash(conn) == seeded
    # identity restarts, so ids are reproducible
    h.add_to_cart(conn, U, 1, 1)
    assert one(conn, "SELECT id FROM carts")["id"] == 1


# ---------- cart ----------

def test_add_to_cart_creates_cart_and_accumulates(conn):
    r = h.add_to_cart(conn, U, 1, 2)
    assert r["qty"] == 2
    assert h.add_to_cart(conn, U, 1, 3)["qty"] == 5
    assert one(conn, "SELECT count(*) AS n FROM carts")["n"] == 1


def test_add_to_cart_does_not_reserve_stock(conn):
    h.add_to_cart(conn, U, 1, 5)
    assert stock(conn, 1) == 50


def test_add_to_cart_out_of_stock(conn):
    fails(conn, "out_of_stock", h.add_to_cart, U, 5, 6)  # 5 lamps on hand


def test_add_to_cart_total_counts_existing_line(conn):
    h.add_to_cart(conn, U, 5, 4)
    fails(conn, "out_of_stock", h.add_to_cart, U, 5, 2)


def test_add_to_cart_inactive_product(conn):
    fails(conn, "not_found", h.add_to_cart, U, 6, 1)


def test_update_cart_item(conn):
    h.add_to_cart(conn, U, 1, 2)
    assert h.update_cart_item(conn, U, 1, 7)["qty"] == 7
    assert h.update_cart_item(conn, U, 1, 0)["qty"] == 0
    assert one(conn, "SELECT count(*) AS n FROM cart_items")["n"] == 0


def test_update_cart_item_failures(conn):
    fails(conn, "not_in_cart", h.update_cart_item, U, 1, 2)
    h.add_to_cart(conn, U, 5, 1)
    fails(conn, "out_of_stock", h.update_cart_item, U, 5, 6)


def test_remove_from_cart(conn):
    h.add_to_cart(conn, U, 1, 2)
    h.add_to_cart(conn, U, 2, 1)
    assert h.remove_from_cart(conn, U, 1)["removed_qty"] == 2
    assert one(conn, "SELECT count(*) AS n FROM cart_items")["n"] == 1


def test_remove_from_cart_not_in_cart(conn):
    fails(conn, "not_in_cart", h.remove_from_cart, U, 1)
    h.add_to_cart(conn, U, 2, 1)
    fails(conn, "not_in_cart", h.remove_from_cart, U, 1)


def test_clear_cart_removes_lines_and_coupon_but_keeps_cart(conn):
    h.add_to_cart(conn, U, 1, 2)
    h.add_to_cart(conn, U, 2, 1)
    h.apply_coupon(conn, U, "SAVE10")
    assert h.clear_cart(conn, U)["cleared"] == 2
    cart = one(conn, "SELECT * FROM carts")
    assert cart["status"] == "open" and cart["coupon_code"] is None
    assert one(conn, "SELECT count(*) AS n FROM cart_items")["n"] == 0


def test_invalid_qty(conn):
    fails(conn, "invalid_qty", h.add_to_cart, U, 1, 0)


# ---------- coupons ----------

def test_apply_coupon_attaches_without_redeeming(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    assert one(conn, "SELECT coupon_code FROM carts")["coupon_code"] == "SAVE10"
    assert one(conn, "SELECT count(*) AS n FROM coupon_redemptions")["n"] == 0


def test_apply_coupon_replaces_previous(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    h.apply_coupon(conn, U, "HALF")
    assert one(conn, "SELECT coupon_code FROM carts")["coupon_code"] == "HALF"


@pytest.mark.parametrize("code", ["NOPE", "OLD20", "OFF15"])  # unknown, expired, inactive
def test_apply_coupon_invalid(conn, code):
    h.add_to_cart(conn, U, 1, 1)
    fails(conn, "invalid_coupon", h.apply_coupon, U, code)


def test_apply_coupon_already_used(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    h.checkout(conn, U)
    h.add_to_cart(conn, U, 1, 1)
    fails(conn, "coupon_already_used", h.apply_coupon, U, "SAVE10")


def test_apply_coupon_without_cart(conn):
    fails(conn, "empty_cart", h.apply_coupon, U, "SAVE10")


def test_remove_coupon(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    assert h.remove_coupon(conn, U)["removed"] is True
    assert one(conn, "SELECT coupon_code FROM carts")["coupon_code"] is None
    assert h.remove_coupon(conn, U)["removed"] is False


# ---------- checkout ----------

def test_checkout_does_everything(conn):
    h.add_to_cart(conn, U, 1, 2)   # 2 x 500
    h.add_to_cart(conn, U, 3, 1)   # 1 x 4500
    h.apply_coupon(conn, U, "SAVE10")
    r = h.checkout(conn, U)
    assert (r["subtotal_cents"], r["discount_cents"], r["total_cents"]) == (5500, 550, 4950)
    order = one(conn, "SELECT * FROM orders")
    assert order["status"] == "placed" and order["total_cents"] == 4950
    assert one(conn, "SELECT count(*) AS n FROM order_items")["n"] == 2
    assert stock(conn, 1) == 48 and stock(conn, 3) == 9
    red = one(conn, "SELECT * FROM coupon_redemptions")
    assert red["coupon_code"] == "SAVE10" and red["order_id"] == order["id"] and not red["released"]
    assert one(conn, "SELECT status FROM carts")["status"] == "checked_out"
    assert one(conn, "SELECT count(*) AS n FROM cart_items")["n"] == 0
    assert events(conn) == ["order.placed"]


def test_checkout_without_coupon_writes_no_redemption(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.checkout(conn, U)
    assert one(conn, "SELECT count(*) AS n FROM coupon_redemptions")["n"] == 0
    assert events(conn) == ["order.placed"]


def test_checkout_empty_cart(conn):
    fails(conn, "empty_cart", h.checkout, U)
    h.add_to_cart(conn, U, 1, 1)
    h.remove_from_cart(conn, U, 1)
    fails(conn, "empty_cart", h.checkout, U)


def test_checkout_out_of_stock_changes_nothing(conn):
    h.add_to_cart(conn, U, 5, 4)
    h.add_to_cart(conn, V, 5, 4)
    h.checkout(conn, V)            # only 1 lamp left, U wants 4
    fails(conn, "out_of_stock", h.checkout, U)


def test_checkout_coupon_already_used_changes_nothing(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    # the coupon gets redeemed by another route after it was applied
    conn.execute("INSERT INTO orders (user_id, subtotal_cents, discount_cents, total_cents) VALUES (%s,0,0,0)", (U,))
    conn.execute("INSERT INTO coupon_redemptions (coupon_code, user_id, order_id) VALUES ('SAVE10', %s, 1)", (U,))
    fails(conn, "coupon_already_used", h.checkout, U)


# ---------- orders ----------

def test_get_and_list_orders(conn):
    h.add_to_cart(conn, U, 1, 1)
    first = h.checkout(conn, U)["order_id"]
    h.add_to_cart(conn, U, 2, 3)
    second = h.checkout(conn, U)["order_id"]
    order = h.get_order(conn, U, second)
    assert order["items"] == [{"product_id": 2, "qty": 3, "unit_price_cents": 150}]
    assert [o["id"] for o in h.list_orders(conn, U)] == [second, first]
    assert h.list_orders(conn, V) == []


def test_get_order_not_found(conn):
    h.add_to_cart(conn, U, 1, 1)
    oid = h.checkout(conn, U)["order_id"]
    fails(conn, "not_found", h.get_order, V, oid)  # someone else's order
    fails(conn, "not_found", h.get_order, U, 999)


def test_cancel_order_undoes_checkout(conn):
    h.add_to_cart(conn, U, 1, 2)
    h.apply_coupon(conn, U, "SAVE10")
    oid = h.checkout(conn, U)["order_id"]
    h.cancel_order(conn, U, oid)
    assert one(conn, "SELECT status FROM orders")["status"] == "cancelled"
    assert stock(conn, 1) == 50
    assert one(conn, "SELECT released FROM coupon_redemptions")["released"] is True
    assert events(conn) == ["order.placed", "order.cancelled"]


def test_cancel_order_frees_the_coupon(conn):
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")
    h.cancel_order(conn, U, h.checkout(conn, U)["order_id"])
    h.add_to_cart(conn, U, 1, 1)
    h.apply_coupon(conn, U, "SAVE10")  # allowed again
    h.checkout(conn, U)                # and redeemable again


def test_cancel_order_failures(conn):
    h.add_to_cart(conn, U, 1, 1)
    oid = h.checkout(conn, U)["order_id"]
    fails(conn, "not_found", h.cancel_order, V, oid)
    h.cancel_order(conn, U, oid)
    fails(conn, "not_cancellable", h.cancel_order, U, oid)


# ---------- share ----------

def test_share_cart_leaves_cart_unchanged(conn):
    h.add_to_cart(conn, U, 1, 2)
    cart_before = state_hash(conn)["cart_items"], state_hash(conn)["carts"]
    token = h.share_cart(conn, U)["token"]
    assert (state_hash(conn)["cart_items"], state_hash(conn)["carts"]) == cart_before
    assert one(conn, "SELECT token FROM share_links")["token"] == token
    assert events(conn) == ["cart.shared"]


def test_share_cart_without_cart(conn):
    fails(conn, "empty_cart", h.share_cart, U)


def test_view_shared_cart(conn):
    h.add_to_cart(conn, U, 1, 2)
    token = h.share_cart(conn, U)["token"]
    before = state_hash(conn)
    assert h.view_shared_cart(conn, token)["lines"] == [{"product_id": 1, "qty": 2}]
    assert state_hash(conn) == before  # read-only


def test_view_shared_cart_unknown_or_revoked(conn):
    h.add_to_cart(conn, U, 1, 2)
    token = h.share_cart(conn, U)["token"]
    fails(conn, "not_found", h.view_shared_cart, "nope")
    conn.execute("UPDATE share_links SET revoked = true")
    fails(conn, "not_found", h.view_shared_cart, token)


def test_import_shared_cart_adds_quantities(conn):
    h.add_to_cart(conn, U, 1, 2)
    h.add_to_cart(conn, U, 2, 1)
    h.apply_coupon(conn, U, "SAVE10")
    token = h.share_cart(conn, U)["token"]
    h.add_to_cart(conn, V, 1, 3)
    owner_before = one(conn, "SELECT * FROM carts WHERE user_id = %s", U)
    h.import_shared_cart(conn, V, token)
    lines = conn.execute(
        "SELECT ci.product_id, ci.qty FROM cart_items ci JOIN carts c ON c.id = ci.cart_id "
        "WHERE c.user_id = %s ORDER BY 1", (V,)
    ).fetchall()
    assert lines == [{"product_id": 1, "qty": 5}, {"product_id": 2, "qty": 1}]
    assert one(conn, "SELECT coupon_code FROM carts WHERE user_id = %s", V)["coupon_code"] is None
    assert one(conn, "SELECT * FROM carts WHERE user_id = %s", U) == owner_before
    assert one(conn, "SELECT qty FROM cart_items WHERE cart_id = %s AND product_id = 1", owner_before["id"])["qty"] == 2
    assert stock(conn, 1) == 50


def test_import_shared_cart_creates_cart(conn):
    h.add_to_cart(conn, U, 1, 2)
    token = h.share_cart(conn, U)["token"]
    h.import_shared_cart(conn, V, token)
    assert one(conn, "SELECT count(*) AS n FROM carts WHERE user_id = %s", V)["n"] == 1


def test_import_shared_cart_bad_token(conn):
    fails(conn, "not_found", h.import_shared_cart, V, "nope")
