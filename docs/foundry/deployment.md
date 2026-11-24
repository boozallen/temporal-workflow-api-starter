# Deployment Guide

## Local Development

### Prerequisites
- Python 3.15
- Docker and Docker Compose
- Access to Temporal server

### Setup Steps

1. **Install Dependencies**
   ```bash
   uv sync --group dev
   ```

2. **Environment Configuration**
   ```bash
   cp env.template .env
   # Edit .env with your settings
   ```

3. **Start External Temporal Server**
   ```bash
   # Using the Temporal CLI's development server
   temporal server start-dev
   ```

4. **Start API Service**
   ```bash
   # Using justfile (recommended)
   just start

   # Or using Docker Compose directly
   docker compose up -d api

   # Or run directly with uv
   just dev
   ```

   Run directly with uv, the API connects to `TEMPORAL_HOST` from `.env`, `localhost:7233`
   from `env.template`, where `temporal server start-dev` listens. `compose.yaml` sets
   `host.docker.internal:7233` for the API container instead.

5. **Test API**

   macOS, Linux and WSL:
   ```bash
   # Test API connectivity
   curl http://localhost:8000/health
   curl http://localhost:8000/health/ready

   # Test workflow endpoints (will show "Available types: " until workflows are registered).
   # The request reads the token from your shell: export the API_AUTH_TOKEN you set in .env
   export API_AUTH_TOKEN=<your-api-auth-token>
   curl -X POST http://localhost:8000/workflows/start \
     -H "Authorization: Bearer $API_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"workflow_type": "YourWorkflowName", "input_data": {"test": true}}'
   ```

   Windows PowerShell:
   ```powershell
   # Test API connectivity
   curl.exe http://localhost:8000/health
   curl.exe http://localhost:8000/health/ready

   # Test workflow endpoints (will show "Available types: " until workflows are registered).
   # The request reads the token from your shell: set the API_AUTH_TOKEN you set in .env
   $env:API_AUTH_TOKEN = "<your-api-auth-token>"
   $body = @{ workflow_type = "YourWorkflowName"; input_data = @{ test = $true } } | ConvertTo-Json
   Invoke-RestMethod -Method Post -Uri http://localhost:8000/workflows/start -Headers @{ Authorization = "Bearer $env:API_AUTH_TOKEN" } -ContentType "application/json" -Body $body
   ```

## **Workflow Execution Requirements**

This template provides **API gateway only**. To actually execute workflows:

