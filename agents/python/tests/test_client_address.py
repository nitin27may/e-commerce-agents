"""Client-address derivation for address-keyed limits: only a trusted proxy's X-Forwarded-For counts.

Pure-function cases plus the label-lookup route over real Redis/Postgres (testcontainers): two clients
behind one proxy get separate budgets. The .NET twin is ``ClientAddress`` (``ClientAddressTests``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import redis.asyncio as redis_asyncio
from httpx import ASGITransport, AsyncClient

import shared.rate_limit as rate_limit_module
from shared.config import settings
from shared.rate_limit import parse_trusted_proxies, resolve_client_ip
from shared.return_label import LEGACY_LABEL_TOKEN_REMOVE_AFTER, legacy_branch_due_for_removal
from tests.test_return_label_token import _app_for, pool  # noqa: F401

PROXY = "172.18.0.5"
NETS = parse_trusted_proxies("172.16.0.0/12, 10.0.0.0/8")


def test_default_hops_zero_ignores_the_header() -> None:
    assert settings.RATE_LIMIT_TRUSTED_PROXY_HOPS == 0
    assert resolve_client_ip("6.6.6.6", PROXY, 0, NETS) == PROXY


def test_spoofed_header_from_an_untrusted_peer_is_ignored() -> None:
    assert resolve_client_ip("6.6.6.6", "198.51.100.9", 1, NETS) == "198.51.100.9"


def test_header_from_a_trusted_proxy_is_honoured() -> None:
    assert resolve_client_ip("203.0.113.7", PROXY, 1, NETS) == "203.0.113.7"


def test_ipv4_mapped_ipv6_peer_matches_an_ipv4_network() -> None:
    assert resolve_client_ip("203.0.113.7", "::ffff:172.18.0.5", 1, NETS) == "203.0.113.7"


def test_rightmost_entry_wins_not_the_leftmost() -> None:
    assert resolve_client_ip("6.6.6.6, 203.0.113.7", PROXY, 1, NETS) == "203.0.113.7"
    assert resolve_client_ip("6.6.6.6, 203.0.113.7, 10.1.1.1", PROXY, 2, NETS) == "203.0.113.7"


@pytest.mark.parametrize("header", ["not-an-ip", "evil:key*", "999.1.1.1", ",,", ""])
def test_malformed_or_missing_header_falls_back_to_the_peer(header: str) -> None:
    assert resolve_client_ip(header, PROXY, 1, NETS) == PROXY


def test_chain_shorter_than_hops_falls_back_to_the_peer() -> None:
    assert resolve_client_ip("203.0.113.7", PROXY, 2, NETS) == PROXY


def test_no_gate_configured_lets_hops_decide() -> None:
    assert parse_trusted_proxies("") is None
    assert resolve_client_ip("203.0.113.7", "198.51.100.9", 1, None) == "203.0.113.7"


def test_a_gate_that_parses_to_nothing_trusts_nobody() -> None:
    nets = parse_trusted_proxies("garbage, also-garbage")
    assert nets == ()
    assert resolve_client_ip("6.6.6.6", PROXY, 1, nets) == PROXY


@pytest.fixture
def limited(redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch) -> redis_asyncio.Redis:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_MAX_REQUESTS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXIES", "")
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)
    return redis_client


async def _lookup(token: str, forwarded_for: str):
    app = _app_for("anon@example.com", uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as h:
        return await h.get(f"/api/returns/{token}/label", headers={"x-forwarded-for": forwarded_for})


@pytest.mark.asyncio
async def test_two_clients_behind_one_proxy_have_separate_label_budgets(pool, limited) -> None:  # noqa: F811
    for _ in range(3):
        assert (await _lookup("f" * 32, "203.0.113.1")).status_code == 404
    assert (await _lookup("e" * 32, "203.0.113.1")).status_code == 429
    # A different client behind the same proxy is untouched by the first one's burst.
    assert (await _lookup("e" * 32, "203.0.113.2")).status_code == 404


@pytest.mark.asyncio
async def test_rotating_a_spoofed_left_entry_does_not_mint_budgets(pool, limited) -> None:  # noqa: F811
    for i in range(3):
        assert (await _lookup("f" * 32, f"10.0.0.{i}, 203.0.113.1")).status_code == 404
    assert (await _lookup("e" * 32, "10.9.9.9, 203.0.113.1")).status_code == 429


def test_removal_marker_is_dec_31_2026_and_due_check_is_clock_injectable() -> None:
    assert date(2026, 12, 31) == LEGACY_LABEL_TOKEN_REMOVE_AFTER
    assert not legacy_branch_due_for_removal(date(2026, 12, 31))
    assert legacy_branch_due_for_removal(date(2027, 1, 1))


def test_legacy_token_branch_is_due_for_removal() -> None:
    """Fails once the legacy 12-character label-token branch is past its removal date. Real clock, on purpose."""
    assert not legacy_branch_due_for_removal(datetime.now(UTC).date()), (
        f"{LEGACY_LABEL_TOKEN_REMOVE_AFTER} has passed: delete the legacy return-label token branch "
        "(shared/return_label.py is_legacy/legacy_token_expired and the 12-character regex alternative, "
        "its call sites in orchestrator/routes/legacy.py, the .NET ReturnLabelToken legacy members) "
        "and then delete this test."
    )
