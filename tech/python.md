# Python

## What it is

Python is a programming language. A programming language is a way to write instructions that a computer follows. You write the instructions in plain text files that end in `.py`. A program called the **interpreter** reads those files and carries out the instructions one at a time. You do not have to translate them into machine code first.

Python is popular for services, scripts and tests because its code is short and readable. This repository uses Python 3.12 inside the container images and Python 3.12 or 3.13 on your own machine. The text `from __future__ import annotations` at the top of most files and the `str | None` style of writing types both need Python 3.10 or newer; `StrEnum` needs 3.11; `datetime.UTC` needs 3.11.

Some words you will meet again and again in these notes:

- A **module** is one `.py` file. A **package** is a folder of modules that has an `__init__.py` file in it. Here, `three_signals` is the package and `three_signals/domain/orders.py` is the module `three_signals.domain.orders`.
- A **function** is a named block of instructions. A **class** is a blueprint for objects that keep some data and have functions (called **methods**) that work on that data.
- A **type hint** (also called an annotation) is a note such as `order_id: int` that says what kind of value is expected. Python itself ignores type hints when the program runs. Tools such as `mypy` read them and warn you about mistakes before you run anything.
- A **standard library** module comes with Python itself (for example `json`, `logging`, `time`, `random`). A **third-party** package is installed from outside (for example Flask or requests).

## Why this repository uses it

Both services, the load scenario, the investigation tool and every test are Python. The lesson is about observability, not about Python, so the code is kept small and plain: a few classes, no clever tricks. The structure matters because it shows where each kind of telemetry belongs: the business rules (`domain`) and the use cases (`application`) know nothing about HTTP, databases or OpenTelemetry, and the outer layers (`infrastructure`, `interfaces`) plug those in.

## Where it appears

| Path | What it does with Python |
| --- | --- |
| `src/three_signals/domain/` | The order model and the named errors. No outside libraries. |
| `src/three_signals/application/` | The two use cases (`AssembleOrder`, `FetchOrder`) and the `Protocol`s they depend on. |
| `src/three_signals/infrastructure/` | Adapters: database, HTTP client, JSON shape, settings, fault planner, OpenTelemetry, logging, shutdown. |
| `src/three_signals/interfaces/` | Flask apps, process entry points and the health command. |
| `src/three_signals/lab/` | Tooling that runs from outside: store clients, the scenario, the click-path walk. |
| `tests/` | pytest tests (see `pytest.md`). |
| `pyproject.toml` | Declares the package, its dependencies and its commands (see `pyproject-toml.md`). |

## How it works underneath

When you run `python something.py`, the operating system starts one **process**. A process is one running program with its own memory. The interpreter inside that process reads your file, turns it into a simpler internal form called **bytecode**, and runs the bytecode.

When one module says `import json`, the interpreter looks for a module with that name. It looks in the folders listed in `sys.path`. The first time a module is imported, its top-level lines run once, and the result is remembered. Importing the same module again is cheap because the remembered copy is reused.

Python is **dynamically typed**. A variable has no fixed type; the value it points to has one. That is why type hints and `mypy` exist: they add the checking that the language itself does not do.

Python has a **global interpreter lock** (the GIL). In the standard build only one thread runs Python bytecode at a time. Threads still help when the program mostly waits for the network or a database, because a waiting thread lets another thread run. All the services in these repositories spend most of their time waiting, so threads are enough.

A **virtual environment** (a `.venv` folder) is a private copy of the interpreter's package folder. You install the project's dependencies into it, so they do not mix with other projects on the same machine.

## Python features you will meet in this repository

Read this list once. Each feature is explained again, with an example from the code, where it first appears.

- **f-strings.** `f"order {order_id} does not exist"` builds a string by putting the value of the names inside the braces into the text. The `f` before the opening quote turns it on.
- **Type hints.** `def next_delay_seconds(self) -> float:` says the function returns a float. `int | None` means "an int, or None". `list[str]` is a list of strings. `tuple[OrderItem, ...]` is a tuple of any length whose items are all `OrderItem`.
- **`from __future__ import annotations`.** It tells Python to treat all type hints as text and not evaluate them when the module loads. This lets a class name itself in a hint and keeps imports used only for hints cheap.
- **Dataclasses.** `@dataclass(frozen=True, slots=True)` turns a class that only lists fields into one that has a constructor, an equality test and a readable text form generated for it. `frozen=True` forbids changing a field after creation, so the object is a fixed value. `slots=True` stores fields in a compact fixed layout instead of a dictionary.
- **Enums.** `class ItemCountBucket(StrEnum)` defines a small closed set of named values. A `StrEnum` member is also a string, so `str(ItemCountBucket.ONE_TO_TWO)` is `"1-2"`.
- **Protocols.** `class OrderRepository(Protocol)` describes what methods an object must have, without forcing it to inherit from anything. Any class with those methods fits. This is called structural typing.
- **Context managers.** `with something as name:` runs setup code on entering and cleanup code on leaving, even when an error happens. A class can offer this with `__enter__` and `__exit__`; a function can offer it with the `@contextmanager` decorator and a `yield`.
- **Decorators.** A line such as `@app.get("/healthz")` above a function hands that function to another function, which registers it or wraps it.
- **Exceptions.** `raise SomeError("text")` stops the current function and travels up the call chain until an `except SomeError:` catches it. `raise NewError(...) from error` keeps the original error attached as the cause.
- **Comprehensions.** `[x * 2 for x in numbers if x > 0]` builds a list from another sequence in one line. `{...}` with `for` builds a set or a dictionary. `( ... for ...)` inside a function call builds values lazily one at a time.
- **Keyword arguments and `**`.** `f(a=1, b=2)` passes by name. `{**one, **two}` merges two dictionaries into a new one; later keys win.
- **Closures.** A function defined inside another function can use the outer function's variables. Flask route functions in this repository are defined that way inside `create_*_app`.
- **Properties.** `@property` lets a method be read like a field: `order.total_cents` with no parentheses.
- **Class methods.** `@classmethod` gives a method that receives the class (`cls`) instead of one object. It is used here as an alternative constructor: `BackSettings.from_env(os.environ)`.
- **The `if __name__ == "__main__":` guard.** The code under it runs only when the file is started directly, not when another module imports it.

