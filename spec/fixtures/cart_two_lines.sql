-- user 1 has an open cart with 2 Notebooks (product 1) and 3 Pens (product 2)
INSERT INTO carts (user_id) VALUES (1);
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 1, 2), (1, 2, 3);
