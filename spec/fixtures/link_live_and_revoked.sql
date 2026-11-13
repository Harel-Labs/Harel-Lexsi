-- user 1 has an open cart (2 Notebooks, 1 Pen, coupon SAVE10) shared as token 'tok_live';
-- an older link 'tok_revoked' to the same cart is revoked. User 2 has no cart.
INSERT INTO carts (user_id, coupon_code) VALUES (1, 'SAVE10');
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 1, 2), (1, 2, 1);
INSERT INTO share_links (token, cart_id, created_by) VALUES ('tok_live', 1, 1);
INSERT INTO share_links (token, cart_id, created_by, revoked) VALUES ('tok_revoked', 1, 1, true);
