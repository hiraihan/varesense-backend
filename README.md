# Varesense.ai Backend

Varesense.ai ingests industrial edge-device telemetry and turns it into operational intelligence — energy analytics, machine-health anomaly detection, threshold alerts, and auditable reports. This repository is the backend service: a **Python FastAPI modular monolith**.

| Status | Value |
| --- | --- |
| Tests | 80 passing |
| Type check | mypy clean (54 source files) |
| API docs | `/docs` (OpenAPI) |
| API guide | [`docs/api-guide.md`](docs/api-guide.md) |

---

## Overview

The service is a modular monolith: one deployable application, cleanly separated domain modules. Each module owns its models, service logic, and API router. The functional surface is exposed under the `/v1` prefix, with a JSON REST API plus a WebSocket feed for realtime push.

### Modules

| Module | Responsibility |
| --- | --- |
| `auth` | Org bootstrap, users, login, JWT access + rotating refresh tokens |
| `assets` | Hierarchical asset tree (site → area → line → machine), history, archiving |
| `devices` | Edge-node registry, device credentials, sensor/channel endpoints |
| `telemetry` | Partitioned sample ingestion, dead-letter queue, quality classification |
| `analytics` | Baselines, trends, z-score anomaly detection |
| `energy` | Rollups, trends, asset comparison, carbon estimates, efficiency |
| `alerts` | Versioned threshold rules, alert lifecycle/state machine |
| `reports` | Scoped aggregation export (CSV / PDF) |
| `maintenance` | Maintenance records |
| `realtime` | WebSocket push hub |
| `audit` | Append-only audit log |

---

## Tech stack

- **Python 3.12+**
- **FastAPI** — async REST framework
- **SQLAlchemy 2.0** (async) — ORM
- **PostgreSQL 16** — primary store (time-range partitioned `telemetry`)
- **Alembic** — schema migrations
- **PyJWT** — access/refresh tokens
- **ReportLab** — PDF report rendering
- **APScheduler** — in-process anomaly-detection schedule
- **uv** — package/project manager

---

## Architecture notes

- **Modular monolith** — single service, clear domain boundaries. Easy to split out a module into its own service later (see ADR-0001).
- **Partitioned telemetry** — `telemetry` is time-range partitioned (partition key `measured_at`) with a default partition; ingestion stays fast as history grows.
- **Alert engine** — rules are versioned (`rule_key` lineage); each edit inserts a new version and deactivates the previous one. A partial unique index (`organization_id, rule_key`) `WHERE active` guarantees one active version per lineage even under concurrency.
- **Anomaly → alert** — statistical anomalies (z-score) materialize as `Alert` rows with `rule_id = NULL` and `source.method = "z-score"`. A scheduled job runs detection for every organization at `anomaly_detect_interval_minutes` (default 15).
- **Refresh rotation** — refresh tokens are recorded in a server-side ledger (`refresh_tokens`, keyed by `jti`); `/auth/refresh` revokes the presented token before minting a new pair, so old tokens cannot be replayed.
- **Row-level security** — RLS policies (`tenant_isolation`) are enabled on all org-scoped tables. They are inert under the current superuser connection role and bind once the app runs under a least-privilege role (defense-in-depth).
- **Audit** — every state-changing, user-facing action writes an append-only audit record.
- **Reports** — aggregation is computed on read over valid telemetry only; non-valid samples are surfaced as explicit quality gaps, never silently filled.

---

## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16 (local, Docker, or hosted)

### 1. Database

```bash
docker run --name varesense-pg \
  -e POSTGRES_USER=varesense \
  -e POSTGRES_PASSWORD=varesense \
  -e POSTGRES_DB=varesense \
  -p 5433:5432 \
  -d postgres:16
```

### 2. Install

```bash
cd varesense-backend
cp .env.example .env   # then edit secrets/connection as needed
uv sync --extra dev
```

### 3. Migrate

```bash
uv run alembic upgrade head
```

### 4. Run

```bash
uv run uvicorn varesense.main:app --reload
```

- API + interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/v1/health>
- Readiness: <http://localhost:8000/v1/ready>

### 5. Tests & type check

```bash
uv run pytest
uv run mypy src/varesense
```

---

## Configuration

All settings read environment variables prefixed `VARESENSE_` (or a local `.env` file).

| Variable | Default | Description |
| --- | --- | --- |
| `VARESENSE_DATABASE_URL` | `postgresql+asyncpg://localhost:5432/varesense` | Async SQLAlchemy connection URL |
| `VARESENSE_JWT_SECRET` | `dev-only-secret-change-me` | **Must be changed in production** |
| `VARESENSE_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `VARESENSE_ACCESS_TOKEN_TTL_MINUTES` | `30` | Access-token lifetime |
| `VARESENSE_REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh-token lifetime |
| `VARESENSE_ANOMALY_DETECT_INTERVAL_MINUTES` | `15` | Scheduled anomaly-detection interval |

> **Security:** use a strong, random `VARESENSE_JWT_SECRET` in any non-local environment. Long-lived refresh tokens should additionally be protected at the application/network layer.

---

## Authentication model

There are two credential flows:

1. **Interactive users** — JWT bearer tokens. Bootstrap the first org, create users, then log in. Access tokens are short-lived; refresh tokens rotate on every `/auth/refresh`.
   - Header: `Authorization: Bearer <access_token>`
2. **Devices** — ingest credentials (client ID + secret issued per edge node). Used by telemetry ingestion.
   - Header: `Authorization: Basic <base64(client_id:secret)>`

See the [API guide](docs/api-guide.md) for end-to-end flows and examples.

---

## Project structure

```
varesense-backend/
├── alembic/               # schema migrations
├── src/varesense/
│   ├── main.py            # app assembly, lifespan (scheduler)
│   ├── core/              # config, db, security, logging
│   ├── auth/ assets/ devices/ telemetry/ analytics/
│   ├── energy/ alerts/ reports/ maintenance/ realtime/ audit/
│   └── ...
├── tests/                 # pytest suite
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Documentation

- **[API guide](docs/api-guide.md)** — endpoint reference, auth flows, examples
- **OpenAPI** — live at `/docs` when the server runs
- **[GLOSSARY.md](../GLOSSARY.md)** — domain terminology
- **[ADR](../docs/adr/)** — architecture decision records

---

## Development conventions

- **Tests** — one runnable check per non-trivial change; the full suite must stay green.
- **Type safety** — `mypy` clean is enforced.
- **Migrations** — every schema change lands as a new Alembic revision; never edit applied revisions.
- **Commit** — one logical unit per commit.
