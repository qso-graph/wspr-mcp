"""L3 live integration tests for wspr-mcp.

These tests hit the real wspr.live ClickHouse instance. wspr.live is a
volunteer-run service, so we use small limits and sleep between tests
to respect rate limits.

Run with: pytest tests/test_live.py --live

Test IDs: WSPR-L3-001 through WSPR-L3-012
"""

import time

import pytest

from wspr_mcp.client import WSPRClient


@pytest.fixture(scope="module")
def client():
    """Single WSPRClient instance shared across all tests in the module."""
    return WSPRClient()


@pytest.fixture(autouse=True)
def rate_limit_pause():
    """Sleep 1 second after each test to respect wspr.live rate limits."""
    yield
    time.sleep(1)


# ---- WSPR-L3-001: spots(limit=5) returns structured spot data ----

@pytest.mark.live
def test_spots_live(client):
    """WSPR-L3-001: spots() returns list of spots with required fields."""
    spots = client.spots(limit=5)
    assert isinstance(spots, list)
    assert len(spots) > 0, "Expected at least 1 spot from wspr.live"
    assert len(spots) <= 5

    required_fields = {"time", "band", "tx_call", "rx_call", "snr", "distance_km"}
    for spot in spots:
        missing = required_fields - set(spot.keys())
        assert not missing, f"Spot missing fields: {missing}"
        assert isinstance(spot["snr"], (int, float))
        assert isinstance(spot["distance_km"], (int, float))
        assert len(spot["tx_call"]) >= 2, f"tx_call too short: {spot['tx_call']!r}"
        assert len(spot["rx_call"]) >= 2, f"rx_call too short: {spot['rx_call']!r}"


# ---- WSPR-L3-002: spots filtered by band ----

@pytest.mark.live
def test_spots_by_band_live(client):
    """WSPR-L3-002: spots(band='20m') returns only 20m spots."""
    spots = client.spots(band="20m", limit=5)
    assert isinstance(spots, list)
    # 20m is the most active WSPR band, should always have spots
    assert len(spots) > 0, "Expected at least 1 spot on 20m"
    for spot in spots:
        assert spot["band"] == "20m", f"Expected band='20m', got {spot['band']!r}"


# ---- WSPR-L3-003: band_activity returns per-band summary ----

@pytest.mark.live
def test_band_activity_live(client):
    """WSPR-L3-003: band_activity() returns list with band names and counts."""
    bands = client.band_activity()
    assert isinstance(bands, list)
    assert len(bands) > 0, "Expected at least 1 active band"

    for entry in bands:
        assert "band" in entry, "Missing 'band' key"
        assert "spots" in entry, "Missing 'spots' key"
        assert isinstance(entry["spots"], int)
        assert entry["spots"] > 0, f"Band {entry['band']} has 0 spots"
        # Band name should be a recognizable format (e.g., "20m", "40m")
        assert entry["band"].endswith("m") or "MHz" in entry["band"], \
            f"Unexpected band format: {entry['band']!r}"


# ---- WSPR-L3-004: top_beacons returns leaderboard ----

@pytest.mark.live
def test_top_beacons_live(client):
    """WSPR-L3-004: top_beacons(limit=5) returns callsign/grid/spot_count."""
    beacons = client.top_beacons(limit=5)
    assert isinstance(beacons, list)
    assert len(beacons) > 0, "Expected at least 1 beacon"
    assert len(beacons) <= 5

    required_fields = {"callsign", "grid", "spots"}
    for beacon in beacons:
        missing = required_fields - set(beacon.keys())
        assert not missing, f"Beacon missing fields: {missing}"
        assert len(beacon["callsign"]) >= 2
        assert isinstance(beacon["spots"], int)
        assert beacon["spots"] > 0


# ---- WSPR-L3-005: top_spotters returns leaderboard ----

@pytest.mark.live
def test_top_spotters_live(client):
    """WSPR-L3-005: top_spotters(limit=5) returns callsign/grid/spot_count."""
    spotters = client.top_spotters(limit=5)
    assert isinstance(spotters, list)
    assert len(spotters) > 0, "Expected at least 1 spotter"
    assert len(spotters) <= 5

    required_fields = {"callsign", "grid", "spots"}
    for spotter in spotters:
        missing = required_fields - set(spotter.keys())
        assert not missing, f"Spotter missing fields: {missing}"
        assert len(spotter["callsign"]) >= 2
        assert isinstance(spotter["spots"], int)
        assert spotter["spots"] > 0


