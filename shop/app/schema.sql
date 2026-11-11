-- Shop schema. Lives in app/ because the builder agent needs to read it.
-- Money is integer cents. user_id is a plain integer (there is no users table).

CREATE TABLE products (
    id           integer PRIMARY KEY,
    name         text    NOT NULL,
    price_cents  integer NOT NULL CHECK (price_cents >= 0),
    active       boolean NOT NULL DEFAULT true
);

CREATE TABLE inventory (
    product_id integer PRIMARY KEY REFERENCES products (id),
    on_hand    integer NOT NULL CHECK (on_hand >= 0)
);

CREATE TABLE coupons (
    code        text PRIMARY KEY,
    percent_off integer NOT NULL CHECK (percent_off BETWEEN 1 AND 100),
    active      boolean NOT NULL DEFAULT true,
    expires_at  timestamptz
);

CREATE TABLE carts (
    id          serial  PRIMARY KEY,
    user_id     integer NOT NULL,
    status      text    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'checked_out')),
    coupon_code text    REFERENCES coupons (code)
);
-- At most one open cart per user.
CREATE UNIQUE INDEX one_open_cart_per_user ON carts (user_id) WHERE status = 'open';

CREATE TABLE cart_items (
    cart_id    integer NOT NULL REFERENCES carts (id) ON DELETE CASCADE,
    product_id integer NOT NULL REFERENCES products (id),
    qty        integer NOT NULL CHECK (qty > 0),
    PRIMARY KEY (cart_id, product_id)
);

CREATE TABLE orders (
    id             serial  PRIMARY KEY,
    user_id        integer NOT NULL,
    status         text    NOT NULL DEFAULT 'placed' CHECK (status IN ('placed', 'cancelled')),
    subtotal_cents integer NOT NULL,
    discount_cents integer NOT NULL,
    total_cents    integer NOT NULL
);

CREATE TABLE order_items (
    order_id         integer NOT NULL REFERENCES orders (id),
    product_id       integer NOT NULL REFERENCES products (id),
    qty              integer NOT NULL CHECK (qty > 0),
    unit_price_cents integer NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE coupon_redemptions (
    id          serial  PRIMARY KEY,
    coupon_code text    NOT NULL REFERENCES coupons (code),
    user_id     integer NOT NULL,
    order_id    integer NOT NULL REFERENCES orders (id),
    released    boolean NOT NULL DEFAULT false
);
-- A user can hold only one live (unreleased) redemption of a coupon.
CREATE UNIQUE INDEX one_live_redemption ON coupon_redemptions (coupon_code, user_id) WHERE NOT released;

CREATE TABLE share_links (
    token      text    PRIMARY KEY,
    cart_id    integer NOT NULL REFERENCES carts (id),
    created_by integer NOT NULL,
    revoked    boolean NOT NULL DEFAULT false
);

-- Events are published by inserting here, in the same transaction as the change they describe.
CREATE TABLE outbox (
    id         bigserial   PRIMARY KEY,
    event      text        NOT NULL,
    payload    jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Row changes must carry before and after values for the whole row.
ALTER TABLE products           REPLICA IDENTITY FULL;
ALTER TABLE inventory          REPLICA IDENTITY FULL;
ALTER TABLE coupons            REPLICA IDENTITY FULL;
ALTER TABLE carts              REPLICA IDENTITY FULL;
ALTER TABLE cart_items         REPLICA IDENTITY FULL;
ALTER TABLE orders             REPLICA IDENTITY FULL;
ALTER TABLE order_items        REPLICA IDENTITY FULL;
ALTER TABLE coupon_redemptions REPLICA IDENTITY FULL;
ALTER TABLE share_links        REPLICA IDENTITY FULL;
ALTER TABLE outbox             REPLICA IDENTITY FULL;
