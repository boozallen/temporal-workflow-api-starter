---
sidebar_position: 2
---

# Quickstart: Temporal Workflow API Starter

Get up and running with the Temporal Workflow API Starter in minutes.

## Prerequisites

- Python 3.15 (`requires-python = ">=3.15,<3.16"` in [`pyproject.toml`](../../pyproject.toml))
- `uv` package manager
- `just` command runner
- Docker and Docker Compose
- A reachable Temporal server (`temporal server start-dev` for local development)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/boozallen/temporal-workflow-api-starter.git
   cd temporal-workflow-api-starter
   ```

2. Install dependencies and create `.env` from [`env.template`](../../env.template):
   ```bash
   just setup
   ```

   This runs `uv sync --group dev --group test`, copies `env.template` to `.env` if it
   does not already exist, and locks the dependency set with `uv lock`.

3. Review and customize `.env`, in particular `API_AUTH_TOKEN`:
   ```bash
   uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   The API is fail-closed: it rejects every `/workflows/*` request while
   `API_AUTH_TOKEN` is unset.

## Running Locally

Start an external Temporal server, then run the API with autoreload:

```bash
temporal server start-dev
just dev
```

`just dev` runs `uv run uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload`.
`just dev` connects to `TEMPORAL_HOST` from `.env`, `localhost:7233` from `env.template`,
where `temporal server start-dev` listens. `compose.yaml` sets `host.docker.internal:7233`
for the API container instead.

## Verify It Works

Visit `http://localhost:8000/docs` for the interactive Swagger UI, or check the health
endpoint directly:

macOS, Linux and WSL:
```bash
curl http://localhost:8000/health
```

Windows PowerShell:
```powershell
curl.exe http://localhost:8000/health
```

To start a test workflow, include the bearer token from your `.env`:

macOS, Linux and WSL:
```bash
curl -X POST http://localhost:8000/workflows/start \
  -H "Authorization: Bearer <your-api-auth-token>" \
  -H "Content-Type: application/json" \
  -d '{"workflow_type": "MyWorkflow", "input_data": {"message": "Hello World"}}'
```

Windows PowerShell:
```powershell
$body = @{ workflow_type = "MyWorkflow"; input_data = @{ message = "Hello World" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/workflows/start -Headers @{ Authorization = "Bearer <your-api-auth-token>" } -ContentType "application/json" -Body $body
```

## Next Steps

- Return to the [Temporal Workflow API Starter](./index.md) overview for architecture details
- See the [API Endpoints Reference](./endpoints.md) for every endpoint
