from app.errors import ShopError

from ._util import open_cart


def apply_coupon(conn, user_id: int, code: str) -> dict:
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
        if used is not None:
            raise ShopError("coupon_already_used")
        conn.execute("UPDATE carts SET coupon_code = %s WHERE id = %s", (code, cart["id"]))
        return {"cart_id": cart["id"], "coupon_code": code}


def remove_coupon(conn, user_id: int) -> dict:
    with conn.transaction():
        cart = open_cart(conn, user_id)
        if cart is None or cart["coupon_code"] is None:
            return {"cart_id": cart["id"] if cart else None, "removed": False}
        conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = %s", (cart["id"],))
        return {"cart_id": cart["id"], "removed": True}