# ---- WSPR-L3-006: propagation between two grids ----

@pytest.mark.live
def test_propagation_live(client):
    """WSPR-L3-006: propagation(tx='DN13', rx='JN48') returns path data."""
    result = client.propagation(tx="DN13", rx="JN48")
    assert isinstance(result, dict)
    # Path may or may not have data, but structure should be valid
    assert "tx" in result
    assert "rx" in result
    assert "bands" in result
    assert isinstance(result["bands"], list)
    # If there are bands, verify structure
    for band in result["bands"]:
        assert "band" in band
        assert "spots" in band
        assert "avg_snr" in band


# ---- WSPR-L3-007: propagation with popular path ----

@pytest.mark.live
def test_propagation_popular_path_live(client):
    """WSPR-L3-007: propagation on a well-traveled path (US-EU) returns data."""
    # DN (western US) to JN (central EU) is a very popular WSPR path
    result = client.propagation(tx="DN", rx="JN", hours=48)
    assert isinstance(result, dict)
    assert "bands" in result
    # This wide field-to-field path should almost always have data
    if result["bands"]:
        assert result.get("total_spots", 0) > 0


# ---- WSPR-L3-008: grid_activity returns summary and spots ----

@pytest.mark.live
def test_grid_activity_live(client):
    """WSPR-L3-008: grid_activity(grid='DN13') returns summary with spots."""
    result = client.grid_activity(grid="DN13", limit=5)
    assert isinstance(result, dict)
    assert "grid" in result
    assert "total_spots" in result
    assert "recent_spots" in result
    assert isinstance(result["recent_spots"], list)
    # DN13 may not always have spots, but structure should be valid
    if result["total_spots"] > 0:
        assert len(result["recent_spots"]) > 0
        for spot in result["recent_spots"]:
            assert "tx_call" in spot
            assert "rx_call" in spot
            assert "snr" in spot


# ---- WSPR-L3-009: grid_activity with active grid ----

@pytest.mark.live
def test_grid_activity_active_grid_live(client):
    """WSPR-L3-009: grid_activity with JO62 (central EU) always has data."""
    result = client.grid_activity(grid="JO62", limit=5)
    assert isinstance(result, dict)
    # JO62 covers Netherlands/Germany — always active on WSPR
    assert result["total_spots"] > 0, "JO62 should always have WSPR activity"
    assert len(result["recent_spots"]) > 0


# ---- WSPR-L3-010: snr_trend returns hourly trend data ----

@pytest.mark.live
def test_snr_trend_live(client):
    """WSPR-L3-010: snr_trend for a well-traveled path returns hourly data."""
    # Use wide Maidenhead fields for maximum chance of data
    result = client.snr_trend(tx="DN", rx="JO", hours=48)
    assert isinstance(result, dict)
    assert "tx" in result
    assert "rx" in result
    assert "trend" in result
    assert isinstance(result["trend"], list)
    # DN-JO is a busy corridor, should have trend data over 48 hours
    if result["trend"]:
        for entry in result["trend"]:
            assert "hour" in entry
            assert "avg_snr" in entry
            assert "spots" in entry
            assert isinstance(entry["avg_snr"], (int, float))


# ---- WSPR-L3-011: snr_trend with band filter ----

@pytest.mark.live
def test_snr_trend_band_filter_live(client):
    """WSPR-L3-011: snr_trend with band='20m' returns filtered trend."""
    result = client.snr_trend(tx="DN", rx="JO", band="20m", hours=48)
    assert isinstance(result, dict)
    assert "trend" in result
    # If there's data, all entries should be 20m
    for entry in result["trend"]:
        assert entry["band"] == "20m", f"Expected band='20m', got {entry['band']!r}"


# ---- WSPR-L3-012: longest_paths returns distance-sorted results ----

@pytest.mark.live
def test_longest_paths_live(client):
    """WSPR-L3-012: longest_paths(limit=5) returns spots sorted by distance."""
    paths = client.longest_paths(limit=5)
    assert isinstance(paths, list)
    assert len(paths) > 0, "Expected at least 1 long path"
    assert len(paths) <= 5

    for path in paths:
        assert "distance_km" in path
        assert "tx_call" in path
        assert "rx_call" in path
        assert "band" in path
        assert isinstance(path["distance_km"], (int, float))

    # Verify descending distance order
    distances = [p["distance_km"] for p in paths]
    assert distances == sorted(distances, reverse=True), \
        f"Paths not sorted by distance descending: {distances}"
