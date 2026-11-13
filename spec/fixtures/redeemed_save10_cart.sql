-- user 1 already redeemed SAVE10 on placed order 1, and has a new open cart with 2 Notebooks (no coupon)
INSERT INTO orders (user_id, subtotal_cents, discount_cents, total_cents) VALUES (1, 1000, 100, 900);
INSERT INTO order_items (order_id, product_id, qty, unit_price_cents) VALUES (1, 1, 2, 500);
INSERT INTO coupon_redemptions (coupon_code, user_id, order_id) VALUES ('SAVE10', 1, 1);
UPDATE inventory SET on_hand = 48 WHERE product_id = 1;
INSERT INTO carts (user_id) VALUES (1);
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 1, 2);
