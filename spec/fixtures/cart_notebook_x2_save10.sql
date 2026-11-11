-- same cart, with coupon SAVE10 attached (not yet redeemed)
INSERT INTO carts (user_id, coupon_code) VALUES (1, 'SAVE10');
INSERT INTO cart_items (cart_id, product_id, qty) VALUES (1, 1, 2);
