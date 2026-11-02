# import-linter

## What it is

import-linter is a command (`lint-imports`) that checks the **import rules** of a Python project. In a layered design each folder (layer) may only use the layers below it. Nothing in the Python language stops a developer from breaking that, so import-linter reads the `import` statements of every module, builds a graph of who imports whom, and fails when the graph breaks a rule written in the configuration.

## Why this repository uses it

The lesson's code is organised in layers so that telemetry lives in exactly the places it belongs. The rule "the domain and the application know nothing about Flask, psycopg, requests or OpenTelemetry" is the whole point of the layout. A comment cannot enforce it; `lint-imports` can, and CI runs it.

## Where it appears

| Path | What it does |
| --- | --- |
| `pyproject.toml` (`[tool.importlinter]` and its contracts) | The rules. |
| `Makefile` (`lint`) | Runs `lint-imports`. |
| `.gitignore`, `Makefile` (`clean`) | Mention `.import_linter_cache`, the folder where the tool caches its graph. |

## How it works underneath

import-linter uses a library called `grimp` to build the **import graph** of the package named in `root_package`: one node per module, one arrow per `import`. It then checks each **contract** in the configuration.

- A **layers** contract lists layers from the top to the bottom. A module may import from layers below it, never from layers above it. Layers written on one line separated by `|` are *independent siblings*: neither may import the other.
- A **forbidden** contract lists `source_modules` and `forbidden_modules`: none of the sources may import any of the forbidden modules, directly or through a chain of other modules. With `include_external_packages = true` the graph also contains third-party packages (Flask, requests, psycopg), so a contract can forbid them.

If a rule is broken the command prints the exact import chain and exits non-zero.

## Walkthrough of this repository's use

```toml
[tool.importlinter]
root_package = "three_signals"
include_external_packages = true
```

`root_package = "three_signals"` is the package whose imports are analysed. `include_external_packages = true` adds third-party packages to the graph so contracts can talk about them.

```toml
[[tool.importlinter.contracts]]
name = "Layers: interfaces and lab -> infrastructure -> application -> domain, never upward"
type = "layers"
layers = [
    "three_signals.interfaces | three_signals.lab",
    "three_signals.infrastructure",
    "three_signals.application",
    "three_signals.domain",
]
```

The first contract is a `layers` contract. The layers from top to bottom: `three_signals.interfaces | three_signals.lab` (two independent top layers), then `infrastructure`, then `application`, then `domain`. The rule: a module may import from a layer below it, never from one above. So `domain` imports nothing from the others; `application` may import `domain` only; `infrastructure` may import `application` and `domain`; `interfaces` and `lab` may import everything below but not each other (the `|`). That last part keeps the services free of lab tooling: the lab package is "verification tooling that talks to the stack from outside".

```toml
[[tool.importlinter.contracts]]
name = "Domain and application know nothing about web, database or telemetry libraries"
type = "forbidden"
source_modules = ["three_signals.domain", "three_signals.application"]
forbidden_modules = ["flask", "psycopg", "psycopg_pool", "requests", "opentelemetry", "waitress"]
```

The second contract is `forbidden`: `three_signals.domain` and `three_signals.application` may not import `flask`, `psycopg`, `psycopg_pool`, `requests`, `opentelemetry` or `waitress`, even indirectly. This is why `application/ports.py` only imports from the standard library and the domain, and why the OpenTelemetry code lives in `infrastructure/otel_telemetry.py` behind the `AssemblyTelemetry` protocol.

## Try it yourself

```bash
.venv/bin/lint-imports
```

Expected: both contracts listed as `KEPT` and the line `Contracts: 2 kept, 0 broken.`

To see a break, add `import flask` as a new first line of `src/three_signals/domain/orders.py`, run the command again and read the broken contract with the import chain, then remove the line:

```bash
sed -i.bak '1i\
import flask
' src/three_signals/domain/orders.py && .venv/bin/lint-imports; mv src/three_signals/domain/orders.py.bak src/three_signals/domain/orders.py
```

Expected: `Domain and application know nothing about web, database or telemetry libraries BROKEN` and `Contracts: 1 kept, 1 broken.`; the last command restores the file. Check with `git status` that the tree is clean.

```bash
.venv/bin/lint-imports --verbose | head -20
```

Expected: the analysed modules and contract results.

```bash
ls -a | grep import_linter_cache
```

Expected: `.import_linter_cache` (ignored by git).

```bash
make lint
```

Expected: ends with the import-linter lines above.

## Mistakes people make

- **Writing the layers in the wrong order.** The first layer listed is the *highest*.
- **Forgetting `include_external_packages`**, which makes rules about third-party libraries silently empty.
- **Letting a rule drift.** A rule that is not run in CI is only a wish; `make lint` runs it.

## Check yourself

1. What does a layers contract forbid? *A lower layer importing a higher one.*
2. What does `A | B` between two layers mean? *They are independent: neither may import the other.*
3. Why does `include_external_packages = true` matter? *It lets a contract forbid third-party packages such as Flask.*
4. What exit code does `lint-imports` give when a contract is broken? *Non-zero.*
5. Why keep the domain free of web and database libraries? *So the business rules can be tested and reused without them.*

## Further reading

- import-linter documentation (2.15): https://import-linter.readthedocs.io/en/stable/
- Contract types: https://import-linter.readthedocs.io/en/stable/contract_types.html
