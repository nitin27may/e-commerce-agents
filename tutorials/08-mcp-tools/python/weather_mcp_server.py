"""
Minimal MCP server exposing a canned weather tool over stdio.

Run standalone to sanity-check:
    python weather_mcp_server.py  # stays open, reads MCP frames from stdin

Used by main.py / tests via agent_framework.MCPStdioTool which spawns this
file as a subprocess and speaks MCP over stdio.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


server = FastMCP("maf-v1-ch08-weather")


@server.tool()
def get_weather(city: str) -> str:
    """Look up the current weather for a city (canned data)."""
    canned = {
        "paris": "Sunny, 18°C.",
        "london": "Overcast, 12°C.",
        "tokyo": "Rain, 15°C.",
    }
    return canned.get(city.lower(), f"No weather data for {city}.")


if __name__ == "__main__":
    server.run()
