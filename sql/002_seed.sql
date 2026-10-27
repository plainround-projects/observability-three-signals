-- 002_seed.sql: DML. 1,000 orders with 1 to 5 items each (200 orders of each size); no randomness, so every
-- run produces the same rows. Replaces any earlier seed.

TRUNCATE order_items, orders RESTART IDENTITY CASCADE;

INSERT INTO orders (id, customer_name, placed_at)
SELECT n,
       'customer-' || lpad(n::text, 4, '0'),
       timestamptz '2026-10-01 08:00:00+00' + (n * interval '7 minutes')
FROM generate_series(1, 1000) AS n;

-- Order n gets 1 + (n * 7 mod 5) items: sizes 1 to 5, spread evenly.
INSERT INTO order_items (order_id, sku, quantity, unit_price_cents)
SELECT o.id,
       'SKU-' || lpad((((o.id * 13 + line * 7) % 50) + 1)::text, 3, '0'),
       1 + ((o.id + line) % 4),
       500 + ((o.id * 31 + line * 17) % 90) * 100
FROM orders AS o
CROSS JOIN LATERAL generate_series(1, 1 + (o.id * 7) % 5) AS line
ORDER BY o.id, line;

ANALYZE orders;
ANALYZE order_items;
