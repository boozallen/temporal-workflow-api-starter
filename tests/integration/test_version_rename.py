# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Integration test for the documented project-rename customization step.

Guards against src/version.py regressing to a hard-coded distribution
name: renaming [project].name in pyproject.toml and re-syncing must not
break version reporting.
"""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UV = shutil.which("uv")


@pytest.mark.integration
@pytest.mark.slow
def test_version_survives_project_rename(tmp_path: Path) -> None:
    """After renaming the project and running `uv sync`, the app must still
    report its version instead of raising PackageNotFoundError."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    expected_version = pyproject["project"]["version"]
    original_name = pyproject["project"]["name"]

    scratch = tmp_path / "renamed-project"
    shutil.copytree(
        REPO_ROOT,
        scratch,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "openspec",
            "__pycache__",
            ".pytest_cache",
            "htmlcov",
            # Build artifacts: a stray *.egg-info left in the real repo root
            # from a prior local build would otherwise get copied in and
            # let importlib.metadata resolve the OLD distribution name from
            # the current working directory, masking the exact regression
            # this test exists to catch.
            "*.egg-info",
            "build",
            "dist",
            ".eggs",
        ),
    )

    pyproject_path = scratch / "pyproject.toml"
    original_text = pyproject_path.read_text()
    renamed_text = original_text.replace(
        f'name = "{original_name}"', 'name = "renamed-project"', 1
    )
    assert renamed_text != original_text, (
        f'pyproject.toml\'s name = "{original_name}" line was not found to rename'
    )
    pyproject_path.write_text(renamed_text)

    assert UV, "uv must be on PATH to run this test"
    # The copy gets its own .venv: an inherited UV_PROJECT_ENVIRONMENT or
    # VIRTUAL_ENV would make `uv sync` install the renamed project into the
    # caller's environment, where it then claims the `src` package too.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
    }
    sync = subprocess.run(
        [UV, "sync"], cwd=scratch, env=env, capture_output=True, text=True
    )
    # uv's own message says why a sync failed (a locked cache file, the index).
    assert sync.returncode == 0, (
        f"uv sync failed (exit {sync.returncode}):\n{sync.stdout}\n{sync.stderr}"
    )
    result = subprocess.run(
        [
            UV,
            "run",
            "python",
            "-c",
            "from src.version import APP_VERSION; print(APP_VERSION)",
        ],
        cwd=scratch,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == expected_version
