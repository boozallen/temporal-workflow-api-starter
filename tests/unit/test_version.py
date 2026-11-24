# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for version reporting
"""

import tomllib
from pathlib import Path

from src.version import APP_VERSION


def test_app_version_matches_pyproject() -> None:
    """APP_VERSION must stay in sync with pyproject.toml's declared version."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    assert pyproject["project"]["version"] == APP_VERSION
