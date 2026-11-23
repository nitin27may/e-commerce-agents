"""The one place the Azure SDK module is bound, so tests can substitute a stub for it."""

from __future__ import annotations

import azure.ai.evaluation as sdk

__all__ = ["sdk"]
