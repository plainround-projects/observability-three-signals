-- 003_queries.sql: the queries the lesson runs. Run with: make queries

-- 1. The orders lookup the back service runs first: one row by primary key.
SELECT id, customer_name FROM orders WHERE id = 3;

-- 2. The order_items lookup it runs second (the planned fault delay is 0 here): the index on order_id answers it.
SELECT oi.id, oi.sku, oi.quantity, oi.unit_price_cents
FROM order_items AS oi CROSS JOIN pg_sleep(0) WHERE oi.order_id = 3 ORDER BY oi.id;

-- 3. The same query with the planted fault: the database itself spends 1.5 seconds inside this one statement,
--    which is why the trace shows one SELECT span far wider than its siblings.
\timing on
SELECT oi.id, oi.sku, oi.quantity, oi.unit_price_cents
FROM order_items AS oi CROSS JOIN pg_sleep(1.5) WHERE oi.order_id = 3 ORDER BY oi.id;
\timing off

-- 4. How many orders fall in each item-count bucket (the values of the order.item_count_bucket attribute).
SELECT CASE WHEN item_count <= 2 THEN '1-2' WHEN item_count <= 5 THEN '3-5' ELSE '6+' END AS bucket,
       count(*) AS orders
FROM (SELECT order_id, count(*) AS item_count FROM order_items GROUP BY order_id) AS sizes
GROUP BY 1 ORDER BY 1;

-- 5. Row counts after seeding: 1,000 orders and 3,000 items.
SELECT (SELECT count(*) FROM orders) AS orders, (SELECT count(*) FROM order_items) AS items;
