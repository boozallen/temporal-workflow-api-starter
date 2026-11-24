# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Temporal Workflow API Starter Template

Template for creating FastAPI-based Temporal workflow API gateways.
Teams fork this template to build REST APIs for workflow management.

Based on foundry-temporal architecture specifications:
- FastAPI application with workflow endpoints
- Temporal client integration
- OpenAPI documentation
- Production-ready deployment configuration
"""


class APIStarterTemplate:
    """
    Template class for Temporal workflow API gateway

    Provides template structure and utilities for creating
    FastAPI-based workflow management APIs.
    """

    def __init__(self, project_name: str = "my-temporal-api") -> None:
        self.project_name = project_name
        self.template_version = "1.0.0"

    def get_template_files(self) -> dict[str, str]:
        """
        Get all template files for API starter

        Returns:
            Dict mapping file paths to template content
        """
        return {
            "README.md": self._get_readme_template(),
            "pyproject.toml": self._get_pyproject_template(),
            "examples/Dockerfile": self._get_dockerfile_template(),
            "examples/compose.yaml": self._get_compose_template(),
            "examples/env.template": self._get_env_template(),
        }

    def _get_readme_template(self) -> str:
        return f"""# {self.project_name}

FastAPI-based Temporal workflow API gateway based on foundry-temporal template.

## Quick Start

```bash
# Setup environment
cp examples/env.template .env
# Also copy shared configuration
cp ../../env.template .env.shared
# Edit .env with your settings

# Start with Docker Compose
docker compose up

# Or run locally
uvicorn src.main:app --reload --port 8000
```

## API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Template Structure

This template includes:
- FastAPI application with workflow endpoints
- Temporal client integration
- Request/response models
- Health checks and monitoring
- Docker deployment configuration
- Test suite setup
- Postman collections

## Next Steps

1. Customize workflow endpoints in `src/routers/workflows.py`
2. Add your data models in `src/models.py`
3. Update dependencies in `pyproject.toml`
4. Configure your deployment in `examples/Dockerfile` and `examples/compose.yaml`
"""

    def _get_pyproject_template(self) -> str:
        return f"""[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{self.project_name}"
version = "0.1.0"
description = "FastAPI-based Temporal workflow API gateway"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "temporalio>=1.11.0",
    "foundry-temporal>=1.0.0",
    "pydantic>=2.0.0",
    "python-multipart>=0.0.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.0.0",
    "httpx>=0.25.0",
    "ruff>=0.8.0",
    "mypy>=1.0.0",
]

[tool.mypy]
python_version = "3.11"
strict = true
"""

    def _get_dockerfile_template(self) -> str:
        return """FROM python:3.11-slim

# Create non-root user
RUN useradd --create-home --shell /bin/bash apiuser

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY pyproject.toml ./
RUN pip install -e .

# Copy application code
COPY src/ ./src/
COPY tests/ ./tests/

# Switch to non-root user
USER apiuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"

# Run FastAPI application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

    def _get_compose_template(self) -> str:
        return f"""services:
  {self.project_name}:
    build: .
    ports:
      - "8000:8000"
    environment:
      TEMPORAL_HOST: temporal:7233
      TEMPORAL_NAMESPACE: default
      API_PORT: 8000
    depends_on:
      temporal:
        condition: service_started
    networks:
      - temporal-network
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Local Temporal server for development
  temporal:
    image: temporalio/auto-setup:1.25.0
    ports:
      - "7233:7233"
      - "8080:8080"
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal
      - POSTGRES_SEEDS=postgresql
    depends_on:
      - postgresql
    networks:
      - temporal-network

  postgresql:
    image: postgres:15-alpine
    environment:
      POSTGRES_PASSWORD: temporal
      POSTGRES_USER: temporal
      POSTGRES_DB: temporal
    networks:
      - temporal-network

networks:
  temporal-network:
    driver: bridge
"""

    def _get_env_template(self) -> str:
        return """# API Configuration
API_PORT=8000
API_HOST=0.0.0.0

# Temporal Configuration
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default

# CORS Configuration
CORS_ORIGINS=["http://localhost:3000", "http://localhost:8000"]

# Optional: Authentication
# JWT_SECRET_KEY=your-secret-key-here
# JWT_ALGORITHM=HS256
# JWT_EXPIRY_HOURS=24

# Optional: TLS Configuration
# TEMPORAL_TLS_CERT=
# TEMPORAL_TLS_KEY=
# TEMPORAL_TLS_CA=
"""
