"""Diagnose why replay fixtures stopped resolving — and fix what can be fixed offline.

A fixture's filename is a hash of the request it was recorded from, and the
request is built from objects whose serialisation the framework owns. So a
framework upgrade, or an edit near a tool, can strand fixtures with nothing
that names the cause: CI just reports ``ReplayFixtureMissingError`` for ~100
cases. This reads every fixture's stored request/response and compares it with
what the *current* code would produce, in three categories:

1. **Serialisation drift** — ``Message.from_dict(stored).to_dict()`` differs
   from the stored message (the framework now writes messages differently).
   Fixable offline: rewrite the stored messages.
2. **Response schema drift** — the stored ``response`` no longer loads with
   ``ChatResponse.from_dict``. The recorded answer is unreadable: re-record.
3. **Tool spec drift** — the stored ``tools`` differ from the live
   ``to_json_schema_spec()`` of the agents' tools.

   - *format-only* (``spec``): same name, description, parameter names, types,
     defaults and required set — only the schema's shape changed (titles, key
     order, ``anyOf`` layout). Fixable offline: rewrite the stored tools.
   - *semantic*: a description, parameter or the tool set changed. The model
     saw different text when it answered, so the recording is stale: re-record.

Exit status: ``0`` clean, ``1`` everything found is fixable offline (run with
``--fix``), ``2`` at least one fixture needs re-recording. With ``--fix`` the
fixable categories are rewritten, the corpus is re-keyed with
``evals.rehash_fixtures``, and the exit status is that of a fresh check.

Not covered: a changed *prompt* (``instructions``). Those are composed per
request from live context, so there is no reliable "live" value to diff
against; a prompt edit still shows up as misses in the replay gate.

Usage::

    uv run python -m evals.fixture_drift
    uv run python -m evals.fixture_drift --fix
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "replay"

SERIALISATION = "serialisation drift"
RESPONSE = "response schema drift"
SPEC = "tool spec drift (format only)"
SEMANTIC = "tool drift (semantic)"

_FIXABLE = {SERIALISATION, SPEC}


@dataclass(frozen=True)
class Finding:
    fixture: str
    category: str
    detail: str

    @property
    def fixable(self) -> bool:
        return self.category in _FIXABLE


# ─────────────────────── live tool specs ───────────────────────


def live_tool_specs() -> dict[str, list[dict[str, Any]]]:
    """``{agent name: [tool spec, ...]}`` from each ``create_*_agent()``, in order."""
    import importlib
    import sys

    from evals.harness import AGENT_FACTORIES

    # Import first, so every module that binds ``settings`` is loaded below.
    factories = {
        name: getattr(importlib.import_module(module_path), factory)
        for name, (module_path, factory) in AGENT_FACTORIES.items()
    }

    # Building an agent needs a chat client; replay needs no key and makes no
    # call. Every loaded ``settings`` object is switched (some tests reload
    # ``shared.config``, leaving older copies bound in other modules) and put
    # back afterwards, so calling this from a test has no side effect.
    switched: dict[int, tuple[Any, str]] = {}
    for module in list(sys.modules.values()):
        candidate = getattr(module, "settings", None)
        if type(candidate).__name__ == "Settings" and hasattr(candidate, "LLM_PROVIDER"):
            switched.setdefault(id(candidate), (candidate, candidate.LLM_PROVIDER))
            candidate.LLM_PROVIDER = "replay"
    specs: dict[str, list[dict[str, Any]]] = {}
    try:
        for name, factory in factories.items():
            agent = factory()
            tools = (getattr(agent, "default_options", None) or {}).get("tools") or []
            agent_specs: list[dict[str, Any]] = []
            for t in tools:
                try:
                    agent_specs.append(t.to_json_schema_spec())
                except AttributeError:
                    agent_specs.append({"name": getattr(t, "name", str(t))})
            specs[name] = agent_specs
    finally:
        for candidate, previous in switched.values():
            candidate.LLM_PROVIDER = previous
    return specs


def _tool_name(spec: dict[str, Any]) -> str:
    return str((spec.get("function") or spec).get("name", ""))


def _types(schema: Any) -> list[str]:
    """Every ``type`` named anywhere in a property schema, sorted."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            t = node.get("type")
            if isinstance(t, str):
                found.add(t)
            elif isinstance(t, list):
                found.update(str(x) for x in t)
            for key in ("anyOf", "oneOf", "allOf"):
                for sub in node.get(key, []) or []:
                    walk(sub)
            if "items" in node:
                walk(node["items"])
            if "enum" in node:
                found.add("enum:" + json.dumps(node["enum"], sort_keys=True))

    walk(schema)
    return sorted(found)


def semantic_view(spec: dict[str, Any]) -> dict[str, Any]:
    """What the *model* can read from a tool spec, minus incidental shape."""
    fn = spec.get("function") or spec
    params = fn.get("parameters") or {}
    props = {
        key: {
            "description": value.get("description") if isinstance(value, dict) else None,
            "types": _types(value),
            "default": value.get("default") if isinstance(value, dict) else None,
        }
        for key, value in (params.get("properties") or {}).items()
    }
    return {
        "name": fn.get("name"),
        "description": fn.get("description"),
        "properties": props,
        "required": sorted(params.get("required") or []),
    }


def _best_agent(stored_names: list[str], live: dict[str, list[dict[str, Any]]]) -> str | None:
    """The agent whose tool list is the closest match to what the fixture stored."""
    wanted = set(stored_names)
    best: tuple[float, str] | None = None
    for agent, specs in live.items():
        names = [_tool_name(s) for s in specs]
        if names == stored_names:
            return agent
        have = set(names)
        score = len(wanted & have) / max(len(wanted | have), 1)
        if best is None or score > best[0]:
            best = (score, agent)
    return best[1] if best and best[0] > 0 else None