1. **Register workflows** in [`src/routers/workflows.py`](../../src/routers/workflows.py) `get_available_workflows()`
2. **Deploy separate worker services** using the [`temporal-workflow-worker-starter`](https://github.com/boozallen/temporal-workflow-worker-starter) template
3. **Configure worker services** to poll the same task queue as the API

## Docker Deployment

### Build Image
```bash
docker build -t my-workflow-api:latest .
```

### Run Container
```bash
docker run -d \
  --name workflow-api \
  -p 8000:8000 \
  -e TEMPORAL_HOST=your-temporal-server:7233 \
  -e TEMPORAL_NAMESPACE=your-namespace \
  my-workflow-api:latest
```

## CI/CD Integration

GitHub runs CodeQL code scanning on this repository. Lint, type checks, tests and the coverage gate run locally: `just check` must pass before you open a PR.

### Available Just Commands

The template includes a comprehensive [`justfile`](../../justfile) for development operations:

```bash
# Setup & Dependencies
just sync         # Sync the virtual environment with the lockfile (dev + test groups)
just setup        # sync + local env prep (.env from env.template, uv lock)

# Code Quality
just lint         # Ruff lint + the bandit command-injection sink set
just lint-fix     # Auto-fix ruff lint findings
just format       # Check code formatting (does not modify files)
just format-fix   # Auto-format code
just type-check   # basedpyright type checking
just check        # Every quality gate: lint, format, type-check, test

# Testing
just test              # Whole tree: tests/ (unit + integration)
just test-unit         # tests/unit/ only
just test-integration  # tests/integration/ only (no coverage gate)

# Local Runtime
just dev          # Run the API locally with hot reload, no Docker (alias: just run)
just up           # Start the API container in the background (alias: just start)
just down         # Stop the API container (alias: just stop)
just logs         # Follow logs (optionally: just logs <service>)
just status       # Container status + health check
just build        # Build the API Docker image
just reset        # Stop containers, clean artifacts, re-run setup

# Maintenance
just clean        # Remove build artifacts and tooling caches
just clean-all    # clean + this project's containers and volumes
```

The `sync` / `setup` split is a convention shared across the Foundry repos: `sync` installs
dependencies only, `setup` layers any repo-specific environment preparation on top of
`sync`. `just setup` therefore still installs dependencies as before.

The unit/integration split is by directory: [`tests/unit/`](../../tests/unit/) and [`tests/integration/`](../../tests/integration/). No
recipe selects tests by marker; the tier a test belongs to is decided by where the file
lives.

Coverage is gated globally via `[tool.pytest.ini_options] addopts` in [`pyproject.toml`](../../pyproject.toml), so
it applies to every test recipe, and the same config writes the HTML coverage report to
`htmlcov/` on every run. There is deliberately no separate coverage recipe.
`test-integration` passes `--no-cov --cov-fail-under=0` because that narrow slice cannot
represent overall project coverage on its own, and instrumenting it would overwrite
`htmlcov/` with integration-only data. It also treats pytest's "no tests collected" exit
code 5 as success.

`just clean` removes `build/`, `dist/`, `htmlcov/`, `*.egg-info/`, `.pytest_cache/`,
`.ruff_cache/`, `.coverage`, `__pycache__/`, and `*.pyc`, then prints
`Cleaned build artifacts`. It skips `.venv/` so a clean does not invalidate the
environment. A stale `.coverage` can make a later coverage run fail spuriously, so it is
always removed. Container recipes use one naming set across the Foundry single-package
repos: `up`/`down` with `start`/`stop` as aliases, `logs [service]`, `status`, `build`,
`reset` (down + clean + setup), and `clean-all` (clean + `docker compose down -v
--remove-orphans`). `clean-all` is scoped to this project's compose file: it removes the
containers and volumes this repo declares and does not prune the Docker host, so it cannot
disturb unrelated services. The local, non-Docker run recipe is `just dev`, with `just run`
as an alias.

## Environment Variables

Required configuration:

```bash
# Temporal Configuration
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=workflow-task-queue
TEMPORAL_TLS_MODE=platform   # platform, tls, mtls, or off (see Temporal TLS below)
# TEMPORAL_TLS_CERT=/certs/client.pem  # Client cert. Required for mtls. Optional for tls.
# TEMPORAL_TLS_KEY=/certs/client.key   # Client key. Set together with the cert.
# TEMPORAL_TLS_CA=/certs/ca.pem        # Private CA. Optional. Set only if you have one.

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_AUTH_TOKEN=              # Bearer token for /workflows/* endpoints (fail-closed when unset)
TRUSTED_PROXY_HOSTS=127.0.0.1  # Proxies allowed to set X-Forwarded-For (see env.template)
# MAX_REQUEST_BODY_BYTES=8388608 # Optional override (example: 8 MiB); unset uses foundry-agent-core's dict byte cap
LOG_LEVEL=INFO

# Timeouts
TEMPORAL_CONNECT_TIMEOUT_SECONDS=30   # Time limit for one connect to Temporal; a slower connect fails and the next request starts a fresh one
# TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT=3 # Optional: readiness probe timeouts in a row before the cached Temporal client is discarded and reconnected
AGENT_TIMEOUT_SECONDS=60    # Timeout for agent calls
# TEMPORAL_PROBE_TIMEOUT_SECONDS=0.9 # Optional: per-health-check Temporal timeout (connection wait plus probe query); keep below the readiness probe's timeoutSeconds

# Environment
ENVIRONMENT=development     # development, staging, or production
```

## Health Checks

Configure health checks in your orchestration platform:

- **Readiness**: `GET /health/ready`
- **Liveness**: `GET /health`

Outside mock mode, `/health/ready` returns `503` whenever Temporal is
unreachable: the client cannot connect, the client is not ready or the
connectivity probe has not answered within `TEMPORAL_PROBE_TIMEOUT_SECONDS`
(default 0.9), or the probe fails with any
status that does not show Temporal handled the request, such as `UNAVAILABLE`,
`UNAUTHENTICATED`, `DEADLINE_EXCEEDED` or `UNIMPLEMENTED`. A missing namespace
(`NOT_FOUND`) or a namespace that is not active on this cluster
(`FAILED_PRECONDITION`) also reports not ready, because every workflow route
would fail, so a misconfigured `TEMPORAL_NAMESPACE` stops a rollout. A throttled probe
(`RESOURCE_EXHAUSTED`) also reports not ready, so set the probe's
`failureThreshold` for how much Temporal throttling your environment should
tolerate. The timeout covers the whole check: waiting for the connection and
the probe query together. A first connection that takes longer keeps going in
the background, so a later check succeeds once it completes. This means the
first readiness check after a slow cold start can fail once while Temporal is
healthy, and the next check passes with the cached client. With
`failureThreshold: 1`, that one failure marks the pod not ready until the next
check, so use a threshold of at least 2 if that matters. When the readiness
probe times out on the same cached client
`TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT` times in a row (default 3), the
client is discarded and the next check connects again, so a connection that
has gone silent does not keep the pod not ready until it restarts. Timeouts on
`/health/detailed` and `/health/temporal` are logged but not counted. A
successful probe on any health endpoint resets the count.
During a Temporal outage every replica
reports not ready at the same time, so the platform stops routing all traffic
to the API, not only Temporal-backed routes. Set the probe's failure threshold
and period for how long a Temporal interruption your environment should
tolerate before removing replicas. When Temporal is reachable but rejects the
probe's workflow visibility query with `PERMISSION_DENIED` or `INVALID_ARGUMENT`, for example because
the API's credential lacks list permission, `/health/ready` stays `200` with
`dependencies.temporal: "degraded"` and the service logs a warning. A
`degraded` response still reports top-level `status: "healthy"`, so alert on
`dependencies.temporal`, not `status`. `/health` does not check Temporal,
so liveness never restarts a replica because of a Temporal outage.

## Kubernetes Deployment

### Deployment Contract

This template is deployment-agnostic: it does not ship a Helm chart, and none
is added to this repo. Deploy the built image with your own platform's chart
or manifests. Whatever you use needs to satisfy this contract:

- **Container port**: `8000`. The image `EXPOSE`s `8000`, and the `CMD`
  starts uvicorn with `--port 8000`. Setting `API_PORT` does not change the
  port the container listens on.
- **Environment variables**:
  - `API_AUTH_TOKEN` - bearer token for `/workflows/*`. The process starts
    without it, and those routes then respond `503` until it is set. A value
    shorter than 32 characters stops startup.
  - `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE` - the image
    sets `TEMPORAL_HOST=temporal-server:7233` and `TEMPORAL_NAMESPACE=default`.
    `TEMPORAL_TASK_QUEUE` is unset in the image and defaults to
    `workflow-task-queue`. Set all three to the Temporal server, namespace,
    and task queue this deployment should use.
  - `TEMPORAL_TLS_MODE` and the `TEMPORAL_TLS_*` paths. See
    [Temporal TLS](#temporal-tls). Startup stops under `mtls` without a cert
    and key, under `tls` with a cert or key but not both, and under either
    mode when a path that is set does not point to a readable file.
  - See [Environment Variables](#environment-variables) above for the full
    list, including optional ones
- **Health check paths**:
  - **Liveness**: `GET /health`
  - **Readiness**: `GET /health/ready`
- **Non-root**: the production image runs as `apiuser` (uid/gid `777`), not
  root - see the `production` stage in the [`Dockerfile`](../../Dockerfile)
- **Graceful shutdown**: the container's `CMD` runs `uvicorn` directly as
  PID 1 (no process supervisor in between), so a `SIGTERM` from the
  orchestrator reaches `uvicorn` directly. `uvicorn` stops accepting new
  connections, drains in-flight requests, and runs the FastAPI `lifespan`
  shutdown logging ([`src/main.py`](../../src/main.py)) before exiting. Give the pod a
  `terminationGracePeriodSeconds` long enough for in-flight requests to
  finish.

### Direct Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: workflow-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: workflow-api
  template:
    metadata:
      labels:
        app: workflow-api
    spec:
      containers:
      - name: api
        image: my-workflow-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: TEMPORAL_HOST
          value: "temporal-server:7233"
        - name: TEMPORAL_NAMESPACE
          value: "default"
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: workflow-api-service
spec:
  selector:
    app: workflow-api
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

### Temporal TLS

`TEMPORAL_TLS_MODE` sets how the API connects to the Temporal frontend.

- `platform` is the default. The platform or service mesh terminates TLS. The `TEMPORAL_TLS_*` paths are ignored and not checked, so they can be shared with other components. The API logs a warning at startup when any of them is set, in case `TEMPORAL_TLS_MODE` was left out by mistake.
- `tls` uses one-way TLS. The server is verified against system root CAs, or against `TEMPORAL_TLS_CA` when it is set.
- `mtls` uses mutual TLS. It needs `TEMPORAL_TLS_CERT` and `TEMPORAL_TLS_KEY`. `TEMPORAL_TLS_CA` is optional.
- `off` means no TLS at all, for a local Temporal server. It behaves like `platform`: the connection is plaintext, the `TEMPORAL_TLS_*` paths are ignored, and the API logs the same startup warning when any of them is set. Use it only for local development: the API refuses to start with `off` when `ENVIRONMENT=production`.

Under `tls`, the cert and key are optional, but must be set together.

Each path that is set must point to a readable PEM file inside the container. Blank values are treated as unset. Under `tls` or `mtls` the API checks that each file exists and is readable at startup, and stops if one is not. It does not check the file contents. A malformed or mismatched PEM file passes startup, then fails every connection attempt. The pod keeps running, and every request that needs Temporal fails with `503` until the file is fixed.

Mount the client certificate from a Kubernetes TLS secret, and point the variables at the mounted files. Create the secret like this.

```bash
kubectl create secret tls temporal-client-tls \
  --cert=client.pem --key=client.key
```

```yaml
        env:
        - name: TEMPORAL_TLS_MODE
          value: "mtls"
        - name: TEMPORAL_TLS_CERT
          value: "/certs/tls.crt"
        - name: TEMPORAL_TLS_KEY
          value: "/certs/tls.key"
        volumeMounts:
        - name: temporal-tls
          mountPath: /certs
          readOnly: true
      volumes:
      - name: temporal-tls
        secret:
          secretName: temporal-client-tls
```

A TLS secret holds only `tls.crt` and `tls.key`. If the Temporal server uses a private CA, mount the CA from a separate secret or config map, and set `TEMPORAL_TLS_CA` to that file. Do not set `TEMPORAL_TLS_CA` to a file that is not mounted, because startup will stop.

The container runs as user `apiuser` (uid `777`), not root. The mounted files must be readable by that user. The default secret mode `0644` works. If you set `defaultMode: 0400`, also set `fsGroup: 777` in the pod `securityContext` and use `0440`, or startup stops with "file is missing or unreadable".

#### Certificate rotation

Restart the pods after you rotate the certificates. Without a restart, the API keeps using the old certificate for an unpredictable time.

TLS checks a certificate only when a connection opens. An open connection keeps working after the old certificate expires. When the connection drops, the Temporal client reconnects with the certificate it read before. Only after that reconnect fails does the API read the new files.

Keep the files mounted for the life of the pod. The startup check runs only once. If a file is missing when the API reconnects, every request fails with `503` until the file is back.

## Production Considerations

1. **Security**
   - Implement authentication/authorization
   - Use HTTPS with proper TLS certificates
   - Network security (VPC, security groups)
   - Run the Temporal Server this API points at on at least 1.28.4 on the
     1.28.x line, 1.29.6 on the 1.29.x line, or 1.30.4 on the 1.30.x line.
     These versions fix an AdminService authorization bypass and a
     cross-namespace workflow tampering issue. Namespace-per-tenant
     isolation on a shared cluster depends on both fixes.

2. **Scalability**
   - Horizontal Pod Autoscaling (HPA)
   - Load balancing
   - Connection pooling

3. **Monitoring**
   - Application metrics (Prometheus)
   - Log aggregation (ELK stack)
   - Health monitoring and alerts

4. **High Availability**
   - Multi-region deployment
   - Database failover
   - Circuit breakers

## Troubleshooting

### Common Issues

**API won't start:**
- Check Temporal server connectivity
- Verify environment variables
- Check logs for specific errors

**Workflows not starting:**
- Verify task queue name matches worker
- Check worker is running and registered
- Validate workflow input data

**Health checks failing:**
- Ensure Temporal server is accessible
- Check network connectivity
- Verify namespace exists and is accessible