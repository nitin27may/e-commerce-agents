"""A ``PyJWKClient`` that survives signing-key rotation without a restart.

On a ``kid`` the cached JWKS does not hold, the client refetches the JWKS once and retries, so a key the
auth-server rotated in after this process cached its JWKS is picked up on the first token that carries it. The
refetch is rate-limited (``UNKNOWN_KID_REFRESH_COOLDOWN`` seconds per process): without that, anyone able to send
a garbage ``kid`` could make every request a fetch against the auth-server.

PyJWT 2.15 has a cooldown of its own, older releases (the project allows ``>=2.9``) refetch on every miss, so the
behaviour is implemented here and does not depend on the installed version. The MCP servers vendor a copy of this
file (``ecommerce_mcp_*/jwks.py``), for the same reason they vendor their verifier.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientError

UNKNOWN_KID_REFRESH_COOLDOWN = 30.0


class RotationAwareJWKClient(PyJWKClient):
    """Looks up a signing key by ``kid``, refreshing the JWKS at most once per cooldown on a miss."""

    def __init__(
        self,
        uri: str,
        *,
        refresh_cooldown: float = UNKNOWN_KID_REFRESH_COOLDOWN,
        clock: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        self._refresh_cooldown = refresh_cooldown
        self._clock = clock
        self._last_forced_refresh: float | None = None
        self._refresh_lock = threading.Lock()
        super().__init__(uri, **kwargs)

    def get_signing_key(self, kid: str) -> PyJWK:
        key = self._find(self.get_signing_keys(), kid)
        if key is not None:
            return key

        with self._refresh_lock:
            now = self._clock()
            cooling_down = (
                self._last_forced_refresh is not None and now - self._last_forced_refresh < self._refresh_cooldown
            )
            if cooling_down:
                raise PyJWKClientError(f"Unable to find a signing key that matches: {kid!r}")
            # Stamp before fetching: a failing auth-server must not be hammered either.
            self._last_forced_refresh = now
            key = self._find(self.get_signing_keys(refresh=True), kid)

        if key is None:
            raise PyJWKClientError(f"Unable to find a signing key that matches: {kid!r}")
        return key

    @staticmethod
    def _find(keys: list[PyJWK], kid: str) -> PyJWK | None:
        return next((k for k in keys if k.key_id == kid), None)
