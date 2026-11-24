# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate docs/openapi.yml from the FastAPI app's OpenAPI schema.

Run with: uv run python scripts/generate_openapi.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "openapi.yml"


def main() -> None:
    """Write the app's current OpenAPI schema to docs/openapi.yml."""
    app = create_app()
    schema = app.openapi()
    OUTPUT_PATH.write_text(yaml.safe_dump(schema, sort_keys=False))
    print(f"Wrote OpenAPI schema to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
