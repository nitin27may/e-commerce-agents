"""Which model judges completeness, and whether that makes the run "self-judged".

The judge is configured with ``JUDGE_PROVIDER`` / ``JUDGE_MODEL`` / ``JUDGE_BASE_URL`` /
``JUDGE_API_KEY`` (``shared/config.py``), on any provider the chat client supports, and built by the
same factory plumbing (``shared.factory.get_judge_chat_client``).

Two rules:

* **No silent default to the model under test.** A judge that is the model it grades flatters itself,
  and a report that hid that would be misleading. With no ``JUDGE_*`` set, ``resolve_judge`` raises
  ``JudgeConfigError`` and says what to set.
* **Self-judging is allowed, never hidden.** ``JUDGE_ALLOW_SELF=true`` (or ``--allow-self-judge``) lets
  the judge be the model under test; a judge that resolves to the same provider and model is flagged
  ``self_judged`` whichever way it got there. The flag is printed in the report and stored in every
  record, and a self-judged run is not comparable with one judged by another model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evals.records import JudgeSetup, current_chat_model

JUDGE_PROVIDERS = ("openai", "azure", "anthropic", "replay")


class JudgeConfigError(ValueError):
    """The judge is not configured, or is configured in a way that is refused."""


@dataclass(frozen=True)
class JudgeConfig:
    provider: str
    model: str
    self_judged: bool
    # True only for an accepted self-judge with no JUDGE_* set: the judge is the run's own chat client.
    via_chat_client: bool = False

    def setup(self) -> JudgeSetup:
        return JudgeSetup(kind="llm", provider=self.provider, model=self.model, self_judged=self.self_judged)

    @property
    def cache_key(self) -> str:
        return f"{self.provider}:{self.model}"


def _judge_model(cfg: Any, provider: str) -> str:
    """The model the judge will call: ``JUDGE_MODEL``, else the provider's own configured model."""
    if cfg.JUDGE_MODEL.strip():
        return str(cfg.JUDGE_MODEL.strip())
    if provider == "replay":
        return "replay"
    return ""


def resolve_judge(cfg: Any, *, allow_self: bool = False) -> JudgeConfig:
    """The judge for a run, or ``JudgeConfigError`` when none may be used.

    ``cfg`` is a ``shared.config.Settings``. ``allow_self`` is the CLI's ``--allow-self-judge``.
    """
    under_test_provider = cfg.LLM_PROVIDER.strip().lower()
    under_test_model = current_chat_model(cfg)
    self_allowed = allow_self or bool(cfg.JUDGE_ALLOW_SELF)

    provider = (cfg.JUDGE_PROVIDER or "").strip().lower()
    if not provider:
        if not self_allowed:
            raise JudgeConfigError(
                "No judge configured. Set JUDGE_PROVIDER and JUDGE_MODEL (and JUDGE_BASE_URL / JUDGE_API_KEY for "
                "an endpoint of your own), or pass --allow-self-judge to let the model under test judge itself "
                "(the run is then labelled self-judged and is not comparable with an independently judged one)."
            )
        return JudgeConfig(provider=under_test_provider, model=under_test_model, self_judged=True, via_chat_client=True)

    if provider not in JUDGE_PROVIDERS:
        raise JudgeConfigError(
            f"Unknown JUDGE_PROVIDER {cfg.JUDGE_PROVIDER!r}. Must be one of {', '.join(JUDGE_PROVIDERS)}."
        )
    model = _judge_model(cfg, provider)
    if provider != "replay" and not model:
        raise JudgeConfigError(f"JUDGE_MODEL is required with JUDGE_PROVIDER={provider}.")
    same = provider == under_test_provider and model.lower() == under_test_model.lower()
    if same and not self_allowed:
        raise JudgeConfigError(
            f"The judge ({provider}:{model}) is the model under test. Choose a different JUDGE_MODEL / JUDGE_PROVIDER, "
            "or pass --allow-self-judge to accept a self-judged run."
        )
    return JudgeConfig(provider=provider, model=model, self_judged=same)
