"""MCP server for WSPR beacon data analytics — band openings, path analysis, solar correlation."""

from __future__ import annotations

try:
    from importlib.metadata import version

    __version__ = version("wspr-mcp")
except Exception:
    __version__ = "0.0.0-dev"
