"""The demo tour's manifest: the chips the web UI offers and the text shown on a replay miss (#86).

Lives in ``config/demo/tour.yaml`` so a recording session can edit prompts without
touching code. Validated whenever it is read: a typo in a mode name or a duplicate
chip id fails loudly with the file and field named, not as a chip that silently does
nothing in a browser.

Nothing here reads request input, and nothing is logged beyond the path and the
validation message.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from shared.config import settings

logger = logging.getLogger(__name__)

_PYTHON_ROOT = Path(__file__).resolve().parent.parent


class ManifestError(ValueError):
    """The tour manifest is missing or invalid."""


class TourChip(BaseModel):
    """One suggested prompt."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=1000)
    mode: str = "tool"
    # False = continue the conversation the previous chip started (a scripted follow-up).
    new_conversation: bool = True

    @field_validator("id", "label", "prompt", "mode")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class TourManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    miss_message: str = Field(min_length=1, max_length=500)
    chips: list[TourChip] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def _chips_are_coherent(self) -> TourManifest:
        seen: set[str] = set()
        for chip in self.chips:
            if chip.id in seen:
                raise ValueError(f"duplicate chip id {chip.id!r}")
            seen.add(chip.id)
        if not self.chips[0].new_conversation:
            raise ValueError("the first chip cannot continue a conversation: there is none yet")
        return self

    def validate_modes(self, known_modes: Collection[str]) -> None:
        """Raise ``ManifestError`` if a chip names a mode the orchestrator does not register."""
        for chip in self.chips:
            if chip.mode not in known_modes:
                raise ManifestError(f"chip {chip.id!r} names unknown mode {chip.mode!r}")


def manifest_path() -> Path:
    configured = Path(settings.DEMO_TOUR_PATH)
    return configured if configured.is_absolute() else _PYTHON_ROOT / configured


def parse_manifest(text: str, *, source: str = "tour manifest") -> TourManifest:
    """Parse and validate manifest YAML. Raises ``ManifestError`` naming the problem."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{source}: not valid YAML ({type(exc).__name__})") from None
    if not isinstance(raw, dict):
        raise ManifestError(f"{source}: expected a mapping with 'miss_message' and 'chips'")
    try:
        return TourManifest.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ManifestError(f"{source}: {problems}") from None


# (path, mtime_ns) -> manifest. Keyed on mtime so editing the file takes effect without a restart.
_CACHE: dict[tuple[str, int], TourManifest] = {}


def load_manifest() -> TourManifest | None:
    """The manifest at ``DEMO_TOUR_PATH``, or ``None`` when there is no such file.

    An existing but invalid file raises ``ManifestError``.
    """
    path = manifest_path()
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    manifest = parse_manifest(path.read_text(encoding="utf-8"), source=path.name)
    _CACHE.clear()
    _CACHE[key] = manifest
    return manifest