## Walkthrough of this repository's use

The code is read here from the inside out: the model first, then the use cases, then the adapters.

### The package markers: the `__init__.py` files

Every folder has an `__init__.py`. A folder with that file is a Python package. The file may be empty, but here each one holds a **docstring**: a text in triple quotes at the top of a file, which Python stores as the module's documentation. The linter rule `D104` (see `ruff.md`) requires one in every package, and each says what the layer is for and what it may depend on.

`src/three_signals/__init__.py`:

```python
"""Two small services whose traces, metrics and logs can be followed from one signal to the next."""
```
`src/three_signals/domain/__init__.py`:

```python
"""Orders, their items and the rules about them; depends on nothing else in the project."""
```
`src/three_signals/application/__init__.py`:

```python
"""Use cases and the ports they need; depends on the domain only."""
```
`src/three_signals/infrastructure/__init__.py`:

```python
"""Adapters for the database, HTTP, OpenTelemetry and the process environment."""
```
`src/three_signals/interfaces/__init__.py`:

```python
"""HTTP entry points and process wiring; may depend on every other layer, nothing depends on it."""
```
```python
"""Verification tooling: clients for the three stores, the load scenario and the investigation walk-through.

The services never import this package; it only talks to the running stack from outside.
```

The sentences are the layering rule in plain words: `domain` depends on nothing, `application` depends on `domain`, `infrastructure` and `interfaces` come after, and `lab` never gets imported by the services. `import-linter` (see `import-linter.md`) enforces it.

### `domain/errors.py`: named errors

```python
"""Named errors that cross layer boundaries."""


class DomainError(Exception):
    """Base class for every error this project raises on purpose."""

```

An **exception** is an object that signals a problem. `class DomainError(Exception):` defines a new class that **inherits** from the built-in `Exception`; "inherits" means it gets everything `Exception` has. The text in triple quotes is its docstring. A class with only a docstring needs no other body.

`DomainError` is a base class for every error this project raises *on purpose*. Code that wants to catch all of them writes `except DomainError`.

```python
class InvalidOrderIdError(DomainError):
    """The order id is not a positive integer."""
```

`InvalidOrderIdError` is raised when an order id is not a positive integer. The `Error` suffix is the naming rule `N818` of the linter.

```python
class OrderNotFoundError(DomainError):
    """No order with this id exists."""

    def __init__(self, order_id: int) -> None:
        super().__init__(f"order {order_id} does not exist")
        self.order_id = order_id
```

`OrderNotFoundError` has its own `__init__`, the **constructor**: the method Python calls when you write `OrderNotFoundError(9)`. `self` is the object being built. It receives `order_id: int` and returns `None`. `super().__init__(...)` calls the parent class's constructor with the message text, built with an f-string. Then `self.order_id = order_id` keeps the id on the object so the code that catches it can read it back (a test does: `raised.value.order_id == 9`).

```python
class BackendUnavailableError(DomainError):
    """The back service answered with a server error or could not be reached."""


class BackendTimeoutError(BackendUnavailableError):
    """The back service did not answer within the configured time."""
```

`BackendUnavailableError` means the back service answered with a server error or could not be reached. `BackendTimeoutError` inherits from it, so code that catches the general one also catches the timeout, while code that wants to treat the timeout differently (the front service maps it to HTTP 504 instead of 502) can catch the specific class first.

### `domain/orders.py`: the order model

```python
"""The order model: immutable values with the few rules the services rely on."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from three_signals.domain.errors import InvalidOrderIdError

```

Line 1 is the docstring. `from __future__ import annotations` turns type hints into text so a class can mention itself (see the feature list above). The imports bring in `dataclass` (to generate constructors), `StrEnum` (a string-valued enumeration) and the one error this file raises.

```python
@dataclass(frozen=True, slots=True)
class OrderId:
    """A positive integer identifying one order."""

    value: int

    def __post_init__(self) -> None:
        """Reject zero, negative and non-integer ids at construction."""
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidOrderIdError(f"order id must be a positive integer, got {self.value!r}")
```

`@dataclass(frozen=True, slots=True)` is a **decorator**: a line above a class that changes it. It generates the constructor `OrderId(value)`, equality, a readable text form and, because `frozen=True`, makes every instance unchangeable. `value: int` is the single field.

