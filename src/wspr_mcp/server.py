"""wspr-mcp: MCP server for WSPR beacon data analytics."""

from __future__ import annotations

import sys
from typing import Any

from fastmcp import FastMCP

from . import __version__
from .client import WSPRClient

mcp = FastMCP(
    "wspr-mcp",
    version=__version__,
    instructions=(
        "MCP server for WSPR (Weak Signal Propagation Reporter) beacon data. "
        "Live WSPR spots, callsign activity, per-band activity, longest paths, "
        "and grid-to-grid propagation analysis. "
        "All public data, no authentication required."
    ),
)

_client: WSPRClient | None = None


def _get_client() -> WSPRClient:
    global _client
    if _client is None:
        _client = WSPRClient()
    return _client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_spots(
    callsign: str = "",
    band: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Get recent WSPR spots.

    WSPR beacons transmit every 2 minutes on precise frequencies.
    Each spot is a 2-minute integration proving a propagation path exists.

    Args:
        callsign: Filter by TX or RX callsign. Empty for all.
        band: Filter by band (e.g., 20m, 40m). Empty for all bands.
        limit: Maximum spots to return (default 50, max 200).

    Returns:
        List of spots with TX/RX callsigns, grids, SNR, distance, and band.
    """
    try:
        spots = _get_client().spots(
            callsign=callsign,
            band=band,
            limit=min(limit, 200),
        )
        return {"total": len(spots), "spots": spots}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def wspr_activity(callsign: str) -> dict[str, Any]:
    """Get WSPR activity summary for a callsign.

    Shows TX/RX spot counts, active bands, unique reporters,
    maximum distance, and best SNR.

    Args:
        callsign: Callsign to look up (e.g., KI7MT, K9AN).

    Returns:
        Activity summary with spot counts, bands, reporters, and records.
    """
    try:
        return _get_client().activity(callsign)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def wspr_band_activity() -> dict[str, Any]:
    """Get current per-band WSPR activity summary.

    Shows how many WSPR spots, TX stations, and RX stations are active
    on each band, with average path distance. Useful for seeing which
    bands are open right now.

    Returns:
        Per-band activity with spot counts, station counts, and average distance.
    """
    try:
        data = _get_client().band_activity()
        return {"bands": data}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def wspr_top_paths(band: str = "", limit: int = 20) -> dict[str, Any]:
    """Get the longest/best WSPR paths in the last 24 hours.

    WSPR's precise timing and low power make it the gold standard for
    detecting marginal propagation. Long paths here prove the band is open.

    Args:
        band: Filter by band (e.g., 20m). Empty for all bands.
        limit: Maximum paths to return (default 20).

    Returns:
        List of top paths with TX/RX callsigns, grids, band, SNR, and distance.
    """
    try:
        paths = _get_client().top_paths(band=band, limit=limit)
        return {"total": len(paths), "paths": paths}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def wspr_propagation(tx_grid: str, rx_grid: str) -> dict[str, Any]:
    """Get WSPR-derived propagation between two grid squares.

    Shows which bands have been open between two locations in the last
    24 hours, with spot counts, average SNR, best SNR, and hours of
    opening. Based on actual WSPR beacon observations.

    Args:
        tx_grid: Transmitter grid square (e.g., DN13, FN31).
        rx_grid: Receiver grid square (e.g., JN48, IO91).

    Returns:
        Per-band propagation data with spot counts, SNR stats, and open hours.
    """
    try:
        return _get_client().propagation(tx_grid, rx_grid)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the wspr-mcp server."""
    transport = "stdio"
    port = 8009
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--transport" and i < len(sys.argv) - 1:
            transport = sys.argv[i + 1]
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    if transport == "streamable-http":
        mcp.run(transport=transport, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
