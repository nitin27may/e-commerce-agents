"""Execute the chapter 01-04 notebooks headlessly, keyless, under replay.

Each notebook's cells import the chapter's own ``main.py`` and reuse its exact prompts, so the committed
replay fixtures keep hitting. A notebook that drifts from its chapter (a retyped prompt, a changed
instruction string) fails here as a replay miss, not as a bad answer.

The committed notebooks carry no outputs and no execution counts; ``test_committed_notebooks_have_no_outputs``
holds that line so diffs stay small. ``scripts/strip_notebook_outputs.py`` cleans one.
"""

from __future__ import annotations

import pathlib

import nbformat
import pytest
from nbclient import NotebookClient

TUTORIALS = pathlib.Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted(TUTORIALS.glob("0[1-4]-*/python/notebook.ipynb"))
CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "ANTHROPIC_API_KEY",
    "LLM_BASE_URL",
    "RECORD",
)


def _id(path: pathlib.Path) -> str:
    return path.parents[1].name


def test_notebooks_are_present_for_chapters_01_to_04() -> None:
    assert [_id(p)[:2] for p in NOTEBOOKS] == ["01", "02", "03", "04"]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=_id)
def test_committed_notebooks_have_no_outputs(path: pathlib.Path) -> None:
    nb = nbformat.read(path, as_version=4)
    dirty = [
        i for i, c in enumerate(nb.cells) if c.cell_type == "code" and (c.get("outputs") or c.get("execution_count"))
    ]
    assert not dirty, (
        f"{path.name} has outputs or execution counts in cells {dirty}; run scripts/strip_notebook_outputs.py"
    )


@pytest.mark.parametrize("path", NOTEBOOKS, ids=_id)
def test_notebook_runs_keyless_under_replay(path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )
    client.execute()  # raises CellExecutionError on any failing cell, including the cells' own asserts

    stdout = "".join(
        o.get("text", "")
        for c in nb.cells
        if c.cell_type == "code"
        for o in c.outputs
        if o.get("output_type") == "stream"
    )
    assert stdout.strip(), "the notebook printed nothing; its cells did not run"
