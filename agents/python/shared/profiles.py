"""Run profiles: provider setups kept as ``config/profiles/<name>.env`` overlays.

A profile sets chat, embeddings and the model together and never holds a secret (see the README beside the
files). This module reads them and applies one to an environment mapping *before* ``shared.config`` builds
``Settings``, so the precedence is the one the shell scripts also implement:

    exported in the shell  >  profile  >  ``.env``  >  defaults in ``shared.config``

A value of ``<set-me>`` means "the user supplies this": the profile leaves the shell and ``.env`` alone for that
variable, and it is reported as unresolved when neither has one.

Deliberately free of ``shared.config`` imports (importing that builds ``Settings`` and reads the environment);
``scripts/lib/profile.sh`` and ``scripts/lib/Profile.ps1`` mirror ``apply_profile``.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parents[1] / "config" / "profiles"
PLACEHOLDER = "<set-me>"

_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_META = re.compile(r"^#\s*@([a-z-]+):\s?(.*)$")

# A key-looking variable may only carry one of these (or nothing): they are placeholders local servers ask for.
SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD", re.IGNORECASE)
SECRET_PLACEHOLDERS = frozenset({"", "ollama", "lm-studio", "not-needed", "none", PLACEHOLDER})


class ProfileError(ValueError):
    """A profile cannot be found, read or applied."""


class UnknownProfileError(ProfileError):
    """The name matches no profile file. The message lists the valid names."""


@dataclass(frozen=True)
class Profile:
    name: str
    path: Path
    values: dict[str, str]
    meta: dict[str, str]
    suggested_judge: dict[str, str] = field(default_factory=dict)

    @property
    def tier(self) -> str:
        return self.meta.get("tier", "")

    @property
    def verified(self) -> str:
        return self.meta.get("verified", "")

    @property
    def dotnet(self) -> str:
        return self.meta.get("dotnet", "")

    @property
    def python_only(self) -> bool:
        """True when the .NET stack does not support the setup (``@dotnet: no ...``)."""
        return self.dotnet.strip().lower().startswith("no")


@dataclass
class AppliedProfile:
    """What ``apply_profile`` did, for the caller to print."""

    name: str
    applied: dict[str, str] = field(default_factory=dict)
    kept_from_environment: dict[str, str] = field(default_factory=dict)
    overrides_dotenv: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (.env value, profile value)
    unresolved: list[str] = field(default_factory=list)


def list_profiles(directory: Path | None = None) -> list[str]:
    root = directory or PROFILES_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.env"))


def load_profile(name: str, directory: Path | None = None) -> Profile:
    """Read ``<name>.env``. The name is checked against a strict pattern before it touches the filesystem."""
    root = directory or PROFILES_DIR
    valid = list_profiles(root)
    if not isinstance(name, str) or not _NAME.match(name) or name not in valid:
        raise UnknownProfileError(f"unknown profile {name!r}; valid profiles: {', '.join(valid) or '(none found)'}")
    path = root / f"{name}.env"
    values: dict[str, str] = {}
    meta: dict[str, str] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        match = _META.match(line)
        if match:
            current = match.group(1)
            meta[current] = match.group(2).strip()
            continue
        if current and re.match(r"^#\s{2,}\S", line):  # a continuation of the previous @field
            meta[current] = f"{meta[current]} {line.lstrip('# ').strip()}".strip()
            continue
        current = None
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    judge: dict[str, str] = {}
    for pair in meta.get("judge-env", "").split():
        key, _, value = pair.partition("=")
        if key and value:
            judge[key] = value
    return Profile(name=name, path=path, values=values, meta=meta, suggested_judge=judge)


def parse_dotenv(path: Path | None) -> dict[str, str]:
    """A minimal ``.env`` reader (``KEY=VALUE`` lines, surrounding quotes dropped); the last assignment wins."""
    if path is None or not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LINE.match(raw.strip().removeprefix("export "))
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[match.group(1)] = value
    return out


def repo_env_file() -> Path | None:
    """The repository's ``.env`` (three levels above this file), or None inside a container image."""
    parents = Path(__file__).resolve().parents
    return parents[3] / ".env" if len(parents) > 3 else None


def containerize(value: str) -> str:
    """Point a host-local URL at the host as a container sees it: ``localhost`` -> ``host.docker.internal``."""
    return re.sub(r"^(https?://)(localhost|127\.0\.0\.1)(?=[:/]|$)", r"\1host.docker.internal", value)


def apply_profile(
    profile: Profile,
    environ: MutableMapping[str, str] | None = None,
    *,
    dotenv: dict[str, str] | None = None,
    for_containers: bool = False,
) -> AppliedProfile:
    """Set the profile's variables in ``environ`` (default ``os.environ``) where the shell has not set them."""
    env = os.environ if environ is None else environ
    dotenv_values = parse_dotenv(repo_env_file()) if dotenv is None else dotenv
    result = AppliedProfile(name=profile.name)
    for key, value in profile.values.items():
        if value == PLACEHOLDER:
            if key in env or dotenv_values.get(key, ""):
                continue
            result.unresolved.append(key)
            continue
        if key in env:
            result.kept_from_environment[key] = env[key]
            continue
        final = containerize(value) if for_containers and key.endswith("_BASE_URL") else value
        env[key] = final
        result.applied[key] = final
        previous = dotenv_values.get(key)
        if previous is not None and previous != value:
            result.overrides_dotenv[key] = (previous, final)
    env["RUN_PROFILE"] = profile.name
    return result


def apply_profile_by_name(name: str, environ: MutableMapping[str, str] | None = None) -> AppliedProfile:
    """``load_profile`` then ``apply_profile``; raises ``ProfileError`` (unknown name, unresolved ``<set-me>``)."""
    applied = apply_profile(load_profile(name), environ)
    if applied.unresolved:
        raise ProfileError(
            f"profile {name!r} needs a value for {', '.join(applied.unresolved)}: set it in .env or export it"
        )
    return applied


def looks_like_secret_value(key: str, value: str) -> bool:
    """True when ``key`` names a secret and ``value`` is not empty or a known placeholder."""
    return bool(SECRET_NAME.search(key)) and value not in SECRET_PLACEHOLDERS
