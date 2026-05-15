"""MCP server for WSPR beacon data analytics — band openings, path analysis, solar correlation."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Final

try:
    _pkg_version = version("wspr-mcp")
except PackageNotFoundError:  # local dev / editable installs without dist metadata
    _pkg_version = "0.0.0-dev"

__version__: Final[str] = _pkg_version

# Upstream data spec the server is bound to. Pinned to the wspr.live
# ClickHouse schema revision we query — bump this when wspr.live
# publishes a new schema. Reported by the get_version_info tool so
# agents can detect fleet drift without going outside the MCP protocol.
__spec_version__: Final[str] = "wspr-live-v1"
