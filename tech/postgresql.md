# PostgreSQL (16.4)

## What it is

PostgreSQL ("Postgres") is a relational database server. It stores data in **tables** (rows and columns), enforces rules on the data (types, `NOT NULL`, `CHECK`, `PRIMARY KEY`, `REFERENCES`), and answers questions written in **SQL**. It runs as a server process that listens on TCP port 5432; programs (and the `psql` command-line client) connect to it, log in as a **role** (a user), pick a **database**, and send SQL. This repository pins PostgreSQL 16.4 (the Alpine Linux variant of the official image).

## Why this repository uses it

The back service needs a real database so that the lesson's slow span is a genuine database span: a `SELECT` that the instrumented driver records, with the SQL text as an attribute. The database also teaches least privilege: the service connects as a role that can only read two tables.

## Where it appears

| Path | What it does |
| --- | --- |
| `docker-compose.yml` (service `postgres`) | Runs `postgres:16.4-alpine` with a named volume and a TCP health check. |
| `sql/001_schema.sql` | Creates the tables, the index and the read-only role. |
| `sql/002_seed.sql` | Loads 1,000 orders and 3,000 items. |
| `sql/003_queries.sql` | The queries the lesson runs by hand. |
| `Makefile` (`db`, `queries`) | Pipe the SQL files into `psql` inside the container. |
| `src/three_signals/infrastructure/postgres_orders.py`, `interfaces/back_main.py` | The service's queries and pool (see `psycopg.md`). |
| `tests/integration/test_stack.py` | Checks the seed counts and that the role cannot write. |

## How it works underneath

The server process forks a worker process for each connection. The data lives in files under the **data directory** (`/var/lib/postgresql/data`), which Compose keeps in a named volume. Every change is first written to the **write-ahead log** and then to the table files, so a crash can be recovered.

**Roles and privileges.** A role may have `LOGIN`. A `GRANT` gives it rights on objects: `CONNECT` on a database, `USAGE` on a schema, `SELECT` on tables. Without a grant a role gets "permission denied". The official image creates the superuser from `POSTGRES_USER` (default `postgres`) with the password from `POSTGRES_PASSWORD`, and creates the database named by `POSTGRES_DB`; it runs scripts found in `/docker-entrypoint-initdb.d/` only at the very first start, using a temporary server that listens on the Unix socket only, which is why a health check must connect over TCP.

**Indexes.** An index is a sorted structure on one or more columns so the database can find rows without reading the whole table. The planner decides between an index scan and a sequential scan.

**Connections are expensive**, so applications keep a **pool**: a set of already-open connections that requests borrow and return.

`pg_sleep(n)` is a built-in function that makes the server wait `n` seconds. This repository uses it to plant a slow query inside the database itself.

## Walkthrough of this repository's use

### The Compose service

```yaml
  postgres:
    image: postgres:16.4-alpine
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?run make up, which creates .env from .env.example}
      POSTGRES_DB: orders
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      # TCP, not the socket: the temporary server that runs the init scripts listens on the socket only.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d orders"]
      interval: 3s
      timeout: 3s
      retries: 20
```

`image: postgres:16.4-alpine` is the official image on the small Alpine Linux base. `POSTGRES_PASSWORD` sets the superuser password from `.env` (`${POSTGRES_PASSWORD:?...}` makes Compose stop with a message if it is empty). `POSTGRES_DB: orders` creates the database `orders` on the first start. The volume `postgres-data` is mounted on `/var/lib/postgresql/data`, the data directory, so the data survives container restarts and is deleted by `make down` (`-v`).

The health check is `pg_isready -h 127.0.0.1 -U postgres -d orders`. `pg_isready` asks a server whether it accepts connections (exit 0 means yes). The comment in the file explains `-h 127.0.0.1`: while the image runs its first-start steps it starts a temporary server that listens on a Unix socket only, and a socket check would report "healthy" too early. Forcing TCP waits for the real server. It checks every 3 seconds, up to 20 times.

### How the SQL gets in: the `Makefile` targets