`__post_init__` is a method that a dataclass calls right after the generated constructor, so it is the place to check the data. The condition has three parts joined by `or`: `isinstance(self.value, bool)` rejects `True`/`False` (a bool is an int in Python, which would otherwise pass), `not isinstance(self.value, int)` rejects strings and floats, `self.value < 1` rejects zero and negatives. `{self.value!r}` in the message uses `repr`, which shows quotes around strings, so `"7"` and `7` look different in the error text.

Making an invalid order id impossible to construct means every function that receives an `OrderId` can trust it.

```python
@dataclass(frozen=True, slots=True)
class OrderItem:
    """One line of an order."""

    item_id: int
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def line_total_cents(self) -> int:
        """Price of the whole line in cents."""
        return self.quantity * self.unit_price_cents
```

`OrderItem` is one line of an order: id, product code (`sku`), quantity and unit price in **cents**. Money is stored in whole cents, an `int`, because floating-point numbers cannot represent most decimal amounts exactly. `@property` makes `line_total_cents` readable like a field, computed on demand as quantity times price.

```python
class ItemCountBucket(StrEnum):
    """A bounded category for the number of items in an order.

    The raw count (or the order id) is unbounded, so it never labels a metric; this handful of
    values can safely group spans and series.
    """

    NONE = "0"
    ONE_TO_TWO = "1-2"
    THREE_TO_FIVE = "3-5"
    SIX_OR_MORE = "6+"
```

`ItemCountBucket` is a `StrEnum`: a closed set of four named values, each also a string. The docstring explains the reason it exists: the raw item count and the order id are *unbounded*, so using them as a metric label would create a new time series for every value (see `prometheus.md`). Four buckets give a bounded label that still tells small orders from big ones.

```python
    @classmethod
    def for_count(cls, item_count: int) -> ItemCountBucket:
        """Return the bucket that holds ``item_count`` items."""
        if item_count <= 0:
            return cls.NONE
        if item_count <= 2:
            return cls.ONE_TO_TWO
        if item_count <= 5:
            return cls.THREE_TO_FIVE
        return cls.SIX_OR_MORE
```

`@classmethod` means the method belongs to the class, and `cls` is the class itself. `for_count(3)` returns `ItemCountBucket.THREE_TO_FIVE`. The method uses early `return`s: the first condition that is true decides. The return type `ItemCountBucket` is written without quotes thanks to `from __future__ import annotations`.

```python
@dataclass(frozen=True, slots=True)
class OrderHeader:
    """The row from the orders table, without its items."""

    order_id: OrderId
    customer_name: str


@dataclass(frozen=True, slots=True)
class Order:
    """An order with all of its items."""

    header: OrderHeader
    items: tuple[OrderItem, ...]

    @property
    def total_cents(self) -> int:
        """Sum of every line."""
        return sum(item.line_total_cents for item in self.items)

    @property
    def item_count_bucket(self) -> ItemCountBucket:
        """The bounded category of this order's size."""
        return ItemCountBucket.for_count(len(self.items))
```

`OrderHeader` is the row of the `orders` table (id and customer name). `Order` combines a header with `items: tuple[OrderItem, ...]`. A **tuple** is an immutable sequence, a better fit than a list for a frozen object. `total_cents` uses a **generator expression**, `sum(item.line_total_cents for item in self.items)`, which feeds the values to `sum` one at a time. `item_count_bucket` asks the enum which bucket the length belongs to.

### `application/ports.py`: what the use cases need

A **port** is a description of something the use case needs from the outside world. It says *what*, not *how*. The adapters in `infrastructure` provide the *how*.

```python
"""The boundaries the use cases talk through; infrastructure supplies the implementations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem
```

`AbstractContextManager` is the type of an object usable in a `with` statement. `Protocol` is the base for structural interfaces.

```python
class OrderRepository(Protocol):
    """Reads orders from the database."""

    def find_header(self, order_id: OrderId) -> OrderHeader | None:
        """Return the order row, or ``None`` when no such order exists."""
        ...

    def find_items(self, order_id: OrderId, planned_delay_seconds: float) -> tuple[OrderItem, ...]:
        """Return the order's lines; the database spends ``planned_delay_seconds`` extra on the query."""
        ...
```

`OrderRepository` lists two methods. The body `...` (called an **ellipsis**) after the docstring means "no implementation here". `find_header` returns an `OrderHeader` or `None` (written `OrderHeader | None`). `find_items` takes the extra `planned_delay_seconds`, the planted fault: the database itself spends that long on the query.

```python
class SlowQueryPlanner(Protocol):
    """Decides, per request, how much slower the items query should be (the planted fault)."""

    def next_delay_seconds(self) -> float:
        """Return 0.0 for a normal request, or the extra seconds the items query must take."""
        ...
```

`SlowQueryPlanner` decides, for each request, how much slower the items query should be. It returns `0.0` for a normal request.

```python
class AssemblyScope(Protocol):
    """What the use case may record while an order is being assembled."""

    def record_bucket(self, bucket: ItemCountBucket) -> None:
        """Attach the order-size bucket to the span (and the duration metric) of this assembly."""
        ...


class AssemblyTelemetry(Protocol):
    """Wraps the assembly of one order in a span and a duration measurement."""

    def measure(self) -> AbstractContextManager[AssemblyScope]:
        """Open the span and start the clock; leaving the block closes the span and records the duration."""
        ...
```

