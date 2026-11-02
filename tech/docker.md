# Docker (Dockerfile and .dockerignore)

## What it is

Docker is a tool that runs programs inside **containers**. A container is a normal process on your machine, but the operating system gives it its own private view: its own file system, its own network address, its own list of processes. The program inside cannot see the rest of your machine unless you let it. Because the container carries its own copy of every file the program needs (the Python interpreter, the libraries), the program runs the same way on every machine.

An **image** is the read-only package a container starts from: a stack of file-system layers plus a few settings (which command to run, which user, which environment variables). A **Dockerfile** is the text file with the recipe for building an image. A **registry** (for example Docker Hub) is a server that stores images; `docker pull` downloads one.

On macOS and Windows, Docker Desktop runs a small Linux virtual machine, and the containers run inside it. That is why container files are not on your normal disk, and why Docker has its own disk limit.

## Why this repository uses it

The lesson's stack has eight moving parts (two services, a database, a Collector, Tempo, Prometheus, Loki, Grafana). Containers let the same pinned versions run on any laptop with one command. The two services share **one image**: the Dockerfile builds it once, and Compose chooses the command (`three-signals-front` or `three-signals-back`) per service.

## Where it appears

| Path | What it does with Docker |
| --- | --- |
| `Dockerfile` | The recipe for the service image. |
| `.dockerignore` | Files the build must not copy into the image. |
| `docker-compose.yml` | Builds the image and runs every container (see `docker-compose.md`). |
| `Makefile` | `docker-build` builds the image; `lint-config` runs the Prometheus and Collector images as one-off tools. |
| `.github/workflows/ci.yml` | CI builds and runs the stack. |
| `tests/observability/test_configuration_as_code.py` | Checks the base image is pinned by digest and the user is not root. |

## How it works underneath

Docker uses two features of the Linux kernel. **Namespaces** give a process its own view of process ids, network interfaces, mount points and host name. **Cgroups** limit how much CPU and memory the process may use. There is no second operating system inside a container; it is one process of the host's kernel, wearing blinders.

An image is made of **layers**. Every instruction in a Dockerfile that changes files (`RUN`, `COPY`, `ADD`) creates one layer on top of the previous. Docker stores each layer once and caches it. When you build again, Docker reuses every layer whose instruction and inputs did not change, and rebuilds from the first one that did. That is why a Dockerfile copies the dependency list and installs the packages **before** it copies the source code: source code changes often, dependencies rarely, so the slow install layer stays cached.

When you start a container, Docker adds a thin writable layer on top of the image layers. Anything the program writes goes there and disappears when the container is removed. A **volume** is a folder managed by Docker that outlives the container; databases keep their data in volumes. A **bind mount** maps a folder or file from your machine into the container.

A container has a main process (**PID 1**). When that process exits, the container stops. `docker stop` sends it the signal `SIGTERM`, waits a grace period (10 seconds unless told otherwise), then sends `SIGKILL`, which cannot be caught.

A **health check** is a command Docker runs inside the container every few seconds. Exit code 0 means healthy, 1 means unhealthy. Docker records the state; Compose can wait for it.

A **digest** (`@sha256:...`) names an exact image by the hash of its contents. A **tag** (`python:3.12.8-slim-bookworm`) is a movable label; the owner of the image can point it at a different image later. A digest cannot move, so pinning by digest makes a build repeatable.

## Walkthrough of this repository's use

### `Dockerfile`

```dockerfile
# One image for both services: the compose file picks the command (front or back) and the service name.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0
```

The first line is a comment; the second is `FROM`, which names the **base image** everything else is added to: `python:3.12.8-slim-bookworm`, the official Python 3.12.8 on Debian 12 ("bookworm") in its "slim" variant (without compilers and extras). `@sha256:2199...` pins it by digest, so the build always starts from exactly the same bytes even if the tag is later re-pointed. A test checks the line ends with a 64-character digest.

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

`ENV` sets environment variables for the build and for the running container. `PYTHONDONTWRITEBYTECODE=1` stops Python from writing `.pyc` cache files (pointless in a container). `PYTHONUNBUFFERED=1` makes Python write output immediately instead of in blocks, so `docker logs` shows lines at once. `PIP_NO_CACHE_DIR=1` makes `pip` skip its download cache so the layer is smaller. `PIP_DISABLE_PIP_VERSION_CHECK=1` stops `pip` from calling the internet to look for a newer `pip`. The backslashes continue one instruction over several lines.

```dockerfile
WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
```

`WORKDIR /app` sets the working directory (created if needed) for all later instructions. `COPY requirements.lock ./` copies only the lock file into `/app`. `RUN pip install --require-hashes -r requirements.lock` installs every dependency, and refuses any file whose hash is not in the lock (see `uv-and-lock-files.md`). Because this layer depends only on the lock file, Docker reuses it from its cache until the lock changes, even when the source code changes.