def _compare_tools(
    stored: list[dict[str, Any]], live: dict[str, list[dict[str, Any]]]
) -> tuple[list[tuple[str, str]], list[dict[str, Any]] | None]:
    """Return ``(findings as (category, detail), corrected tools or None)``."""
    if not stored:
        return [], None
    stored_names = [_tool_name(s) for s in stored]
    agent = _best_agent(stored_names, live)
    if agent is None:
        return [(SEMANTIC, f"no live agent offers tools {stored_names[:3]}...")], None

    live_specs = live[agent]
    live_names = [_tool_name(s) for s in live_specs]
    if live_names != stored_names:
        added = sorted(set(live_names) - set(stored_names))
        removed = sorted(set(stored_names) - set(live_names))
        detail = f"tool set/order differs from {agent}"
        if added:
            detail += f"; added {added}"
        if removed:
            detail += f"; removed {removed}"
        return [(SEMANTIC, detail)], None

    findings: list[tuple[str, str]] = []
    corrected: list[dict[str, Any]] = []
    format_only = False
    for old, new in zip(stored, live_specs, strict=True):
        corrected.append(new)
        if old == new:
            continue
        if semantic_view(old) != semantic_view(new):
            findings.append((SEMANTIC, f"{_tool_name(new)}: description or parameters changed"))
        else:
            format_only = True
            findings.append((SPEC, f"{_tool_name(new)}: schema layout changed"))
    if any(cat == SEMANTIC for cat, _ in findings):
        return findings, None
    return findings, corrected if format_only else None


# ─────────────────────── per-fixture check ───────────────────────


def check_fixture(path: Path, live: dict[str, list[dict[str, Any]]]) -> tuple[list[Finding], dict[str, Any] | None]:
    """Findings for one fixture, plus the offline-corrected data if any fix applies."""
    from agent_framework import ChatResponse, Message

    data = json.loads(path.read_text())
    request = data.get("request", {})
    findings: list[Finding] = []
    fixed = json.loads(json.dumps(data))  # deep copy
    changed = False

    # 1. serialisation drift
    stored_messages = request.get("messages", [])
    try:
        reserialised = [Message.from_dict(m).to_dict() for m in stored_messages]
    except Exception as exc:  # noqa: BLE001 - any load failure is a finding
        findings.append(Finding(path.name, SEMANTIC, f"stored messages no longer load: {exc!r}"))
        reserialised = stored_messages
    if reserialised != stored_messages:
        drifted = sum(1 for a, b in zip(stored_messages, reserialised, strict=False) if a != b)
        findings.append(
            Finding(
                path.name, SERIALISATION, f"{drifted} of {len(stored_messages)} message(s) re-serialise differently"
            )
        )
        fixed["request"]["messages"] = reserialised
        changed = True

    # 2. response schema drift
    try:
        ChatResponse.from_dict(data["response"])
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding(path.name, RESPONSE, f"response no longer loads: {exc!r}"))

    # 3. tool spec drift
    tool_findings, corrected = _compare_tools(request.get("tools", []), live)
    findings.extend(Finding(path.name, cat, detail) for cat, detail in tool_findings)
    if corrected is not None:
        fixed["request"]["tools"] = corrected
        changed = True

    return findings, (fixed if changed else None)


def check_all(
    fixtures_dir: Path, live: dict[str, list[dict[str, Any]]]
) -> tuple[list[Finding], dict[Path, dict[str, Any]]]:
    findings: list[Finding] = []
    fixes: dict[Path, dict[str, Any]] = {}
    for path in sorted(fixtures_dir.glob("*.json")):
        found, fixed = check_fixture(path, live)
        findings.extend(found)
        if fixed is not None:
            fixes[path] = fixed
    return findings, fixes


def exit_code(findings: list[Finding]) -> int:
    if not findings:
        return 0
    return 1 if all(f.fixable for f in findings) else 2


def _report(findings: list[Finding], total: int) -> None:
    print(f"Fixtures scanned: {total}")
    if not findings:
        print("No drift: every fixture still matches what the current code would send.")
        return
    for category in (SERIALISATION, SPEC, RESPONSE, SEMANTIC):
        group = [f for f in findings if f.category == category]
        if not group:
            continue
        fixable = "fixable offline (--fix)" if category in _FIXABLE else "needs re-recording"
        print(f"\n{category}: {len({f.fixture for f in group})} fixture(s) — {fixable}")
        for f in group[:5]:
            print(f"  {f.fixture}: {f.detail}")
        if len(group) > 5:
            print(f"  ... and {len(group) - 5} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--fix", action="store_true", help="Rewrite offline-fixable fixtures, then re-key the corpus.")
    args = parser.parse_args(argv)

    fixtures_dir: Path = args.fixtures_dir
    if not fixtures_dir.is_dir():
        print(f"Not a directory: {fixtures_dir}", file=sys.stderr)
        return 2

    live = live_tool_specs()
    total = len(list(fixtures_dir.glob("*.json")))
    findings, fixes = check_all(fixtures_dir, live)
    _report(findings, total)

    if args.fix and fixes:
        for path, data in fixes.items():
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"\nRewrote {len(fixes)} fixture(s); re-keying the corpus...")
        from evals.rehash_fixtures import main as rehash_main

        rehash_main(["--fixtures-dir", str(fixtures_dir)])
        findings, _ = check_all(fixtures_dir, live)
        print("\nAfter --fix:")
        _report(findings, len(list(fixtures_dir.glob("*.json"))))

    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
