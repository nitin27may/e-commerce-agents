"""
Root conftest for all tutorial chapter tests.

Registers custom markers used across chapters so pytest doesn't warn about them.
"""

import sys

# Every chapter's tests/test_*.py inserts its own `<chapter>/python` directory
# at the front of sys.path and then does `from main import ...` (some chapters
# also import a same-named helper, e.g. chapter 08's `weather_mcp_server`).
# That's correct when each chapter runs in its own process, but collecting the
# whole `tutorials/` tree in one pytest session means the *second* chapter
# collected would otherwise reuse the *first* chapter's cached `main` module
# from `sys.modules`, since module names — not paths — are the cache key.
# Evict these transient, chapter-local module names before each test module is
# collected so every chapter re-imports its own `main.py` off its own
# sys.path entry.
_TRANSIENT_MODULES = ("main", "weather_mcp_server")


def pytest_configure(config):  # noqa: ANN001 - pytest hook signature
    config.addinivalue_line(
        "markers",
        "integration: test hits a real LLM; skipped when credentials are missing",
    )


def pytest_collectstart(collector):  # noqa: ANN001 - pytest hook signature
    for name in _TRANSIENT_MODULES:
        sys.modules.pop(name, None)
