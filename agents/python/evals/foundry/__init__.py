"""Optional adapter: score run records with Microsoft Foundry's evaluators (``azure-ai-evaluation``).

This package is a second tier on top of the provider-neutral eval path. The core path (``evals.run_evals``,
evaluator, records, scoring, rescore, judge, harness, provider_check, baselines) imports no Azure package and
never imports this package; ``tests/test_eval_foundry.py`` enforces that. Importing ``evals.foundry`` without
the extra installed fails here, with the command that installs it:

    uv sync --extra foundry-evals

Entry point: ``python -m evals.foundry.run --records <records.jsonl>`` (see ``evals/foundry/README`` in
``evals/README.md``).
"""

from __future__ import annotations

INSTALL_HINT = "uv sync --extra foundry-evals"

try:
    import azure.ai.evaluation as _azure_probe  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised by a test that blocks the import
    raise ImportError(
        "evals.foundry needs the optional 'foundry-evals' extra (azure-ai-evaluation), which is not installed. "
        f"Install it with: {INSTALL_HINT}. The core eval path does not need it."
    ) from exc
