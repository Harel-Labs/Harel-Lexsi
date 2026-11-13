-- user 1 has order 1 (2 Notebooks, no coupon), already cancelled and restocked
INSERT INTO orders (user_id, status, subtotal_cents, discount_cents, total_cents) VALUES (1, 'cancelled', 1000, 0, 1000);
INSERT INTO order_items (order_id, product_id, qty, unit_price_cents) VALUES (1, 1, 2, 500);
