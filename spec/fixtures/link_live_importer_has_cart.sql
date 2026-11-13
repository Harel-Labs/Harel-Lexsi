-- as link_live_and_revoked, and user 2 also has an open cart (id 2) with 1 Notebook
INSERT INTO carts (user_id, coupon_code) VALUES (1, 'SAVE10');
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 1, 2), (1, 2, 1);
INSERT INTO share_links (token, cart_id, created_by) VALUES ('tok_live', 1, 1);
INSERT INTO share_links (token, cart_id, created_by, revoked) VALUES ('tok_revoked', 1, 1, true);
INSERT INTO carts (user_id) VALUES (2);
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (2, 1, 1);
