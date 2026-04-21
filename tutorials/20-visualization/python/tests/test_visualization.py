"""
Chapter 20 — Workflow Visualization: tests.

No LLM — pure graph rendering.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import build_workflow, render_dot, render_mermaid  # noqa: E402


def test_mermaid_output_non_empty() -> None:
    mermaid = render_mermaid()
    assert mermaid.strip()


def test_mermaid_starts_with_flowchart_directive() -> None:
    mermaid = render_mermaid()
    assert mermaid.startswith("flowchart")


def test_mermaid_includes_all_three_executors() -> None:
    mermaid = render_mermaid()
    assert "uppercase" in mermaid
    assert "validate" in mermaid
    assert "log" in mermaid


def test_mermaid_includes_both_edges() -> None:
    mermaid = render_mermaid()
    # Edges are formatted "source --> target"
    assert "uppercase --> validate" in mermaid
    assert "validate --> log" in mermaid


def test_mermaid_is_deterministic() -> None:
    """Same workflow structure → same Mermaid output byte-for-byte."""
    assert render_mermaid() == render_mermaid()


def test_dot_output_is_a_digraph() -> None:
    dot = render_dot()
    assert dot.strip().startswith("digraph")


def test_dot_references_every_node() -> None:
    dot = render_dot()
    for node in ("uppercase", "validate", "log"):
        assert f'"{node}"' in dot


def test_dot_is_deterministic() -> None:
    assert render_dot() == render_dot()


def test_build_workflow_is_buildable() -> None:
    assert build_workflow() is not None
