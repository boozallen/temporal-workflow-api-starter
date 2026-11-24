# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Single source of truth for the API's reported version."""

from importlib.metadata import packages_distributions
from importlib.metadata import version as _pkg_version

_TOP_LEVEL_PACKAGE = __name__.partition(".")[0]
_distributions = packages_distributions().get(_TOP_LEVEL_PACKAGE)
if not _distributions:
    raise RuntimeError(
        f"No installed distribution provides the '{_TOP_LEVEL_PACKAGE}' "
        "package; is the project installed (`uv sync` without "
        "--no-install-project)?"
    )

# packages_distributions() can list the same distribution more than once
# (e.g. once per RECORD entry it was inferred from); dedupe before checking
# for genuine ambiguity between two *different* distributions.
_unique_distributions = sorted(set(_distributions))
if len(_unique_distributions) > 1:
    raise RuntimeError(
        f"Multiple distinct distributions ({', '.join(_unique_distributions)}) "
        f"claim to provide the '{_TOP_LEVEL_PACKAGE}' package; cannot "
        "determine this project's version unambiguously."
    )

APP_VERSION = _pkg_version(_unique_distributions[0])
