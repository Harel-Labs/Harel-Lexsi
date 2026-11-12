-- user 1 has placed order 1: 2 Notebooks (product 1) bought with coupon SAVE10, stock already taken
INSERT INTO orders (user_id, subtotal_cents, discount_cents, total_cents) VALUES (1, 1000, 100, 900);
INSERT INTO order_items (order_id, product_id, qty, unit_price_cents) VALUES (1, 1, 2, 500);
INSERT INTO coupon_redemptions (coupon_code, user_id, order_id) VALUES ('SAVE10', 1, 1);
UPDATE inventory SET on_hand = 48 WHERE product_id = 1;
