"""
pytest configuration for the test suite.

Provides a session-scoped skip guard for regression tests that require the
NIFTY CSV data file to be present.  On a fresh clone without data the tests
are skipped with an explicit message rather than crashing with a
FileNotFoundError buried in application code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# The data file referenced by tests/fixtures/regression_config.yaml.
_REGRESSION_DATA = (
    Path(__file__).parent / "fixtures" / "regression_config.yaml"
)


def _regression_data_path() -> Path:
    """Return the resolved CSV path from the regression fixture config."""
    import yaml
    with _REGRESSION_DATA.open() as fh:
        cfg = yaml.safe_load(fh)
    raw = Path(cfg["data"]["file"])
    if raw.is_absolute():
        return raw
    return (_REGRESSION_DATA.parent / raw).resolve()


def pytest_collection_modifyitems(config, items):
    """Skip all tests in test_regression.py when the data file is missing."""
    data_file = _regression_data_path()
    if data_file.exists():
        return  # Nothing to do — data is present.

    skip_marker = pytest.mark.skip(
        reason=(
            f"Regression data file not found: {data_file}\n"
            "Fetch it with: python fetch_data.py --symbol NIFTY --tf 5 "
            "--from 2025-06-13 --to 2026-06-13"
        )
    )
    for item in items:
        if item.fspath.basename == "test_regression.py":
            item.add_marker(skip_marker)