```dockerfile
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .
```

`COPY pyproject.toml README.md ./` and `COPY src ./src` bring in the project (the README is needed because `pyproject.toml` declares it as the package's readme). `RUN pip install --no-deps .` installs the project itself without touching dependencies; `--no-deps` means "the lock already did that". This also creates the commands `three-signals-front`, `three-signals-back` and `three-signals-health` in the image.

```dockerfile
RUN useradd --system --uid 10001 --no-create-home app
USER app
```

`RUN useradd --system --uid 10001 --no-create-home app` creates an unprivileged system user with a fixed id and no home folder. `USER app` makes every later instruction and the running container use it. If an attacker took over the service, they would not be root inside the container. A test checks that this line exists.

```dockerfile
# opentelemetry-instrument applies the automatic instrumentation (Flask, requests, psycopg) and reads the
# OTEL_* variables; the compose file sets them per service.
CMD ["opentelemetry-instrument", "three-signals-front"]
```

The comment says why the command starts with `opentelemetry-instrument`. `CMD ["opentelemetry-instrument", "three-signals-front"]` is the default command in **exec form** (a JSON list): Docker starts that program directly as process 1, so it receives `SIGTERM` itself. Compose overrides it for the back service. There is no `HEALTHCHECK` here because the health check differs per service and lives in Compose.

### `.dockerignore`

```
.git
.venv
logs
tests
docs
observability
sql
**/__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
.coverage
.env
```

When you build, Docker sends the **build context** (this folder) to the builder. `.dockerignore` lists what to leave out: the git history (`.git`), the virtual environment (`.venv`), logs, tests, docs, the observability configs and the SQL files (none are needed inside the service image), Python caches (`**/__pycache__` means any folder of that name at any depth) and tool caches, the coverage file, and `.env` (which holds local secrets and must never end up in an image). A smaller context builds faster, and the cache is not invalidated by irrelevant changes.

## Try it yourself

```bash
make docker-build
```

Expected: a successful build ending with a line such as `naming to docker.io/library/observability-three-signals:dev`.

```bash
docker image inspect observability-three-signals:dev --format '{{.Config.User}} {{.Config.Cmd}}'
```

Expected: `app [opentelemetry-instrument three-signals-front]`.

```bash
docker run --rm --entrypoint id observability-three-signals:dev
```

Expected: `uid=10001(app) gid=...` and not `uid=0(root)`.

```bash
docker history observability-three-signals:dev | head
```

Expected: one row per layer, the biggest being the dependency install.

```bash
docker compose exec back sh -c 'pip list 2>/dev/null | grep -i -E "^(flask|waitress|psycopg|requests) "'
```

Expected: the four libraries with the versions of `requirements.lock`.

## Mistakes people make

- **Running as root.** The default user inside a container is root. If the program is compromised, the attacker is root inside the container. This repository creates an unprivileged user and switches to it with `USER`.
- **Tag `latest`.** It changes silently. Every third-party image here is pinned to a version, and the base image is pinned by digest.
- **Copying everything before installing dependencies.** Any change to any file then invalidates the install layer. Copy the dependency list first.
- **Storing data in the container's writable layer.** It is deleted with the container. Use a volume.
- **Putting secrets in the image.** Anything in a layer can be read by anyone with the image. Secrets come in at run time through environment variables.
- **A shell-form `CMD`.** `CMD python app.py` runs through `/bin/sh -c`, so the shell becomes PID 1 and may swallow `SIGTERM`. The exec form (`CMD ["python", "app.py"]`) makes your program PID 1.
- **Forgetting that `localhost` inside a container is the container itself.** To reach another container, use its service name on the shared network.

## Check yourself

1. What is the difference between an image and a container? *An image is the read-only package; a container is a running process started from it.*
2. Why does a Dockerfile copy the dependency file before the source code? *So Docker can reuse the cached install layer when only the source changed.*
3. What happens to files a container writes when the container is removed? *They disappear, unless they were written to a volume or a bind mount.*
4. What does a digest give you that a tag does not? *A tag can be moved to a different image later; a digest always names the same bytes.*
5. Which signal does `docker stop` send first, and what comes after the grace period? *`SIGTERM`; then `SIGKILL`.*

## Further reading

- Docker overview: https://docs.docker.com/get-started/docker-overview/
- Dockerfile reference: https://docs.docker.com/reference/dockerfile/
- Build cache: https://docs.docker.com/build/cache/
- `docker run` reference: https://docs.docker.com/reference/cli/docker/container/run/
