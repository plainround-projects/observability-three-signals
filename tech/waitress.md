# waitress

## What it is

waitress is a production-quality **WSGI server** written in pure Python. It opens a TCP port, accepts connections, reads HTTP requests, calls your WSGI application (the Flask app) in one of a pool of worker threads, and writes the response back. It runs on Windows, macOS and Linux and needs no other software. This repository uses waitress 3.0.

## Why this repository uses it

Flask's own server is only for development. A real service needs a server that handles several requests at once, survives slow clients and can be told to stop cleanly. waitress does that with no extra software and works the same in the container and on a laptop. The lesson's load is about 10 requests per second with some taking 1.5 seconds, so a pool of eight threads is plenty.

## Where it appears

| Path | What it does with waitress |
| --- | --- |
| `src/three_signals/interfaces/server.py` | `run_server`: builds the server, installs the SIGTERM handler, runs it, and shuts down. |
| `src/three_signals/interfaces/back_main.py`, `front_main.py` | Call `run_server` as the last step of start-up. |
| `pyproject.toml` | Lists `waitress` as a dependency and tells mypy to ignore its missing type information. |

## How it works underneath

waitress has one main thread that watches all connections with the operating system's `select`-style facility (it can wait for many sockets at once) and a pool of worker **threads** (here 8). When a full request has arrived, the main thread hands it to a worker, which runs the app. This means up to eight requests can be in progress at the same time, and a ninth waits in a queue.

`create_server(app, host=..., port=..., threads=8, ident=...)` builds the server object without starting it. `server.run()` starts the loop and blocks until the program is interrupted. `server.close()` closes the listening socket. Because the code calls `run()` itself, it can handle shutdown signals and run clean-up callbacks, which the one-line `serve()` helper would hide.

`ident` is the text sent in the `Server:` response header.

## Walkthrough of this repository's use

```python
"""Run a WSGI app on waitress and shut down cleanly on SIGTERM or Ctrl-C."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from flask import Flask
from waitress.server import create_server

from three_signals.infrastructure.shutdown import install_termination_handler

_log = logging.getLogger(__name__)
```

`create_server` is imported from `waitress.server`; it builds a server object but does not start it. `Sequence` is a type for "a list or tuple". `install_termination_handler` is the SIGTERM handler from `python-threads-and-signals.md`.

```python
def run_server(app: Flask, host: str, port: int, on_close: Sequence[Callable[[], None]] = ()) -> None:
    """Serve until SIGTERM or Ctrl-C, let in-flight requests finish, then run the ``on_close`` callbacks."""
    install_termination_handler()
    server = create_server(app, host=host, port=port, threads=8, ident="three-signals")
```

`run_server(app, host, port, on_close=())` takes the Flask app, where to listen, and a sequence of functions to call when the server stops (closing the database pool, closing the HTTP session). The first line installs the handler so a SIGTERM is turned into `KeyboardInterrupt`. `create_server(..., threads=8, ident="three-signals")` binds the socket and prepares eight worker threads. Because the socket is bound here, a port already in use fails now, at start-up, with a clear error.

```python
    _log.info("listening", extra={"host": host, "port": port})
    try:
        server.run()
```

A log line says where the service listens (the `extra` fields become JSON keys). `server.run()` is the blocking loop: it accepts connections until the process is interrupted.

```python
    except KeyboardInterrupt:
        _log.info("shutdown requested; finishing in-flight requests")
    finally:
        server.close()
        for close in on_close:
            close()
        _log.info("stopped")
```

If the loop is interrupted by Ctrl-C or by SIGTERM (now a `KeyboardInterrupt`), the `except` branch logs that shutdown was requested. The `finally` block runs in every case: `server.close()` stops listening and waits for the workers to finish the requests they are handling; then each function in `on_close` runs (`pool.close` for the database, `session.close` for HTTP); the last log line says "stopped". Compose gives the container 15 seconds (`stop_grace_period`) for all this before it sends SIGKILL.

## Try it yourself

```bash
docker compose logs back | grep -E '"msg": "(listening|stopped)"'
```

Expected after `make up`: a `listening` line with `"host": "0.0.0.0", "port": 8081`.

```bash
docker compose stop back && docker compose logs back | tail -4
```

Expected: lines `shutdown requested; finishing in-flight requests` and `stopped` just before the container exits. Start it again with `docker compose start back`.

```bash
docker compose exec back python -c "import waitress; print(waitress.__name__)"
```

Expected: `waitress` (the library is installed inside the image).

## Mistakes people make

- **Binding to `0.0.0.0` on a laptop** and exposing the service to the network. The default here is `127.0.0.1`; only the container sets `0.0.0.0`, where Docker controls the exposure.
- **Too few threads** for a service that waits on a slow dependency. Eight workers fill up if every request takes seconds.
- **Not closing** the server on exit.

## Check yourself

1. What does a WSGI server do? *Listens on a port and calls the application for each request.*
2. How many requests can this server run at once? *Eight (one per worker thread).*
3. Why call `create_server` and `run()` instead of `serve()`? *To control shutdown and clean-up.*
4. What happens to a ninth simultaneous request? *It waits in the queue until a worker is free.*
5. Why is the bind host `0.0.0.0` in the container? *So Docker can forward connections to it; the host port is bound to 127.0.0.1.*

## Further reading

- waitress 3.0 documentation: https://docs.pylonsproject.org/projects/waitress/en/stable/
- Arguments: https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html
