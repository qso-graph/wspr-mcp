"""wspr-mcp: MCP server for WSPR beacon data analytics.

Data source: wspr.live public ClickHouse database (db1.wspr.live)
containing all wsprnet.org spots (~2.7B records, 2008-present).
Rate limited to 20 req/min with circuit breaker protection.
"""

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
        "8 tools: live spots, band activity, top beacons, top spotters, "
        "path propagation, grid activity, longest paths, and SNR trends. "
        "Data from wspr.live (~2.7B spots, 2008-present). "
        "All public data, no authentication required. "
        "Rate limited to 20 req/min to respect the volunteer-run service."
    ),
)

_client: WSPRClient | None = None


def _get_client() -> WSPRClient:
    global _client
    if _client is None:
        _client = WSPRClient()
    return _client


# ---------------------------------------------------------------------------
# Tool 1: wspr_spots — "Who's hearing me?" / "What's on the air?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_spots(
    callsign: str = "",
    band: str = "",
    hours: int = 24,
    limit: int = 50,
    grid: str = "",
    min_snr: int | None = None,
    max_snr: int | None = None,
    min_distance: int | None = None,
) -> dict[str, Any]:
    """Get recent WSPR spots.

    WSPR beacons transmit every 2 minutes on precise frequencies.
    Each spot is a 2-minute integration proving a propagation path exists.

    Args:
        callsign: Filter by TX or RX callsign (e.g., KI7MT, K9AN). Empty for all.
        band: Filter by band (e.g., 20m, 40m, 10m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).
        limit: Maximum spots to return (1-200, default 50).
        grid: Filter by grid square prefix (e.g., DN13, FN31). Matches TX or RX.
        min_snr: Minimum SNR in dB (e.g., -20). Filter out weak signals.
        max_snr: Maximum SNR in dB (e.g., -5). Filter out strong signals.
        min_distance: Minimum path distance in km (e.g., 5000 for DX only).

    Returns:
        List of spots with TX/RX callsigns, grids, band, SNR, distance, power.
    """
    try:
        spots = _get_client().spots(
            callsign=callsign, band=band, hours=hours, limit=limit,
            grid=grid, min_snr=min_snr, max_snr=max_snr,
            min_distance=min_distance,
        )
        return {"total": len(spots), "hours": hours, "spots": spots}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 2: wspr_band_activity — "What bands are open right now?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_band_activity(hours: int = 1) -> dict[str, Any]:
    """Get per-band WSPR activity summary.

    Shows spot counts, TX/RX station counts, average and max distance,
    and average SNR for each band. The best indicator of which bands
    are open right now.

    Args:
        hours: Time window in hours (1-6, default 1).

    Returns:
        Per-band activity with spot counts, station counts, distances, and SNR.
    """
    try:
        data = _get_client().band_activity(hours=hours)
        return {"hours": hours, "bands": data}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 3: wspr_top_beacons — "Who are the big guns transmitting?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_top_beacons(
    band: str = "",
    hours: int = 24,
    sort_by: str = "spots",
    limit: int = 20,
) -> dict[str, Any]:
    """Get top WSPR transmitters ranked by spot count or max distance.

    Shows the most active or most far-reaching WSPR beacon operators.
    Useful for finding who's putting out a big signal on a band.

    Args:
        band: Filter by band (e.g., 20m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).
        sort_by: Ranking criteria — "spots" (most heard) or "distance" (farthest reach).
        limit: Number of results (1-50, default 20).

    Returns:
        Ranked list of transmitters with spot counts, reporters, max distance, bands.
    """
    try:
        beacons = _get_client().top_beacons(
            band=band, hours=hours, sort_by=sort_by, limit=limit,
        )
        return {"total": len(beacons), "hours": hours, "sort_by": sort_by, "beacons": beacons}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 4: wspr_top_spotters — "Who are the best receivers?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_top_spotters(
    band: str = "",
    hours: int = 24,
    sort_by: str = "spots",
    limit: int = 20,
) -> dict[str, Any]:
    """Get top WSPR receivers ranked by spot count or max distance.

    Shows the most prolific or most sensitive WSPR receiving stations.
    Useful for finding good receivers to monitor for propagation.

    Args:
        band: Filter by band (e.g., 20m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).
        sort_by: Ranking criteria — "spots" (most received) or "distance" (farthest heard).
        limit: Number of results (1-50, default 20).

    Returns:
        Ranked list of receivers with spot counts, unique heard, max distance, bands.
    """
    try:
        spotters = _get_client().top_spotters(
            band=band, hours=hours, sort_by=sort_by, limit=limit,
        )
        return {"total": len(spotters), "hours": hours, "sort_by": sort_by, "spotters": spotters}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 5: wspr_propagation — "Is the path to EU open?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_propagation(
    tx: str,
    rx: str,
    band: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Get WSPR-derived propagation between two locations.

    Shows which bands have been open between two endpoints, with spot
    counts, SNR statistics, and hours of opening. Accepts callsigns
    or grid squares (or a mix). Searches both directions automatically.

    Args:
        tx: First endpoint — callsign (e.g., KI7MT) or grid (e.g., DN13).
        rx: Second endpoint — callsign (e.g., G8JNJ) or grid (e.g., IO91).
        band: Filter to a specific band (e.g., 20m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).

    Returns:
        Per-band propagation with spot counts, SNR stats, and UTC hours open.
    """
    try:
        return _get_client().propagation(tx=tx, rx=rx, band=band, hours=hours)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 6: wspr_grid_activity — "What's happening in my area?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_grid_activity(
    grid: str,
    band: str = "",
    hours: int = 24,
    limit: int = 50,
) -> dict[str, Any]:
    """Get all WSPR activity in or out of a Maidenhead grid square.

    Shows summary stats and recent spots for a geographic area.
    Use 2-character grid (e.g., DN) for a wide area or 4-character
    (e.g., DN13) for a specific region.

    Args:
        grid: Maidenhead grid square — 2 char (DN) or 4 char (DN13).
        band: Filter by band (e.g., 20m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).
        limit: Maximum recent spots to return (1-200, default 50).

    Returns:
        Summary stats (totals, stations, bands) plus recent spot list.
    """
    try:
        return _get_client().grid_activity(
            grid=grid, band=band, hours=hours, limit=limit,
        )
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 7: wspr_longest_paths — "Best DX on 10m today?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_longest_paths(
    band: str = "",
    hours: int = 24,
    limit: int = 20,
    min_distance: int = 0,
) -> dict[str, Any]:
    """Get the longest WSPR paths in the given time window.

    WSPR's precise timing and low power make it the gold standard for
    detecting marginal propagation. Long paths here prove the band is open.

    Args:
        band: Filter by band (e.g., 20m, 10m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).
        limit: Maximum paths to return (1-50, default 20).
        min_distance: Minimum distance in km (e.g., 15000 for near-antipodal).

    Returns:
        Paths ranked by distance, with callsigns, grids, band, SNR, power.
    """
    try:
        paths = _get_client().longest_paths(
            band=band, hours=hours, limit=limit, min_distance=min_distance,
        )
        return {"total": len(paths), "hours": hours, "paths": paths}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 8: wspr_snr_trend — "How's SNR trending on this path?"
# ---------------------------------------------------------------------------


@mcp.tool()
def wspr_snr_trend(
    tx: str,
    rx: str,
    band: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Get SNR trend over time for a specific WSPR path.

    Shows hourly SNR buckets for a path between two endpoints.
    Useful for seeing when a band opens and closes on a specific path,
    and how signal strength varies over time.

    Args:
        tx: First endpoint — callsign (e.g., K9AN) or grid (e.g., EN50).
        rx: Second endpoint — callsign (e.g., G8JNJ) or grid (e.g., IO91).
        band: Filter to a specific band (e.g., 20m). Empty for all bands.
        hours: Time window in hours (1-72, default 24).

    Returns:
        Hourly SNR data points with spot counts, avg/best/worst SNR per hour.
    """
    try:
        return _get_client().snr_trend(tx=tx, rx=rx, band=band, hours=hours)
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
