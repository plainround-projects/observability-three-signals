# 3. Plant the slow query inside the SQL statement, not before it

Status: accepted, 2026-10-27

## Context

The lesson plants the fault with a `time.sleep` right before the `order_items` query, yet its expected trace shows the
`SELECT order_items` span 1.5 seconds wide. A sleep before the statement happens outside the database span, so the
trace would show a gap with nothing explaining it, and the "slow span" the exercise must find would not be slow.

## Decision

The items query is `SELECT ... FROM order_items AS oi CROSS JOIN pg_sleep(%(delay_seconds)s) WHERE oi.order_id = ...`.
`delay_seconds` is `0.0` for most requests and `SLOW_QUERY_SECONDS` for the planned one in twenty (a `SlowQueryPlanner`
port, implemented with an injected seeded `random.Random`). The statement text is the same for fast and slow requests;
`pg_sleep(0)` costs nothing.

## Alternatives considered

- **`time.sleep` before the query** (the lesson's hint). Leaves the slow part outside any database span.
- **A manual span around sleep and query.** Gives the right span width, but then the trace has a manual `SELECT` span
  and an automatic one inside it, and the sleep is not in the database at all.
- **A separate `SELECT pg_sleep(1.5)` statement.** Makes a second, unrelated slow span, so the `order_items` span stays fast.

## Consequences

- The slow request has exactly one wide span, the `order_items` `SELECT`, and `db.statement` shows `pg_sleep` in it (which
  reveals the cause to a reader of the trace; a real incident would not be this tidy).
- The fault is production code behind configuration (`SLOW_QUERY_PROBABILITY=0` switches it off). A real system would
  inject faults from outside.
- A seed (`SLOW_QUERY_SEED`) makes the same requests slow on every run, which the end-to-end test relies on.
