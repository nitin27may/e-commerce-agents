"""The MCP tools and their native twins show a model the same thing.

``MCP_ENABLED=true`` swaps a specialist's data tools for an MCP server's. A model picks a tool from its name,
description and parameter schema, so those must be equal on both paths: a reworded description is what moved
"when is it back in stock" from ``get_restock_schedule`` to ``get_warehouse_availability`` (#320). The MCP packages
cannot import ``shared`` (they install on their own), so each tool is mirrored by hand and this test is what keeps
the two from drifting. What a call returns is checked against the seeded database by ``evals/mcp_compare.py``
(``scripts/mcp-compare.sh``, the ``mcp-parity`` CI job). No database and no model here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ecommerce_mcp_inventory.server import mcp as inventory_mcp
from ecommerce_mcp_product.server import mcp as product_mcp

from inventory_fulfillment import tools as inventory_tools
from product_discovery import tools as product_tools
from shared.tools import inventory_tools as shared_inventory_tools
from shared.tools import pricing_tools

NATIVE_MODULES = {
    "product": (product_tools, pricing_tools),
    "inventory": (inventory_tools, shared_inventory_tools),
}


def _native(server: str, name: str) -> Any:
    for module in NATIVE_MODULES[server]:
        found = getattr(module, name, None)
        if found is not None:
            return found
    raise AssertionError(f"no native tool {name!r} for the {server} server")


def _mcp_tools(server: Any) -> dict[str, Any]:
    return {t.name: t for t in asyncio.run(server.list_tools())}


def _model_view(schema: dict[str, Any]) -> dict[str, Any]:
    """What the model is shown of a parameter schema: the properties and which are required (not the title)."""
    return {"properties": schema.get("properties", {}), "required": sorted(schema.get("required", []))}


CASES = [
    (server, name)
    for server, mcp in (("product", product_mcp), ("inventory", inventory_mcp))
    for name in _mcp_tools(mcp)
]


def test_every_mcp_tool_is_covered() -> None:
    assert {name for _, name in CASES} == {
        "search_products",
        "get_product_details",
        "compare_products",
        "get_trending_products",
        "get_price_history",
        "check_stock",
        "get_warehouse_availability",
        "get_restock_schedule",
        "estimate_shipping",
        "compare_carriers",
    }


@pytest.mark.parametrize(("server", "name"), CASES)
def test_description_equals_native_description(server: str, name: str) -> None:
    mcp_tool = _mcp_tools(product_mcp if server == "product" else inventory_mcp)[name]
    native = _native(server, name)

    assert mcp_tool.description == native.description


@pytest.mark.parametrize(("server", "name"), CASES)
def test_parameter_schema_equals_native_schema(server: str, name: str) -> None:
    mcp_tool = _mcp_tools(product_mcp if server == "product" else inventory_mcp)[name]
    native = _native(server, name)

    assert _model_view(mcp_tool.input_schema) == _model_view(native.parameters())


def test_inventory_descriptions_keep_restock_and_availability_apart() -> None:
    """The pair the model confused: 'when is it back' is the restock tool, 'what is where' the availability tool."""
    tools = _mcp_tools(inventory_mcp)

    assert "restock schedule" in tools["get_restock_schedule"].description.lower()
    assert tools["get_restock_schedule"].description != tools["get_warehouse_availability"].description
