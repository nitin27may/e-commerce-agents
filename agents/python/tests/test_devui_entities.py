"""B-17 (#98): the DevUI entry point builds the entities it promises, as the identity it was told to.

DevUI itself is not installed in this environment (it is added to the run only, see ``scripts/devui.py``), so these
cover everything that does not need it: the entities, the identity, and the bind guard.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from shared.context import current_session_id, current_user_email, current_user_role

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "devui.py"


@pytest.fixture(scope="module")
def devui() -> ModuleType:
    spec = importlib.util.spec_from_file_location("repo_devui_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _replay(monkeypatch: pytest.MonkeyPatch) -> None:
    # No key and no network: the chat client is only built, never called.
    monkeypatch.setattr("shared.config.settings.LLM_PROVIDER", "replay")


@pytest.fixture(autouse=True)
def _restore_identity():
    """``bind_dev_identity`` sets ContextVars, and a sync test shares its context with every test after it.

    Left bound, a later test that expects no signed-in user (the return workflow's "needs a signed-in user" path)
    would see this one's and go on to the database.
    """
    before = (current_user_email.get(), current_user_role.get(), current_session_id.get())
    yield
    current_user_email.set(before[0])
    current_user_role.set(before[1])
    current_session_id.set(before[2])


def test_it_registers_the_five_specialists_and_the_two_workflows(devui: ModuleType) -> None:
    devui.bind_dev_identity("alice.johnson@gmail.com", "customer")

    entities = devui.build_entities()

    names = sorted(str(getattr(e, "name", "")) for e in entities)
    assert names == [
        "inventory-fulfillment",
        "order-management",
        "pre-purchase",
        "pricing-promotions",
        "product-discovery",
        "return-and-replace",
        "review-sentiment",
    ]


def test_the_workflows_are_the_real_graphs_with_the_real_tools(devui: ModuleType) -> None:
    devui.bind_dev_identity("alice.johnson@gmail.com", "customer")

    workflows = {str(e.name): e for e in devui.build_entities() if type(e).__name__ == "Workflow"}

    assert set(workflows) == {"pre-purchase", "return-and-replace"}
    ret = workflows["return-and-replace"]
    assert {"check-eligibility", "policy-gate", "initiate-return", "finalize"} <= set(ret.executors)


def test_the_bound_identity_is_what_the_tools_read(devui: ModuleType) -> None:
    devui.bind_dev_identity("seller.demo@gmail.com", "seller")

    assert current_user_email.get() == "seller.demo@gmail.com"
    assert current_user_role.get() == "seller"
    assert current_session_id.get().startswith("devui-")


def test_the_prompt_is_composed_for_the_bound_role(devui: ModuleType) -> None:
    """Agents are built at construction time with a role-aware prompt, so the identity has to be bound first."""

    def instructions(role: str) -> str:
        devui.bind_dev_identity("x@example.com", role)
        agent = next(e for e in devui.build_entities() if getattr(e, "name", "") == "order-management")
        return str(getattr(agent, "default_options", {}).get("instructions", "") or getattr(agent, "instructions", ""))

    customer, admin = instructions("customer"), instructions("admin")

    assert customer and admin
    assert customer != admin  # the YAML has role-specific blocks; a customer's prompt must not be an admin's


def test_identity_comes_from_the_arguments_then_the_environment_then_a_seeded_customer(
    devui: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEVUI_USER_EMAIL", raising=False)
    monkeypatch.delenv("DEVUI_USER_ROLE", raising=False)
    assert devui.resolve_identity() == ("alice.johnson@gmail.com", "customer")

    monkeypatch.setenv("DEVUI_USER_EMAIL", " Support.Demo@Gmail.com ")
    monkeypatch.setenv("DEVUI_USER_ROLE", "SUPPORT")
    assert devui.resolve_identity() == ("support.demo@gmail.com", "support")
    assert devui.resolve_identity("a@b.co", "admin") == ("a@b.co", "admin")


def test_an_unknown_role_is_an_error_not_a_customer(devui: ModuleType) -> None:
    with pytest.raises(ValueError, match="DEVUI_USER_ROLE"):
        devui.resolve_identity("a@b.co", "root")


def test_a_non_loopback_bind_needs_an_auth_token(devui: ModuleType) -> None:
    devui.check_bind("127.0.0.1", None)
    devui.check_bind("localhost", None)
    devui.check_bind("0.0.0.0", "secret")  # noqa: S104 - the guard's own input, nothing is bound

    with pytest.raises(SystemExit, match="DEVUI_AUTH_TOKEN"):
        devui.check_bind("0.0.0.0", None)  # noqa: S104
    with pytest.raises(SystemExit):
        devui.check_bind("192.168.1.20", "")


def test_without_devui_installed_it_says_how_to_run_it(
    devui: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *a: object, **k: object):
        if name == "agent_framework_devui":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)

    assert devui.main([]) == 2
    assert "--with agent-framework-devui==1.0.0b261112" in capsys.readouterr().err


def test_binding_an_identity_does_not_leak_into_the_next_test(devui: ModuleType) -> None:
    """Guards the fixture above: the identity a test bound is gone by the time the next one starts."""
    # test_the_bound_identity_is_what_the_tools_read binds seller.demo@gmail.com; nothing may remain bound here.
    assert current_user_email.get() != "seller.demo@gmail.com"
    assert current_user_email.get() != "x@example.com"
