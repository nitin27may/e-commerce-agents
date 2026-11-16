"""Prompt-size gate: how many tokens does each agent spend on instructions and tool schemas?

Every request pays for the composed system prompt plus the JSON schema of every
tool the agent carries, before the user has said a word. Both grow quietly: a
paragraph added to a YAML prompt, a docstring that gets longer, a tool with one
more parameter. This counts them per agent and per role with ``tiktoken``,
compares against the committed ``evals/baselines/prompt_tokens.json`` and fails
when any agent/role pair grows by more than ``--max-growth`` (default 10%).

Free and keyless: it builds each agent (no model call) and reads two things off
it — ``default_options["instructions"]`` (the composed, role-aware prompt) and
``default_options["tools"]`` rendered with ``to_json_schema_spec()``, the same
schema ``shared/replay_client.py`` hashes and a provider receives.

    uv run python -m evals.prompt_tokens                       # gate
    uv run python -m evals.prompt_tokens --update-baseline     # after a reviewed change
    uv run python -m evals.prompt_tokens --json                # machine-readable

Exit codes: 0 within budget, 1 over budget or no baseline for a measured pair,
2 the encoding could not be loaded (tiktoken downloads it on first use).

Counts are an estimate of what a gpt-4.1-family model is billed (``o200k_base``),
not an exact figure for any provider: a gate needs a stable yardstick, not the
invoice. Dynamic context (the per-user block ``ECommerceContextProvider`` injects)
is not included.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ENCODING = "o200k_base"
ROLES = ("customer", "seller", "admin")
DEFAULT_BASELINE = Path(__file__).resolve().parent / "baselines" / "prompt_tokens.json"
DEFAULT_MAX_GROWTH = 0.10

Counter = Callable[[str], int]


def tiktoken_counter(encoding: str = ENCODING) -> Counter:
    """A token counter backed by ``tiktoken`` (raises if the encoding cannot be loaded)."""
    import tiktoken

    enc = tiktoken.get_encoding(encoding)
    return lambda text: len(enc.encode(text, disallowed_special=()))


def tool_schemas(agent: Any) -> list[dict[str, Any]]:
    """The tool schemas a request to ``agent`` carries, in the provider's function-spec shape."""
    tools = (getattr(agent, "default_options", None) or {}).get("tools") or []
    return [t.to_json_schema_spec() for t in tools if hasattr(t, "to_json_schema_spec")]


def measure_agent(agent: Any, count: Counter) -> dict[str, int]:
    """Token counts for one built agent: instructions, tool schemas, and their sum."""
    instructions = (getattr(agent, "default_options", None) or {}).get("instructions") or ""
    schemas = tool_schemas(agent)
    # Compact and key-sorted: the count should not move because of whitespace or dict order.
    rendered = json.dumps(schemas, sort_keys=True, separators=(",", ":"), default=str)
    instruction_tokens = count(instructions)
    tool_tokens = count(rendered)
    return {
        "instructions": instruction_tokens,
        "tools": tool_tokens,
        "tool_count": len(schemas),
        "total": instruction_tokens + tool_tokens,
    }


def measure(
    count: Counter,
    agents: dict[str, Any] | None = None,
    roles: tuple[str, ...] = ROLES,
) -> dict[str, dict[str, dict[str, int]]]:
    """``{agent: {role: counts}}`` for every agent in ``evals.harness.AGENT_FACTORIES``."""
    from evals.harness import AGENT_FACTORIES, _create_agent
    from shared.context import current_user_role

    names = list(agents) if agents is not None else list(AGENT_FACTORIES)
    result: dict[str, dict[str, dict[str, int]]] = {}
    previous = current_user_role.get()
    try:
        for name in names:
            result[name] = {}
            for role in roles:
                # Prompts are composed at construction from this ContextVar.
                current_user_role.set(role)
                agent = agents[name](role) if agents is not None else _create_agent(name)
                result[name][role] = measure_agent(agent, count)
    finally:
        current_user_role.set(previous)
    return result


def compare(
    baseline: dict[str, Any],
    current: dict[str, dict[str, dict[str, int]]],
    max_growth: float = DEFAULT_MAX_GROWTH,
) -> tuple[list[str], list[str]]:
    """``(failures, report_lines)``. A pair fails when ``total`` grew by more than ``max_growth``.

    A measured pair with no baseline also fails: an unguarded agent or role is the
    thing this gate exists to prevent. Shrinking is never a failure.
    """
    entries = baseline.get("entries", {})
    failures: list[str] = []
    lines = [f"{'agent':<24}{'role':<10}{'baseline':>10}{'now':>10}{'change':>9}"]
    for agent, roles in current.items():
        for role, counts in roles.items():
            now = counts["total"]
            was = entries.get(agent, {}).get(role, {}).get("total")
            if was is None:
                failures.append(f"{agent}/{role}: no baseline (run with --update-baseline after review)")
                lines.append(f"{agent:<24}{role:<10}{'-':>10}{now:>10}{'new':>9}")
                continue
            growth = (now - was) / was if was else 0.0
            flag = ""
            if growth > max_growth:
                flag = "  ** OVER BUDGET **"
                failures.append(f"{agent}/{role}: {was} -> {now} tokens ({growth:+.1%}, limit {max_growth:+.0%})")
            lines.append(f"{agent:<24}{role:<10}{was:>10}{now:>10}{growth:>+9.1%}{flag}")
    return failures, lines


def build_baseline(current: dict[str, dict[str, dict[str, int]]], encoding: str = ENCODING) -> dict[str, Any]:
    return {"encoding": encoding, "entries": current}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fail when an agent's prompt + tool schemas grow past the baseline.")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--max-growth", type=float, default=DEFAULT_MAX_GROWTH, help="allowed growth, 0.10 = 10%%")
    parser.add_argument("--update-baseline", action="store_true", help="overwrite the baseline with this run")
    parser.add_argument("--json", action="store_true", help="print the measurements as JSON and exit 0")
    parser.add_argument("--encoding", default=ENCODING)
    args = parser.parse_args()

    try:
        count = tiktoken_counter(args.encoding)
    except Exception as exc:  # network, missing encoding, tiktoken absent
        print(f"Could not load the {args.encoding!r} encoding: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    current = measure(count)

    if args.json:
        print(json.dumps(build_baseline(current, args.encoding), indent=2))
        return 0

    path = Path(args.baseline)
    if args.update_baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build_baseline(current, args.encoding), indent=2) + "\n")
        print(f"Baseline written: {path}")
        return 0

    if not path.exists():
        print(f"No baseline at {path}; create one with --update-baseline.", file=sys.stderr)
        return 1
    baseline = json.loads(path.read_text())
    if baseline.get("encoding", args.encoding) != args.encoding:
        print(
            f"Baseline was counted with {baseline.get('encoding')!r}, not {args.encoding!r}; "
            "the numbers are not comparable. Re-baseline.",
            file=sys.stderr,
        )
        return 1

    failures, lines = compare(baseline, current, args.max_growth)
    print("\n".join(lines))
    if failures:
        print("\nPrompt token budget exceeded:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"\nPrompt token budget OK (limit {args.max_growth:+.0%} per agent and role).")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
