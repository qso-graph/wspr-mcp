"""L2 unit tests for wspr-mcp — validation, circuit breaker, mock mode.

Uses WSPR_MCP_MOCK=1 for tool-level tests (no wspr.live queries).
Direct unit tests on validation functions and helpers.

Test IDs: WSPR-L2-001 through WSPR-L2-045
"""

from __future__ import annotations

import os
import pytest

# Enable mock mode before importing anything
os.environ["WSPR_MCP_MOCK"] = "1"

from wspr_mcp.client import (
    WSPRClient,
    _CircuitBreaker,
    _validate_callsign,
    _validate_grid,
    _validate_band,
    _clamp,
    _band_label,
    _sql_escape,
    _BAND_MHZ,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Fresh WSPRClient instance (no cache carryover)."""
    return WSPRClient()


@pytest.fixture
def cb():
    """Fresh circuit breaker."""
    return _CircuitBreaker()


# ---------------------------------------------------------------------------
# WSPR-L2-001..008: _validate_callsign
# ---------------------------------------------------------------------------


class TestValidateCallsign:
    def test_valid_callsign(self):
        """WSPR-L2-001: Valid callsign normalizes to uppercase."""
        assert _validate_callsign("ki7mt") == "KI7MT"

    def test_valid_with_slash(self):
        """WSPR-L2-002: Portable callsign with slash."""
        assert _validate_callsign("KI7MT/P") == "KI7MT/P"

    def test_empty_string(self):
        """WSPR-L2-003: Empty string returns empty."""
        assert _validate_callsign("") == ""
        assert _validate_callsign("  ") == ""

    def test_invalid_callsign(self):
        """WSPR-L2-004: Invalid chars raise ValueError."""
        with pytest.raises(ValueError):
            _validate_callsign("DROP TABLE")

    def test_too_long(self):
        """WSPR-L2-005: >20 chars raises ValueError."""
        with pytest.raises(ValueError):
            _validate_callsign("A" * 21)

    def test_whitespace_stripped(self):
        """WSPR-L2-006: Leading/trailing whitespace stripped."""
        assert _validate_callsign("  W1AW  ") == "W1AW"


# ---------------------------------------------------------------------------
# WSPR-L2-010..015: _validate_grid
# ---------------------------------------------------------------------------


class TestValidateGrid:
    def test_valid_4char(self):
        """WSPR-L2-010: Valid 4-char grid normalizes to uppercase."""
        assert _validate_grid("dn13") == "DN13"

    def test_valid_6char(self):
        """WSPR-L2-011: 6-char grid accepted and truncated to 6."""
        assert _validate_grid("DN13la") == "DN13LA"

    def test_empty_string(self):
        """WSPR-L2-012: Empty string returns empty."""
        assert _validate_grid("") == ""

    def test_invalid_grid(self):
        """WSPR-L2-013: Invalid grid raises ValueError."""
        with pytest.raises(ValueError):
            _validate_grid("XX99")  # X is invalid for field chars

    def test_sql_injection(self):
        """WSPR-L2-014: SQL injection attempt raises ValueError."""
        with pytest.raises(ValueError):
            _validate_grid("'; DROP TABLE --")


# ---------------------------------------------------------------------------
# WSPR-L2-016..020: _validate_band
# ---------------------------------------------------------------------------


class TestValidateBand:
    def test_valid_band(self):
        """WSPR-L2-016: Valid band returns MHz integer."""
        assert _validate_band("20m") == 14
        assert _validate_band("40m") == 7

    def test_case_insensitive(self):
        """WSPR-L2-017: Band matching is case-insensitive."""
        assert _validate_band("20M") == 14

    def test_empty_returns_none(self):
        """WSPR-L2-018: Empty band returns None."""
        assert _validate_band("") is None

    def test_invalid_band(self):
        """WSPR-L2-019: Unknown band raises ValueError."""
        with pytest.raises(ValueError, match="Unknown band"):
            _validate_band("99m")

    def test_all_bands(self):
        """WSPR-L2-020: All known bands resolve."""
        for band_name in _BAND_MHZ:
            result = _validate_band(band_name)
            assert isinstance(result, int)


# ---------------------------------------------------------------------------
# WSPR-L2-021..023: Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_clamp(self):
        """WSPR-L2-021: _clamp constrains values."""
        assert _clamp(5, 1, 10) == 5
        assert _clamp(-1, 1, 10) == 1
        assert _clamp(100, 1, 10) == 10

    def test_band_label(self):
        """WSPR-L2-022: MHz → band name mapping."""
        assert _band_label(14) == "20m"
        assert _band_label(7) == "40m"
        assert _band_label(999) == "999MHz"  # Unknown

    def test_sql_escape(self):
        """WSPR-L2-023: SQL escape handles quotes and backslashes."""
        assert _sql_escape("O'Brien") == "O\\'Brien"
        assert _sql_escape("back\\slash") == "back\\\\slash"
        assert _sql_escape("normal") == "normal"


# ---------------------------------------------------------------------------
# WSPR-L2-025..030: Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self, cb):
        """WSPR-L2-025: Fresh circuit breaker allows requests."""
        cb.check()  # Should not raise

    def test_opens_after_threshold(self, cb):
        """WSPR-L2-026: Opens after 3 consecutive failures."""
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        with pytest.raises(RuntimeError, match="circuit breaker open"):
            cb.check()

    def test_success_resets(self, cb):
        """WSPR-L2-027: Success resets failure count."""
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.check()  # Should not raise — count reset

    def test_two_failures_still_closed(self, cb):
        """WSPR-L2-028: Below threshold (2 < 3) stays closed."""
        cb.record_failure()
        cb.record_failure()
        cb.check()  # Should not raise


# ---------------------------------------------------------------------------
# WSPR-L2-031..040: Mock mode tool tests
# ---------------------------------------------------------------------------


class TestSpotsMock:
    def test_all_spots(self, client):
        """WSPR-L2-031: spots() returns mock spots."""
        result = client.spots()
        assert len(result) == 2

    def test_filter_by_callsign(self, client):
        """WSPR-L2-032: spots(callsign='KI7MT') filters correctly."""
        result = client.spots(callsign="KI7MT")
        assert len(result) == 1
        assert result[0]["tx_call"] == "KI7MT"

    def test_filter_by_band(self, client):
        """WSPR-L2-033: spots(band='20m') filters correctly."""
        result = client.spots(band="20m")
        assert len(result) == 2  # Both mock spots are 20m

    def test_spot_fields(self, client):
        """WSPR-L2-034: Mock spots have expected fields."""
        spots = client.spots()
        spot = spots[0]
        for field in ("time", "band", "tx_call", "tx_grid", "rx_call", "rx_grid",
                       "snr", "distance_km", "power_dbm"):
            assert field in spot, f"Missing field: {field}"

    def test_limit_respected(self, client):
        """WSPR-L2-035: Limit parameter caps results."""
        result = client.spots(limit=1)
        assert len(result) <= 1

    def test_spots_cached(self, client):
        """WSPR-L2-036: Second call returns cached result."""
        r1 = client.spots()
        r2 = client.spots()
        assert r1 == r2


# ---------------------------------------------------------------------------
# WSPR-L2-041..045: Cache and edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_cache_expiry(self, client):
        """WSPR-L2-041: Cache entries expire after TTL."""
        client._cache_set("test_key", "test_value", 0.01)
        assert client._cache_get("test_key") == "test_value"
        import time
        time.sleep(0.02)
        assert client._cache_get("test_key") is None

    def test_cache_miss(self, client):
        """WSPR-L2-042: Cache miss returns None."""
        assert client._cache_get("nonexistent") is None

    def test_time_filter(self):
        """WSPR-L2-043: Time filter generates valid SQL fragment."""
        result = WSPRClient._time_filter(24)
        assert "INTERVAL 24 HOUR" in result

    def test_band_filter_none(self):
        """WSPR-L2-044: Band filter with None returns empty string."""
        assert WSPRClient._band_filter(None) == ""

    def test_band_filter_value(self):
        """WSPR-L2-045: Band filter with value returns SQL fragment."""
        result = WSPRClient._band_filter(14)
        assert result == "band = 14"
