-- Seeded catalog. Reset reloads exactly this. Carts, orders and share links start empty.

INSERT INTO products (id, name, price_cents, active) VALUES
    (1, 'Notebook',         500, true),
    (2, 'Pen',              150, true),
    (3, 'Backpack',        4500, true),
    (4, 'Water Bottle',    1200, true),
    (5, 'Desk Lamp',       2500, true),
    (6, 'Discontinued Mug', 800, false);

INSERT INTO inventory (product_id, on_hand) VALUES
    (1, 50),
    (2, 200),
    (3, 10),
    (4, 30),
    (5, 5),
    (6, 20);

INSERT INTO coupons (code, percent_off, active, expires_at) VALUES
    ('SAVE10', 10, true,  NULL),
    ('HALF',   50, true,  NULL),
    ('OLD20',  20, true,  '2020-01-01T00:00:00Z'),
    ('OFF15',  15, false, NULL);
