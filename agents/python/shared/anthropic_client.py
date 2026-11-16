"""Claude through MAF's own connector (``agent-framework-anthropic``), for ``LLM_PROVIDER=anthropic``.

Never imported on the default (OpenAI / Azure / replay) paths: ``shared.factory`` imports it only when
an Anthropic client is actually built, so the provider's SDK is not loaded for everyone else.

One thing this module adds to the connector: **a default output length**. Claude's Messages API
requires ``max_tokens`` on every request, and the connector falls back to 1024 when a call sets none,
which truncates a specialist's answer mid-sentence. The .NET stack applies 4096
(``ChatClientFactory.AnthropicDefaultMaxOutputTokens``); this applies the same number, and an explicit
``max_tokens`` on a call (``default_options`` or per-run options) still wins.

Status: wired and covered by a stubbed-transport test only (``tests/test_anthropic_provider.py``). It has
never made a live call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_framework import Message
from agent_framework.anthropic import AnthropicClient

# Matches the .NET stack's ChatClientFactory.AnthropicDefaultMaxOutputTokens.
ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS = 4096


class ECommerceAnthropicClient(AnthropicClient):
    """``AnthropicClient`` with this repo's default ``max_tokens``."""

    def _prepare_options(
        self,
        messages: Sequence[Message],
        options: Mapping[str, Any],
        request_state: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not options.get("max_tokens") and not kwargs.get("max_tokens"):
            options = {**options, "max_tokens": ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS}
        return super()._prepare_options(messages, options, request_state, **kwargs)


def build_anthropic_client(*, model: str, api_key: str, anthropic_client: Any = None) -> ECommerceAnthropicClient:
    """Construct the Claude chat client. ``anthropic_client`` injects a pre-built ``AsyncAnthropic`` (tests)."""
    return ECommerceAnthropicClient(model=model, api_key=api_key, anthropic_client=anthropic_client)
