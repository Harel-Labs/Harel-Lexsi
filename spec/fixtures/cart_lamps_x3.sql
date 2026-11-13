-- user 1 has an open cart with 3 Desk Lamps (product 5, 5 in stock)
INSERT INTO carts (user_id) VALUES (1);
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 5, 3);
