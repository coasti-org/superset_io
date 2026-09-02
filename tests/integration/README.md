# Integration tests

These tests exercise `superset_io` against a running Apache Superset instance.
They are marked with `@pytest.mark.integration` and are skipped during a normal
`pytest` run.

## Prerequisites

- Docker Desktop (or another Docker Engine with Docker Compose)
- Project dependencies installed:

```bash
uv sync --all-groups --all-extras
```

## Run with the managed Docker stack

From the repository root, run:

```bash
uv run pytest --integration
```

The test fixture uses [docker-compose.yml](docker-compose.yml) to start the
Postgres, Redis, and Superset services, waits for the Superset health endpoint,
and removes the stack and its volumes when the test session finishes.

The first run can take several minutes while Docker downloads the images and
Superset initializes its database.

## Run against an already-running stack

Start the services manually:

```bash
docker compose -f tests/integration/docker-compose.yml up -d
```

Run the tests from the repository root:

```bash
uv run pytest --integration
```

The fixture detects the running stack and does not stop it after the tests.
Stop it manually when finished:

```bash
docker compose -f tests/integration/docker-compose.yml down -v --remove-orphans
```

## Run a subset of tests

For example:

```bash
uv run pytest --integration tests/integration/test_io.py -q
```

## Configuration

The default managed stack is available at `http://localhost:8088` with the
following credentials:

- Username: `admin`
- Password: `admin`

The test options can be inspected with:

```bash
uv run pytest --help
```

The `--superset-url`, `--superset-username`, and `--superset-password` options
also accept the environment variables `SUPERSET_TEST_URL`,
`SUPERSET_TEST_USERNAME`, and `SUPERSET_TEST_PASSWORD`, respectively.