```makefile
db: .env ## run sql/001_schema.sql and sql/002_seed.sql against the running database
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 \
	  -v reader_password="$(ORDERS_READER_PASSWORD)" -q -f - < sql/001_schema.sql
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -q -f - < sql/002_seed.sql
```

`make db` runs `docker compose exec -T postgres psql ...` twice. `exec` runs a command inside the running container; `-T` turns off the pseudo-terminal so input can be piped. `psql` is the command-line client; `-U postgres -d orders` is the user and database; `-v ON_ERROR_STOP=1` makes `psql` stop at the first error and exit non-zero (without it a script keeps going after errors); `-v reader_password="..."` defines a `psql` variable used inside the schema file; `-q` is quiet; `-f -` reads the script from standard input; and `< sql/001_schema.sql` feeds the file to that input. The shell redirection happens on your machine, so the SQL files do not need to be in the container.

```makefile
queries: ## run sql/003_queries.sql and print the results
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -f - < sql/003_queries.sql
```

`make queries` runs the lesson's queries and prints their results (no `-q`).

### The least-privilege role

`orders_reader` is created in `sql/001_schema.sql` (see `sql.md`). Its connection string is built in Compose:

```yaml
      DATABASE_URL: postgresql://orders_reader:${ORDERS_READER_PASSWORD:?run make up}@postgres:5432/orders
```

`postgresql://orders_reader:<password>@postgres:5432/orders` is a **connection URL**: scheme, user, password, host (the Compose service name `postgres`), port, database. The service never uses the superuser. The integration test proves that `orders_reader` can `SELECT` but `DELETE` fails with "permission denied".

### The integration test

```python
def _psql(sql: str, user: str = "postgres") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", user, "-d", "orders", "-At", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )
```

`_psql` runs the same `docker compose exec` as `make db`, with `-At` (unaligned, tuples only, so the output is just the value) and `-c` (run one statement). `subprocess.run(..., capture_output=True, text=True, check=False)` runs the command, captures its output as text and does not raise on a non-zero exit, so the test can inspect `returncode` and `stderr`.

## Try it yourself

```bash
docker compose exec postgres psql -U postgres -d orders -c "\dt"
```

Expected (needs `make up`): a table list with `orders` and `order_items`.

```bash
docker compose exec postgres psql -U postgres -d orders -c "SELECT count(*) FROM orders"
```

Expected: `1000`.

```bash
docker compose exec -e PGPASSWORD="$(grep ^ORDERS_READER_PASSWORD .env | cut -d= -f2)" postgres psql -h 127.0.0.1 -U orders_reader -d orders -c "DELETE FROM orders WHERE id = 1"
```

Expected: `ERROR:  permission denied for table orders`.

```bash
docker compose exec postgres pg_isready -h 127.0.0.1 -U postgres -d orders; echo "exit $?"
```

Expected: `127.0.0.1:5432 - accepting connections` and `exit 0`.

## Mistakes people make

- **Connecting as the superuser from the application.** This repository creates `orders_reader`, which can only `SELECT` from two tables.
- **A missing index** on the column used to find rows.
- **Opening a connection per request.** Use a pool.
- **Putting passwords in code.** They come from the environment.
- **Assuming init scripts run on every start.** They run only when the data directory is empty. This repository loads the SQL with `make db` instead, which is repeatable.

## Check yourself

1. What is a role? *A database user or group that can own objects and receive privileges.*
2. What does `GRANT SELECT` do? *Lets that role read the table.*
3. Why use a connection pool? *Opening connections is slow; a pool reuses open ones.*
4. What does `pg_sleep(1.5)` do? *Makes the statement wait 1.5 seconds on the server.*
5. Why does the health check use `-h 127.0.0.1`? *During init, the temporary server listens only on the socket; TCP succeeds only for the real server.*

## Further reading

- PostgreSQL 16 documentation: https://www.postgresql.org/docs/16/
- `psql`: https://www.postgresql.org/docs/16/app-psql.html
- Privileges: https://www.postgresql.org/docs/16/ddl-priv.html
- Official image notes: https://hub.docker.com/_/postgres