`AssemblyScope` is what the use case may do while the order is assembled: record the bucket. `AssemblyTelemetry.measure()` returns a context manager of that scope, so the use case can write `with telemetry.measure() as scope:`. The use case does not know that behind it a span and a histogram exist.

```python
class BackClient(Protocol):
    """How the front service asks the back service for an order."""

    def fetch_order(self, order_id: OrderId) -> Order:
        """Return the order, raising ``OrderNotFoundError`` or ``BackendUnavailableError``."""
        ...
```

`BackClient` is how the front service asks the back service for an order. It says in the docstring which two errors it may raise.

### `application/assemble_order.py`: the back service's use case

```python
"""The back service's use case: read an order and its items and assemble the response."""

from __future__ import annotations

import logging

from three_signals.application.ports import AssemblyTelemetry, OrderRepository, SlowQueryPlanner
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import Order, OrderId

_log = logging.getLogger(__name__)
```

`logging.getLogger(__name__)` returns the logger for this module (`__name__` is the module's dotted name). The leading underscore in `_log` is a convention for "private to this file". See `python-logging.md`.

```python
class AssembleOrder:
    """Loads an order's header and items (two queries) inside one measured span."""

    def __init__(
        self,
        repository: OrderRepository,
        planner: SlowQueryPlanner,
        telemetry: AssemblyTelemetry,
    ) -> None:
        """Wire the use case to its collaborators."""
        self._repository = repository
        self._planner = planner
        self._telemetry = telemetry
```

The class stores its three collaborators in `self._repository`, `self._planner`, `self._telemetry`. They arrive through the constructor ("dependency injection"). A test hands in fakes; the running service hands in the real adapters. The type hints are the ports, not concrete classes.

```python
    def execute(self, order_id: OrderId) -> Order:
        """Return the order, or raise ``OrderNotFoundError``."""
        with self._telemetry.measure() as scope:
            header = self._repository.find_header(order_id)
            if header is None:
                raise OrderNotFoundError(order_id.value)
            items = self._repository.find_items(order_id, self._planner.next_delay_seconds())
            order = Order(header=header, items=items)
            scope.record_bucket(order.item_count_bucket)
        _log.info(
            "order assembled",
            extra={"order_id": order_id.value, "item_count": len(items), "bucket": str(order.item_count_bucket)},
        )
        return order
```

`execute` is the whole use case. `with self._telemetry.measure() as scope:` opens the span and starts the clock; leaving the block, normally or by an error, closes the span and records the duration. Inside: read the header; if there is none, `raise OrderNotFoundError(order_id.value)`; otherwise read the items, passing the planner's delay for this request; build the `Order`; tell the scope the bucket. After the block, log one line. `extra={...}` adds fields to the log record, which the JSON formatter turns into JSON keys (see `python-logging.md`). `str(order.item_count_bucket)` turns the enum into its text, such as `"1-2"`.

### `application/fetch_order.py`: the front service's use case

```python
"""The front service's use case: ask the back service for an order."""

from __future__ import annotations

import logging

from three_signals.application.ports import BackClient
from three_signals.domain.orders import Order, OrderId

_log = logging.getLogger(__name__)


class FetchOrder:
    """Forwards an order lookup to the back service."""

    def __init__(self, back: BackClient) -> None:
        """Wire the use case to the client of the back service."""
        self._back = back

    def execute(self, order_id: OrderId) -> Order:
        """Return the order the back service assembled."""
        order = self._back.fetch_order(order_id)
        _log.info("order fetched from back", extra={"order_id": order_id.value})
        return order
```

The same shape, smaller. `FetchOrder` holds one `BackClient`, calls `fetch_order`, logs `"order fetched from back"` with the order id, and returns the order. It does no HTTP itself; that is the adapter's job.

### `infrastructure/fault.py`: the planted fault

```python
"""The planted fault: one request in twenty has a slow items query."""

from __future__ import annotations

import random


class RandomSlowQueryPlanner:
    """Returns the configured delay with the configured probability, drawing from an injected generator."""

    def __init__(self, probability: float, delay_seconds: float, rng: random.Random) -> None:
        """Take the probability (0 to 1), the delay and the random generator to draw from."""
        self._probability = probability
        self._delay_seconds = delay_seconds
        self._rng = rng

    def next_delay_seconds(self) -> float:
        """Return the delay for this request: ``delay_seconds`` with the given probability, else 0.0."""
        return self._delay_seconds if self._rng.random() < self._probability else 0.0
```

`import random` brings in the standard random-number module. The constructor takes a `probability` between 0 and 1, the `delay_seconds`, and an `rng` (`random.Random` object, an independent generator). Passing the generator in, instead of calling the global `random`, lets a test give it a **seed**: a number that makes the "random" sequence exactly repeatable. `self._rng.random()` returns a float in `[0.0, 1.0)`. The last line is a **conditional expression**: `A if condition else B`. About one request in twenty (probability 0.05) gets the delay; the rest get `0.0`.

### `infrastructure/order_json.py`: the JSON shape of an order

```python
"""The JSON shape of an order on the wire between front, back and the client."""

from __future__ import annotations

from typing import Any

from three_signals.domain.orders import Order, OrderHeader, OrderId, OrderItem


def order_to_json(order: Order) -> dict[str, Any]:
    """Serialize an order the way the back service returns it."""
    return {
        "order": {
            "id": order.header.order_id.value,
            "customer": order.header.customer_name,
            "total_cents": order.total_cents,
            "item_count_bucket": str(order.item_count_bucket),
        },
        "items": [
            {
                "id": item.item_id,
                "sku": item.sku,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
            }
            for item in order.items
        ],
    }


def order_from_json(payload: dict[str, Any]) -> Order:
    """Parse the back service's response; raises ``KeyError``, ``TypeError`` or a domain error when malformed."""
    header = payload["order"]
    items = tuple(
        OrderItem(item["id"], item["sku"], item["quantity"], item["unit_price_cents"]) for item in payload["items"]
    )
    return Order(OrderHeader(OrderId(header["id"]), header["customer"]), items)
```

`order_to_json` turns an `Order` into a nested dict. The back service sends it; `jsonify` writes it as text. The `"items"` value is a **list comprehension** (`[ ... for item in order.items ]`) that builds one small dict per item. `dict[str, Any]` is the return type: a dictionary whose keys are strings and whose values can be anything (`Any` from the `typing` module).

`order_from_json` is the opposite, used by the front service. `payload["order"]` reads a key and raises `KeyError` if it is missing. The `items` value is a tuple built by a generator expression. The last line builds an `Order`; `OrderId(header["id"])` runs the id check, so a response with id `0` raises `InvalidOrderIdError`. The docstring lists the errors the caller must expect: `KeyError`, `TypeError` (wrong shape) and the domain error.

### `infrastructure/settings.py`: configuration from the environment

An **environment variable** is a named text value that a process inherits from the program that started it. Configuration through the environment follows the "twelve-factor" method: the same image runs everywhere and only the variables differ.

```python
"""Configuration read once from the environment and validated (twelve-factor)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(ValueError):
    """An environment variable is missing or has an unusable value."""
```

`Mapping` is the read-only dictionary type. `os.environ` is one, and a test can pass a plain dict instead. `SettingsError` inherits from `ValueError`, so a bad value is a kind of value error.

```python
def _text(env: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = env.get(name, default)
    if value is None or value == "":
        raise SettingsError(f"{name} is required")
    return value
```

`_text` reads a required text variable: `env.get(name, default)` returns the value, or the default when the name is absent. If the result is `None` or an empty string, it raises `SettingsError("NAME is required")`. A function whose name starts with an underscore is private to the module.

```python
def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as error:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from error
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value
```

`_number` reads a number within bounds. The `try` block converts text to `float`. If the variable is missing or empty, the default is used. A bad text raises `ValueError`, which is caught and re-raised as `SettingsError` with `from error` so the cause stays attached. `{raw!r}` shows the bad value with quotes. The chained comparison `low <= value <= high` is true only when the value is inside the range; Python lets you chain comparisons like in mathematics.

```python
def _whole(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    return int(_number(env, name, default, low, high))
```

`_whole` reuses `_number` and converts the result to `int`.

```python
def _log_level(env: Mapping[str, str]) -> str:
    level = env.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise SettingsError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR, got {level!r}")
    return level
```

`_log_level` upper-cases the variable (so `debug` is accepted) and checks it against a **set** of allowed names written with braces. A set holds each value once and answers "is this in it?" quickly.

```python
@dataclass(frozen=True, slots=True)
class BackSettings:
    """Everything the back service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    database_url: str
    pool_max_size: int
    slow_query_probability: float
    slow_query_seconds: float
    slow_query_seed: int | None
    slow_query_warn_seconds: float
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BackSettings:
        """Build the settings, raising ``SettingsError`` for a missing or invalid variable."""
        seed = env.get("SLOW_QUERY_SEED", "")
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "back"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=_whole(env, "BACK_PORT", 8081, 1, 65535),
            database_url=_text(env, "DATABASE_URL"),
            pool_max_size=_whole(env, "DB_POOL_MAX_SIZE", 8, 1, 64),
            slow_query_probability=_number(env, "SLOW_QUERY_PROBABILITY", 0.05, 0.0, 1.0),
            slow_query_seconds=_number(env, "SLOW_QUERY_SECONDS", 1.5, 0.0, 30.0),
            slow_query_seed=int(seed) if seed else None,
            slow_query_warn_seconds=_number(env, "SLOW_QUERY_WARN_SECONDS", 1.0, 0.0, 30.0),
            log_level=_log_level(env),
        )
```

`BackSettings` is a frozen dataclass: one field per setting. `from_env` is a class method that reads the environment and returns a new object. `service_name` defaults to `back`; `bind_host` to `127.0.0.1` (loopback only, safe on a laptop); `port` to 8081 within 1 to 65535; `database_url` is required; `pool_max_size` defaults to 8; `slow_query_probability` defaults to 0.05 within 0 to 1; `slow_query_seconds` to 1.5; `slow_query_seed` is an `int` or `None` (`int(seed) if seed else None`: empty text means "not seeded"); `slow_query_warn_seconds` (1.0) is the duration above which the query is logged as slow.

```python
@dataclass(frozen=True, slots=True)
class FrontSettings:
    """Everything the front service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    back_url: str
    back_timeout_seconds: float
    back_retries: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> FrontSettings:
        """Build the settings, raising ``SettingsError`` for a missing or invalid variable."""
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "front"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=_whole(env, "FRONT_PORT", 8080, 1, 65535),
            back_url=_text(env, "BACK_URL").rstrip("/"),
            back_timeout_seconds=_number(env, "BACK_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            back_retries=_whole(env, "BACK_RETRIES", 2, 0, 5),
            log_level=_log_level(env),
        )
```

`FrontSettings` follows the same pattern for the front service: the URL of the back service (required, with a trailing `/` removed by `.rstrip("/")`), a timeout (0.1 to 60 seconds, default 5), and the number of retries (0 to 5, default 2).

### `lab/config.py`: where the lab tools find the stack

```python
"""Where the lab tooling finds the stack: URLs and the Grafana admin login, from the environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LabSettings:
    """Addresses of the published ports on this machine."""

    front_url: str
    prometheus_url: str
    tempo_url: str
    loki_url: str
    grafana_url: str
    grafana_user: str
    grafana_password: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> LabSettings:
        """Derive every URL from the same ``*_HOST_PORT`` variables the compose file publishes."""
        return cls(
            front_url=f"http://127.0.0.1:{env.get('FRONT_HOST_PORT', '28081')}",
            prometheus_url=f"http://127.0.0.1:{env.get('PROMETHEUS_HOST_PORT', '29090')}",
            tempo_url=f"http://127.0.0.1:{env.get('TEMPO_HOST_PORT', '23200')}",
            loki_url=f"http://127.0.0.1:{env.get('LOKI_HOST_PORT', '23100')}",
            grafana_url=f"http://127.0.0.1:{env.get('GRAFANA_HOST_PORT', '23000')}",
            grafana_user="admin",
            grafana_password=env.get("GRAFANA_ADMIN_PASSWORD", ""),
        )
```

`LabSettings.from_env` builds the base URLs of the services published on your machine. `env.get('FRONT_HOST_PORT', '28081')` is the same variable the Compose file uses for the published port, so the lab tools and the stack always agree. The f-strings assemble `http://127.0.0.1:<port>`. `grafana_password` is the only secret and comes from the environment.

### `lab/follow.py`: the click path as code

This module does with code what you do with the mouse in Grafana: start at the latency spike, take the slowest exemplar, open its trace, then read the log lines of that trace.

```python
"""Follow one slow request from the latency panel to its trace to its log lines, the way the lesson's clicks do."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from three_signals.lab.stores import Exemplar, LogLine, SpanView, StoreError, is_complete

P95_EXPRESSION = "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[1m])))"
EXEMPLAR_SELECTOR = "order_assembly_duration_seconds_bucket"
SLOW_SPAN_SECONDS = 1.0
MAX_TRACES_OPENED = 10
```

The constants name the p95 query (see `promql.md`), the metric whose exemplars to read, the one-second threshold, and how many traces to open at most.

```python
class MetricSource(Protocol):
    """The Prometheus calls the walk-through needs."""

    def query_range(self, expression: str, start: float, end: float, step_seconds: float) -> list[tuple[float, float]]:
        """Points of one series."""
        ...

    def exemplars(self, selector: str, start: float, end: float) -> list[Exemplar]:
        """Exemplars stored for a selector."""
        ...


class TraceSource(Protocol):
    """The Tempo calls the walk-through needs."""

    def wait_for_trace(
        self, trace_id: str, timeout_seconds: float = ..., until: Callable[[list[SpanView]], bool] = ...
    ) -> list[SpanView]:
        """The spans of one trace, once ``until`` accepts them."""
        ...


class LogSource(Protocol):
    """The Loki call the walk-through needs."""

    def query(self, logql: str, start: float, end: float, limit: int = ...) -> list[LogLine]:
        """Lines matching a LogQL query."""
        ...
```

Three small `Protocol`s describe the only methods of Prometheus, Tempo and Loki clients that this module uses. In the `TraceSource` protocol the default value `...` for a parameter is how a protocol says "this parameter has a default, whatever it is". A test passes fake sources, the real command passes the real clients. `Callable[[list[SpanView]], bool]` is the type of a function that takes a list of spans and returns a bool.

```python
@dataclass(frozen=True, slots=True)
class Investigation:
    """What each click found."""

    peak_p95_seconds: float
    peak_time: float
    exemplar: Exemplar
    spans: tuple[SpanView, ...]
    slow_span: SpanView
    log_lines: tuple[LogLine, ...]
    reverse_trace_id: str

    @property
    def services_in_trace(self) -> frozenset[str]:
        """The services that contributed spans to the trace."""
        return frozenset(span.service for span in self.spans)
```

`Investigation` is the frozen result of the walk: the peak p95, when it happened, the chosen exemplar, the spans of its trace, the slowest database span, the log lines and the trace id found by reading the first log line. `services_in_trace` is a property that returns a `frozenset` (an immutable set) of the service names.

```python
def has_both_services(spans: list[SpanView]) -> bool:
    """True when the trace is whole and holds spans from both front and back."""
    return is_complete(spans) and {"front", "back"} <= {span.service for span in spans}


def slowest_database_span(spans: list[SpanView]) -> SpanView:
    """The widest span that queried the order_items table."""
    candidates = [span for span in spans if "order_items" in str(span.attributes.get("db.statement", ""))]
    if not candidates:
        candidates = [span for span in spans if span.name.startswith("SELECT")]
    if not candidates:
        raise StoreError("the trace has no database span")
    return max(candidates, key=lambda span: span.duration_ms)
```

`has_both_services` is true when the trace is whole (`is_complete`, see `requests.md`) and the set `{"front", "back"}` is a **subset** (`<=`) of the services found. `slowest_database_span` looks for spans whose `db.statement` attribute mentions the table `order_items`; if none, any span whose name starts with `SELECT`; if still none, it raises `StoreError`. `max(candidates, key=lambda span: span.duration_ms)` picks the widest; a `lambda` is a one-line anonymous function.

```python
def log_trace_id(line: LogLine) -> str:
    """The trace id of a log line: its structured metadata, or the ``trace_id`` field of its JSON body."""
    if "trace_id" in line.structured_metadata:
        return str(line.structured_metadata["trace_id"])
    try:
        body: Mapping[str, object] = json.loads(line.line)
    except ValueError:
        return ""
    return str(body.get("trace_id", ""))
```

A log line's trace id can sit in structured metadata or in the JSON body. This function checks the metadata first. `json.loads(line.line)` parses the text; a line that is not JSON raises `ValueError` and the function returns an empty string.

```python
def _open_until_a_slow_database_span(
    traces: TraceSource, candidates: list[Exemplar]
) -> tuple[Exemplar, list[SpanView], SpanView]:
    """Open exemplar traces, slowest first, until one has a slow database span; else the one with the widest.

    A slow assembly can also come from queueing or a busy host. The trace worth opening is the one whose database
    span is slow, as it is when the planted fault, not the machine, made the request slow.
    """
    opened: list[tuple[Exemplar, list[SpanView], SpanView]] = []
    for candidate in candidates[:MAX_TRACES_OPENED]:
        spans = traces.wait_for_trace(candidate.trace_id, until=has_both_services)
        slow_span = slowest_database_span(spans)
        opened.append((candidate, spans, slow_span))
        if slow_span.duration_ms >= SLOW_SPAN_SECONDS * 1000:
            break
    return max(opened, key=lambda item: item[2].duration_ms)
```

Not every slow request is slow because of the database: a busy machine can make anything slow. This function opens at most ten exemplar traces, slowest first, and stops at the first whose database span lasted at least a second (`SLOW_SPAN_SECONDS * 1000` milliseconds). It returns the one with the widest database span.

```python
def follow_slow_request(
    metrics: MetricSource,
    traces: TraceSource,
    logs: LogSource,
    window_seconds: float = 900.0,
    now: float | None = None,
) -> Investigation:
    """Start at the p95 panel's peak, take the slowest exemplar, open its trace, then read its log lines."""
    end = now if now is not None else time.time()
    start = end - window_seconds
    points = metrics.query_range(P95_EXPRESSION, start, end, 5.0)
    if not points:
        raise StoreError("the p95 panel has no data in the window; run the scenario first")
    peak_time, peak_value = max(points, key=lambda point: point[1])

    exemplars = metrics.exemplars(EXEMPLAR_SELECTOR, start, end)
    slow_exemplars = sorted(
        (exemplar for exemplar in exemplars if exemplar.value >= SLOW_SPAN_SECONDS),
        key=lambda candidate: candidate.value,
        reverse=True,
    )
    if not slow_exemplars:
        raise StoreError("no slow exemplar found; was the slow-query fault enabled?")
    exemplar, spans, slow_span = _open_until_a_slow_database_span(traces, slow_exemplars)

    lines = logs.query(f'{{service_name="back"}} | trace_id="{exemplar.trace_id}"', start, end + 60)
    if not lines:
        raise StoreError(f"no log lines for trace {exemplar.trace_id}")
    return Investigation(
        peak_p95_seconds=peak_value,
        peak_time=peak_time,
        exemplar=exemplar,
        spans=tuple(spans),
        slow_span=slow_span,
        log_lines=tuple(lines),
        reverse_trace_id=log_trace_id(lines[0]),
    )
```

`follow_slow_request` is the walk. Click 1: query the p95 over the last 15 minutes (`window_seconds=900`) in 5-second steps and take the maximum point. Click 2: read the exemplars, keep those at least a second long and sort them slowest first with `sorted(..., key=..., reverse=True)`. Click 3: open the trace. Click 4: ask Loki for lines of the back service with that trace id (the f-string doubles the braces `{{` `}}` to write literal braces). Each dead end raises `StoreError` with a message that says where the trail went cold.

```python
def follow_with_patience(
    metrics: MetricSource,
    traces: TraceSource,
    logs: LogSource,
    patience_seconds: float,
    pause_seconds: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Investigation:
    """Retry ``follow_slow_request`` while telemetry reaches the stores; raise the last error at the deadline."""
    deadline = clock() + patience_seconds
    while True:
        try:
            return follow_slow_request(metrics, traces, logs)
        except StoreError:
            if clock() >= deadline:
                raise
            sleep(pause_seconds)
```

`follow_with_patience` retries the whole walk until a deadline, because telemetry takes seconds to reach the stores. `clock` and `sleep` are parameters so a test can fake time. The loop catches `StoreError`; if the deadline has passed it re-raises with a bare `raise` (the same error), otherwise it sleeps and tries again.

```python
def render(investigation: Investigation) -> str:
    """The investigation as numbered clicks, ready to paste into a runbook."""
    exemplar = investigation.exemplar
    lines = [
        f"1. Latency panel: p95 peaks at {investigation.peak_p95_seconds * 1000:.0f} ms "
        f"at {time.strftime('%H:%M:%S', time.localtime(investigation.peak_time))}.",
        f"2. Click the exemplar dot: trace {exemplar.trace_id}, a {exemplar.value * 1000:.0f} ms assembly.",
        f"3. The trace spans {', '.join(sorted(investigation.services_in_trace))} "
        f"with {len(investigation.spans)} spans; the widest database span is "
        f"'{investigation.slow_span.name}' at {investigation.slow_span.duration_ms:.0f} ms "
        f"({investigation.slow_span.attributes.get('db.statement', '')}).",
        f"4. Click 'logs for this span': {len(investigation.log_lines)} lines carry trace_id={exemplar.trace_id}:",
    ]
    lines.extend(f"     {line.line}" for line in investigation.log_lines)
    lines.append(
        f"5. Back the other way: the first log line's trace_id is {investigation.reverse_trace_id}, "
        f"{'the same trace' if investigation.reverse_trace_id == exemplar.trace_id else 'a different trace'}."
    )
    return "\n".join(lines)
```

`render` formats the result as five numbered clicks. `time.strftime('%H:%M:%S', time.localtime(...))` turns a Unix time (seconds since 1970) into a clock time. `:.0f` formats a float with no decimals. `lines.extend(f"     {line.line}" for line in ...)` appends the log lines. `"\n".join(lines)` joins them with line breaks.

## Try it yourself

Run these in the repository folder after `make install` (it creates `.venv`).

```bash
.venv/bin/python -c "from three_signals.domain.orders import ItemCountBucket; print([ItemCountBucket.for_count(n) for n in (0, 1, 3, 9)])"
```

Expected: `[<ItemCountBucket.NONE: '0'>, <ItemCountBucket.ONE_TO_TWO: '1-2'>, <ItemCountBucket.THREE_TO_FIVE: '3-5'>, <ItemCountBucket.SIX_OR_MORE: '6+'>]`.

```bash
.venv/bin/python -c "from three_signals.domain.orders import OrderId; OrderId(0)"
```

Expected: a traceback ending in `InvalidOrderIdError: order id must be a positive integer, got 0`.

```bash
.venv/bin/python -c "from three_signals.infrastructure.settings import BackSettings; print(BackSettings.from_env({'DATABASE_URL': 'postgresql://x'}))"
```

Expected: one line showing every field with its default (`port=8081`, `slow_query_probability=0.05`).

```bash
.venv/bin/python -c "from three_signals.infrastructure.settings import BackSettings; BackSettings.from_env({})"
```

Expected: `SettingsError: DATABASE_URL is required`.

```bash
.venv/bin/python -m pytest tests/unit/domain tests/unit/application -q
```

Expected: a line such as `19 passed` and no failures.

## Mistakes people make

- **A mutable default argument.** Writing `def f(items=[])` shares one list between all calls. Use `None` and create the list inside the function. The code in this repository either uses immutable defaults (`()`, `None`, numbers) or `field(default_factory=dict)` in dataclasses.
- **Catching everything.** `except Exception:` hides bugs. This repository catches named errors and lets the rest travel up to one place that logs them and returns a 500 answer.
- **Comparing floats with `==`.** Floating-point numbers carry tiny rounding errors. Tests here compare with ranges or with exact values that are safe (for example `0.25` is exactly representable).
- **Using the wall clock to measure durations.** `time.time()` can jump when the system clock is corrected. Code that measures how long something took uses `time.monotonic()`, which only moves forward.
- **Forgetting that `bool` is an `int`.** In Python `True == 1`. The `OrderId` check rejects booleans on purpose.
- **Installing packages into the system Python.** Use a virtual environment. `make install` creates `.venv` for you.

## Check yourself

1. What is the difference between an image and a container? *An image is the read-only package; a container is a running process started from it.*
2. Why does a Dockerfile copy the dependency file before the source code? *So Docker can reuse the cached install layer when only the source changed.*
3. What happens to files a container writes when the container is removed? *They disappear, unless they were written to a volume or a bind mount.*
4. What does a digest give you that a tag does not? *A tag can be moved to a different image later; a digest always names the same bytes.*
5. Which signal does `docker stop` send first, and what comes after the grace period? *`SIGTERM`; then `SIGKILL`.*

## Further reading

- Python 3.12 documentation: https://docs.python.org/3.12/
- The tutorial: https://docs.python.org/3.12/tutorial/index.html
- The standard library index: https://docs.python.org/3.12/library/index.html
- Type hints (`typing`): https://docs.python.org/3.12/library/typing.html
