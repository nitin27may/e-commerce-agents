"""``log_safe`` strips line breaks and bounds length; request-derived names are never logged bare."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from shared.log_safe import MAX_LOGGED_LENGTH, log_safe

PYTHON_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("a\nb", "ab"),
        ("a\r\nb", "ab"),
        ("id=1\nINFO forged line\r", "id=1INFO forged line"),
        (None, "None"),
        (42, "42"),
    ],
)
def test_line_breaks_are_removed(raw: object, expected: str) -> None:
    assert log_safe(raw) == expected


def test_a_uuid_and_exceptions_are_stringified() -> None:
    value = uuid.uuid4()
    assert log_safe(value) == str(value)
    assert log_safe(ValueError("bad\nvalue")) == "badvalue"


def test_length_is_capped() -> None:
    out = log_safe("x" * 1000)
    assert out.startswith("x" * MAX_LOGGED_LENGTH) and len(out) == MAX_LOGGED_LENGTH + 3
    assert log_safe("x" * 10, limit=4) == "xxxx..."


# Names that come straight from a path parameter, header, query string or body field. A logging call that
# passes one of these without log_safe() is the log-injection finding CodeQL reports.
REQUEST_NAMES = {
    "approval_id",
    "hitl_id",
    "conversation_id",
    "request_id",
    "mode_name",
    "user_email",
    "session_id",
    "decision",
    "agent_name",
}
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
SWEPT = [
    "orchestrator",
    "worker",
    "workflows",
    "shared",
    "auth_server",
    "product_discovery",
    "order_management",
    "pricing_promotions",
    "review_sentiment",
    "inventory_fulfillment",
]


def _bare_request_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"logger", "log", "logging"}
        ):
            continue
        for arg in node.args[1:]:
            if isinstance(arg, ast.Name) and arg.id in REQUEST_NAMES:
                rel = path.relative_to(PYTHON_ROOT).as_posix()
                found.append(f"{rel}:{node.lineno} logs bare {arg.id}")
    return found


def test_no_logging_call_passes_a_request_derived_name_unwrapped() -> None:
    offenders: list[str] = []
    for top in SWEPT:
        for path in sorted((PYTHON_ROOT / top).rglob("*.py")):
            offenders.extend(_bare_request_names(path))
    assert offenders == [], "wrap these in shared.log_safe.log_safe():\n" + "\n".join(offenders)
