"""
MAF v1 — Chapter 19: Declarative Workflows (Python)

Load a workflow from a YAML file at runtime instead of hand-writing the
graph in Python. Demonstrates the principle of declarative orchestration
with a minimal, purpose-built schema.

Run:
    python tutorials/19-declarative-workflows/python/main.py "hello"
    python tutorials/19-declarative-workflows/python/main.py ""     # short-circuit
"""

import asyncio
import pathlib
import sys
from typing import Any, Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import yaml  # noqa: E402

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow import Workflow  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402


SPEC_PATH = pathlib.Path(__file__).resolve().parent / "workflow.yaml"


# ─────────────── Built-in "ops" the YAML can reference ───────────────

def _build_op(op: str, config: dict[str, Any]) -> Callable[[str], tuple[str | None, str | None]]:
    """Returns a pure function: input_text -> (forwarded_text, terminal_text).

    If forwarded_text is not None → ctx.send_message it.
    If terminal_text is not None → ctx.yield_output it.
    """
    if op == "upper":
        return lambda s: (s.upper(), None)
    if op == "lower":
        return lambda s: (s.lower(), None)
    if op == "strip":
        return lambda s: (s.strip(), None)
    if op == "reverse":
        return lambda s: (s[::-1], None)
    if op == "non_empty":
        def _non_empty(s: str) -> tuple[str | None, str | None]:
            return (s, None) if s.strip() else (None, "[skipped: empty input]")
        return _non_empty
    if op == "prefix":
        prefix = config.get("prefix", "")
        return lambda s: (None, f"{prefix}{s}")
    raise ValueError(f"unknown op: {op!r}")


class DeclarativeExecutor(Executor):
    """An executor whose behavior is defined by a YAML 'op' string."""

    def __init__(self, executor_id: str, op: str, config: dict[str, Any]) -> None:
        super().__init__(id=executor_id)
        self._op = _build_op(op, config)

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        forward, terminal = self._op(message)
        if terminal is not None:
            await ctx.yield_output(terminal)
            return
        if forward is not None:
            await ctx.send_message(forward)


# ─────────────── Loader ───────────────

def load_workflow(spec_path: pathlib.Path = SPEC_PATH) -> Workflow:
    spec = yaml.safe_load(spec_path.read_text())
    executors_by_id: dict[str, DeclarativeExecutor] = {}
    for entry in spec["executors"]:
        executor_id = entry["id"]
        op = entry["op"]
        config = {k: v for k, v in entry.items() if k not in {"id", "op"}}
        executors_by_id[executor_id] = DeclarativeExecutor(executor_id, op, config)

    start = executors_by_id[spec["start"]]
    builder = WorkflowBuilder(start_executor=start, name=spec.get("name", "declarative"))
    for edge in spec["edges"]:
        builder = builder.add_edge(executors_by_id[edge["from"]], executors_by_id[edge["to"]])
    return builder.build()


async def run(text: str) -> list[object]:
    workflow = load_workflow()
    outputs: list[object] = []
    async for event in workflow.run(text, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"spec: {SPEC_PATH.name}")
    print(f"input: {text!r}")
    for output in await run(text):
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
