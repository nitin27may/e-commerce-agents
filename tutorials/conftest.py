"""
Root conftest for all tutorial chapter tests.

Registers custom markers used across chapters so pytest doesn't warn about them.
"""


def pytest_configure(config):  # noqa: ANN001 - pytest hook signature
    config.addinivalue_line(
        "markers",
        "integration: test hits a real LLM; skipped when credentials are missing",
    )
