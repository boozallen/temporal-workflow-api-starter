---
sidebar_position: 1
---

# Temporal Workflow API Starter

Template for building FastAPI gateways for Temporal workflow management.

## Overview

`temporal-workflow-api-starter` is a fork-and-customize template for a FastAPI service that
manages Temporal workflow executions over REST. It does not contain workflow or activity
code — it starts, queries, signals, and approves workflows that run in a separate worker
service, such as one built from [`temporal-workflow-worker-starter`](https://github.com/boozallen/temporal-workflow-worker-starter). It composes `temporalio`
and [`foundry-temporal`](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal/index.md) to talk to a Temporal server and exposes that as a documented,
authenticated HTTP API.

## Key Features

- **Workflow lifecycle endpoints** — start, check status, list, query, signal, and approve
  (human-in-the-loop) a Temporal workflow via [`src/routers/workflows.py`](../../src/routers/workflows.py)
- **Fail-closed bearer authentication** — `/workflows/*` endpoints require
  `API_AUTH_TOKEN`; the API rejects every workflow request when it is unset
- **Kubernetes-ready health checks** — `/health`, `/health/detailed`, `/health/temporal`,
  and `/health/ready` in [`src/routers/health.py`](../../src/routers/health.py)
- **Automatic OpenAPI documentation** — Swagger UI at `/docs` and ReDoc at `/redoc`,
  generated from the Pydantic models in [`src/models.py`](../../src/models.py)
- **Dual-tier tests and Postman collections** — [`tests/unit/`](../../tests/unit/) and [`tests/integration/`](../../tests/integration/),
  plus a ready-to-import collection under [`postman/`](../../postman/)

## Architecture

The API is a thin FastAPI gateway ([`src/main.py`](../../src/main.py)) in front of a Temporal server. Requests
land on one of two routers — `workflows.py` for workflow operations and `health.py` for
liveness/readiness — and [`src/dependencies.py`](../../src/dependencies.py) provides the authenticated Temporal client,
built with `foundry-temporal`'s `create_temporal_client`. Configuration is loaded from
environment variables through `pydantic-settings` in [`src/config.py`](../../src/config.py). The template ships
with `MyWorkflow` references from `temporal-workflow-worker-starter` for testing, but
running an actual workflow requires deploying a separate worker service.

## Next Steps

- [Quickstart](./quickstart.md) — get up and running
- [API Design Guide](./api-design.md) — REST API design principles
- [API Endpoints Reference](./endpoints.md) — detailed API reference
- [Deployment Guide](./deployment.md) — production deployment
