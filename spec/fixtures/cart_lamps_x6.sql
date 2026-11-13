-- user 1 has an open cart with 6 Desk Lamps (product 5), more than the 5 in stock
INSERT INTO carts (user_id) VALUES (1);
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 5, 6);
