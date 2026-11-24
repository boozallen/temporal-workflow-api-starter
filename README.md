# Temporal Workflow API Starter

**Template for building FastAPI gateways for Temporal workflow management**

This starter template provides a REST API gateway that communicates with Temporal server for workflow management operations. **This template does NOT include workflow implementations** - use the [`temporal-workflow-worker-starter`](https://github.com/boozallen/temporal-workflow-worker-starter) template for worker services.

> **End-to-End Guides** — For a complete walkthrough of setting up and running the full Temporal workflow stack (API + Worker + Agent), see:
> - [Getting Started](https://github.com/boozallen/temporal-workflow-worker-starter/blob/HEAD/docs/foundry/guides/end-to-end-how-to-getting-started.md) — Full stack setup from scratch
> - [Next Steps](https://github.com/boozallen/temporal-workflow-worker-starter/blob/HEAD/docs/foundry/guides/end-to-end-how-to-next-steps.md) — Swap mock agent for real [Strands Base Agent](https://github.com/boozallen/strands-base-agent/blob/HEAD/docs/foundry/index.md)
> - [Advanced](https://github.com/boozallen/temporal-workflow-worker-starter/blob/HEAD/docs/foundry/guides/end-to-end-how-to-advanced.md) — Building complex workflows with loops

## Quick Start (5 minutes)

```bash
# Clone this template
git clone <your-repo-url> my-workflow-api
cd my-workflow-api
```

Rename the project in [`pyproject.toml`](pyproject.toml) **before running `just setup`**:
```toml
[project]
name = "my-workflow-api"
description = "Description of your workflow API"
```
Already ran `just setup`? Delete `*.egg-info` in the repo root and run
`uv sync` again.

```bash
# Install dependencies and create .env from the template
just setup

# Start external Temporal server
temporal server start-dev

# Start API service
just dev
```

Visit `http://localhost:8000/docs` for interactive API documentation.

**Note**: This provides API endpoints only. Template includes workflow names from `temporal-workflow-worker-starter` for testing. To execute workflows, deploy separate worker services using that template.

## What You Get

- **FastAPI** application with async/await support
- **Temporal workflow management** endpoints (start, status, signal)
- **OpenAPI documentation** with Swagger UI and ReDoc
- **Health checks** for API and Temporal connectivity
- **Docker configuration** with complete development stack
- **Postman collections** for API testing
- **Testing framework** with unit and integration tests

## Dependencies

- **fastapi** - Modern, fast web framework for building APIs
- **temporalio** - Official Temporal Python SDK
- **[foundry-temporal](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal/index.md)** - Core workflow utilities, from the [temporal-packages](https://github.com/boozallen/temporal-packages) repository
- **uvicorn** - Lightning-fast ASGI server

## Customization

Update Project Configuration (renaming `pyproject.toml`) is covered in
[Quick Start](#quick-start-5-minutes) above - do it before the first `uv sync`.

### 1. Customize Workflow Registration

Edit [`src/routers/workflows.py`](src/routers/workflows.py) to register your workflow types:
```python
def get_available_workflows():
    workflow_types = {
        "MyWorkflowType",
        "AnotherWorkflow",
    }
    return {name: name for name in workflow_types}
```

Template includes `MyWorkflow` from `temporal-workflow-worker-starter` for testing.

### 2. Update Data Models

Edit [`src/models.py`](src/models.py) for your request/response schemas:
```python
class MyWorkflowRequest(BaseModel):
    # Define your workflow input schema
    data: Dict[str, Any]
    priority: str = "normal"
```

### 3. Configure Environment

Update `.env` with your settings:
```env
API_PORT=8000
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TLS_MODE=platform
API_AUTH_TOKEN=your-secret-token
```

For `mtls`, also set `TEMPORAL_TLS_CERT` and `TEMPORAL_TLS_KEY`. Set `TEMPORAL_TLS_CA` only when the Temporal server uses a private CA. See [Temporal TLS](docs/foundry/deployment.md#temporal-tls).

## API Endpoints

### Workflow Management
- `POST /workflows/start` - Start a new workflow
- `GET /workflows/{workflow_id}/status` - Get workflow status
- `GET /workflows/` - List workflows with filtering
- `GET /workflows/{workflow_id}/query` - Query workflow state
- `POST /workflows/{workflow_id}/approve` - Approve workflow (HITL step)
- `POST /workflows/{workflow_id}/signal` - Send signal to workflow

### Health & Monitoring
- `GET /health` - Basic API health check
- `GET /health/detailed` - Detailed health check with dependencies
- `GET /health/temporal` - Temporal connectivity check
- `GET /health/ready` - Readiness probe for Kubernetes

### Documentation
- `GET /docs` - Swagger UI documentation
- `GET /redoc` - ReDoc documentation
- `GET /openapi.json` - OpenAPI specification

## Testing

Tests are split by directory: [`tests/unit/`](tests/unit/) and [`tests/integration/`](tests/integration/). No recipe selects
tests by marker.

### Run the Full Test Suite
```bash
just test          # tests/ - unit + integration
```

### Run Unit Tests
```bash
just test-unit     # tests/unit/ only
# or: uv run pytest tests/unit/
```

### Run Integration Tests
```bash
just test-integration   # tests/integration/ only
# or: uv run pytest tests/integration/ --no-cov --cov-fail-under=0
```

Coverage instrumentation, the terminal report, the HTML report in `htmlcov/`, and the
coverage gate all come from the `pyproject.toml` pytest addopts, so they apply to every
test recipe and there is no separate coverage recipe. `test-integration` disables coverage
for that isolated slice so it cannot overwrite the full-suite HTML report. Run
`just check` (lint, format, type-check, test) before opening a PR.

### Test API Endpoints
macOS, Linux and WSL:
```bash
# Start the API
just dev

# Test endpoints (the start request reads the API_AUTH_TOKEN you set in .env from your shell)
export API_AUTH_TOKEN=<your-api-auth-token>
curl http://localhost:8000/health
curl -X POST http://localhost:8000/workflows/start \
  -H "Authorization: Bearer $API_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workflow_type": "MyWorkflow", "input_data": {"message": "Hello World"}}'
```

Windows PowerShell:
```powershell
# Start the API
just dev

# Test endpoints (the start request reads the API_AUTH_TOKEN you set in .env from your shell)
$env:API_AUTH_TOKEN = "<your-api-auth-token>"
curl.exe http://localhost:8000/health
$body = @{ workflow_type = "MyWorkflow"; input_data = @{ message = "Hello World" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/workflows/start -Headers @{ Authorization = "Bearer $env:API_AUTH_TOKEN" } -ContentType "application/json" -Body $body
```

### Use Postman Collections
1. Import [`postman/API.postman_collection.json`](postman/API.postman_collection.json)
2. Import [`postman/Local.postman_environment.json`](postman/Local.postman_environment.json)
3. Test all endpoints with pre-configured requests

## Deployment

### Local Development
```bash
just up
```

The container reads `API_AUTH_TOKEN` (and any other overrides) from `.env`
via `compose.yaml`'s `env_file`, the same file `just setup` created from
`env.template`. Set a real token in `.env` before running `just up` - with
no token configured, `/workflows/*` fails closed on every request (see
[Authentication](#authentication) below).

### Production Deployment
This template is deployment-agnostic and does not ship a Helm chart. Build the
Docker image and deploy it with your own platform's chart or manifests. See the
[deployment contract](docs/foundry/deployment.md#deployment-contract)
for what a chart author needs: container port, required
environment variables, health check paths, non-root user, and graceful
shutdown behavior.

## Project Structure

```
temporal-workflow-api-starter/
├── README.md                    # This file
├── pyproject.toml              # Dependencies and project config
├── Dockerfile                  # API container image
├── compose.yaml                # Local development stack
├── justfile                    # Development commands
│
├── src/
│   ├── __init__.py
│   ├── main.py               # FastAPI application
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── workflows.py      # CUSTOMIZE: Workflow endpoints
│   │   └── health.py         # Health check endpoints
│   ├── models.py             # CUSTOMIZE: Request/response models
│   ├── dependencies.py       # Temporal client dependency
│   └── config.py             # Configuration management
│
├── tests/
│   ├── unit/
│   │   ├── test_routers.py   # Unit tests for API routes
│   │   └── test_auth.py      # Bearer-token auth tests
│   └── integration/
│       └── test_api_workflow_integration.py
│
├── postman/
│   ├── API.postman_collection.json      # Complete API test collection
│   └── Local.postman_environment.json   # Local environment variables
│
├── docs/foundry/
│   ├── index.md              # Overview
│   ├── quickstart.md         # Get up and running
│   ├── api-design.md         # API design decisions
│   ├── endpoints.md          # Detailed endpoint documentation
│   └── deployment.md         # Deployment guide
│
└── .github/workflows/
    └── release.yml           # Release workflow
```

## Key Features

### Async FastAPI
Built for high-performance async operations:
```python
@router.post("/workflows/start")
async def start_workflow(
    request: WorkflowStartRequest,
    client: Client = Depends(get_temporal_client),
):
    handle = await client.start_workflow(...)
    return {"workflow_id": handle.id}
```

### OpenAPI Documentation
Automatic API documentation with Pydantic models:
```python
class WorkflowRequest(BaseModel):
    message: str = Field(..., description="Input message")
    metadata: Optional[Dict] = Field(None, description="Metadata")
```

### Health Checks
Health endpoints for the API and its Temporal connection:
```python
@router.get("/health/temporal")
async def temporal_health():
    # Verify Temporal connectivity
    await client.list_workflows("WorkflowId = 'health-check'")
    return {"status": "healthy", "service": "temporal"}
```

### Error Handling
Comprehensive error handling and validation:
```python
try:
    result = await client.execute_workflow(...)
except WorkflowExecutionError as e:
    raise HTTPException(status_code=500, detail=str(e))
```

## Documentation

- [API Design Guide](docs/foundry/api-design.md) - REST API design principles
- [API Endpoints Reference](docs/foundry/endpoints.md) - Detailed API reference
- [Deployment Guide](docs/foundry/deployment.md) - Production deployment
- [foundry-temporal](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal/index.md) - Core package docs

## Security

### Authentication
Bearer token authentication is built in for `/workflows/*` endpoints. Set `API_AUTH_TOKEN` in your environment — the API is **fail-closed** (rejects all requests when unset):

```bash
# Generate a token
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

# Set in .env
API_AUTH_TOKEN=your-generated-token
```

Requests must include the `Authorization: Bearer <token>` header.

### Request Size Limits
Every request field, path parameter, and query parameter is bounded (a length limit, a numeric range, or a fixed set of allowed values); a value over its limit returns `422`. Request bodies larger than `MAX_REQUEST_BODY_BYTES` (default 4 MiB, [foundry-agent-core](https://github.com/boozallen/foundry-agent-packages/blob/HEAD/docs/foundry/packages/foundry-agent-core/index.md)'s shared dict byte cap) are rejected with `413` as soon as they pass the limit, before the app processes them. The limits are placeholders, tuned in three different places: field lengths are constants in `src/models.py` (edit them when you adopt the template), the body limit is a deployment setting (`MAX_REQUEST_BODY_BYTES`), and the `input_data` caps come from `foundry-agent-core`.

### CORS
Add CORS middleware in [`src/main.py`](src/main.py) as needed:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Contributing

This is a template repository - fork it and make it your own!

For issues with the foundry-temporal package, please file issues in the [temporal-packages](https://github.com/boozallen/temporal-packages) repository.

## License

Apache-2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).