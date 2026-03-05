"""WSPR data client — WSPRnet spot queries and band activity."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from . import __version__

_WSPR_ROCKS = "https://wspr.rocks"

# Cache TTLs
_SPOTS_TTL = 120.0  # 2 minutes
_ACTIVITY_TTL = 300.0  # 5 minutes
_BAND_TTL = 300.0  # 5 minutes
_PATHS_TTL = 600.0  # 10 minutes

# Rate limiting
_MIN_DELAY = 0.5  # WSPRnet is volunteer-run, be respectful

# Band dial frequencies (MHz) used by WSPR
_WSPR_BANDS: dict[str, float] = {
    "160m": 1.8366, "80m": 3.5686, "60m": 5.2872,
    "40m": 7.0386, "30m": 10.1387, "20m": 14.0956,
    "17m": 18.1046, "15m": 21.0946, "12m": 24.9246,
    "10m": 28.1246, "6m": 50.2930, "2m": 144.4890,
}

# Reverse: frequency range → band name
_FREQ_RANGES: list[tuple[float, float, str]] = [
    (1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.2, 5.4, "60m"),
    (7.0, 7.3, "40m"), (10.1, 10.2, "30m"), (14.0, 14.4, "20m"),
    (18.0, 18.2, "17m"), (21.0, 21.5, "15m"), (24.8, 25.0, "12m"),
    (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
]


def _is_mock() -> bool:
    return os.getenv("WSPR_MCP_MOCK") == "1"


def _freq_to_band(freq_mhz: float) -> str:
    """Convert frequency in MHz to band name."""
    for lo, hi, name in _FREQ_RANGES:
        if lo <= freq_mhz <= hi:
            return name
    return f"{freq_mhz:.3f}MHz"


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_MOCK_SPOTS = [
    {
        "timestamp": "2026-03-04 21:00",
        "tx_call": "KI7MT",
        "tx_grid": "DN13la",
        "tx_power_dbm": 23,
        "rx_call": "KPH",
        "rx_grid": "CM87",
        "frequency": 14.097074,
        "snr": -12,
        "drift": 0,
        "distance_km": 742,
        "band": "20m",
    },
    {
        "timestamp": "2026-03-04 21:00",
        "tx_call": "K9AN",
        "tx_grid": "EN50",
        "tx_power_dbm": 37,
        "rx_call": "G8JNJ",
        "rx_grid": "IO91",
        "frequency": 14.097052,
        "snr": -18,
        "drift": 0,
        "distance_km": 6453,
        "band": "20m",
    },
    {
        "timestamp": "2026-03-04 21:02",
        "tx_call": "VK6XT",
        "tx_grid": "OF78",
        "tx_power_dbm": 23,
        "rx_call": "KI7MT",
        "rx_grid": "DN13",
        "frequency": 7.040088,
        "snr": -24,
        "drift": 1,
        "distance_km": 15246,
        "band": "40m",
    },
]

_MOCK_ACTIVITY = {
    "callsign": "KI7MT",
    "grid": "DN13la",
    "tx_spots": 47,
    "rx_spots": 312,
    "bands_active": ["20m", "40m", "30m"],
    "last_spot": "2026-03-04 21:00",
    "unique_reporters": 28,
    "max_distance_km": 15246,
    "best_snr": -8,
}

_MOCK_BAND_ACTIVITY = {
    "160m": {"spots": 234, "tx_stations": 45, "rx_stations": 89, "avg_distance_km": 1250},
    "80m": {"spots": 567, "tx_stations": 112, "rx_stations": 203, "avg_distance_km": 2100},
    "40m": {"spots": 1245, "tx_stations": 289, "rx_stations": 445, "avg_distance_km": 4500},
    "30m": {"spots": 876, "tx_stations": 198, "rx_stations": 367, "avg_distance_km": 5200},
    "20m": {"spots": 2134, "tx_stations": 456, "rx_stations": 678, "avg_distance_km": 7800},
    "15m": {"spots": 432, "tx_stations": 98, "rx_stations": 167, "avg_distance_km": 9200},
    "10m": {"spots": 187, "tx_stations": 34, "rx_stations": 76, "avg_distance_km": 11500},
}

_MOCK_TOP_PATHS = [
    {"tx_call": "VK6XT", "tx_grid": "OF78", "rx_call": "EA8BFK", "rx_grid": "IL18",
     "band": "20m", "snr": -22, "distance_km": 17482, "timestamp": "2026-03-04 20:30"},
    {"tx_call": "ZL2IFB", "tx_grid": "RE66", "rx_call": "SWL-IW2DZX", "rx_grid": "JN45",
     "band": "20m", "snr": -26, "distance_km": 18812, "timestamp": "2026-03-04 20:00"},
    {"tx_call": "K9AN", "tx_grid": "EN50", "rx_call": "VK2KRR", "rx_grid": "QF56",
     "band": "40m", "snr": -24, "distance_km": 15123, "timestamp": "2026-03-04 19:30"},
]

_MOCK_PROPAGATION = {
    "tx_grid": "DN13",
    "rx_grid": "JN48",
    "distance_km": 8842,
    "bands": {
        "20m": {"spots_24h": 12, "avg_snr": -15.2, "best_snr": -8, "hours_open": [14, 15, 16, 17, 18]},
        "40m": {"spots_24h": 3, "avg_snr": -22.7, "best_snr": -19, "hours_open": [4, 5, 6]},
    },
}


class WSPRClient:
    """WSPR data client with caching and rate limiting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # Cache + HTTP
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires, value = entry
        if time.monotonic() > expires:
            del self._cache[key]
            return None
        return value

    def _cache_set(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = (time.monotonic() + ttl, value)

    def _rate_limit(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < _MIN_DELAY:
                time.sleep(_MIN_DELAY - elapsed)
            self._last_request = time.monotonic()

    def _get_json(self, url: str) -> Any:
        self._rate_limit()
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", f"wspr-mcp/{__version__}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if not body or body.strip() == "":
            return None
        return json.loads(body)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def spots(
        self,
        callsign: str = "",
        band: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent WSPR spots, optionally filtered."""
        key = f"spots:{callsign}:{band}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = list(_MOCK_SPOTS)
        else:
            params: dict[str, str] = {"count": str(min(limit, 200))}
            if callsign:
                params["call"] = callsign.upper()
            if band and band in _WSPR_BANDS:
                params["band"] = str(_WSPR_BANDS[band])
            qs = urllib.parse.urlencode(params)
            data = self._get_json(f"{_WSPR_ROCKS}/api/spots?{qs}") or []

            # Normalize
            spots = []
            for s in data:
                if isinstance(s, dict):
                    freq = s.get("frequency", 0)
                    if isinstance(freq, (int, float)):
                        s["band"] = _freq_to_band(freq)
                    spots.append(s)
            data = spots

        # Client-side filtering
        if band and _is_mock():
            data = [s for s in data if s.get("band", "").lower() == band.lower()]
        if callsign and _is_mock():
            call = callsign.upper()
            data = [s for s in data if s.get("tx_call", "").upper() == call
                    or s.get("rx_call", "").upper() == call]

        self._cache_set(key, data[:limit], _SPOTS_TTL)
        return data[:limit]

    def activity(self, callsign: str) -> dict[str, Any]:
        """Get WSPR activity summary for a callsign."""
        call = callsign.upper()
        key = f"activity:{call}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = dict(_MOCK_ACTIVITY)
        else:
            data = self._get_json(
                f"{_WSPR_ROCKS}/api/activity/{urllib.parse.quote(call)}"
            )

        if not data:
            return {"callsign": call, "error": "No WSPR activity found"}

        self._cache_set(key, data, _ACTIVITY_TTL)
        return data

    def band_activity(self) -> dict[str, Any]:
        """Get current per-band WSPR activity summary."""
        key = "band_activity"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = dict(_MOCK_BAND_ACTIVITY)
        else:
            data = self._get_json(f"{_WSPR_ROCKS}/api/bands") or {}

        self._cache_set(key, data, _BAND_TTL)
        return data

    def top_paths(self, band: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Get longest/best WSPR paths in the last 24 hours."""
        key = f"top_paths:{band}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = list(_MOCK_TOP_PATHS)
        else:
            params: dict[str, str] = {"count": str(min(limit, 50))}
            if band:
                params["band"] = band
            qs = urllib.parse.urlencode(params)
            data = self._get_json(f"{_WSPR_ROCKS}/api/top?{qs}") or []

        if band and _is_mock():
            data = [p for p in data if p.get("band", "").lower() == band.lower()]

        self._cache_set(key, data[:limit], _PATHS_TTL)
        return data[:limit]

    def propagation(self, tx_grid: str, rx_grid: str) -> dict[str, Any]:
        """Get WSPR-derived propagation between two grid squares."""
        tg = tx_grid.upper()[:4]
        rg = rx_grid.upper()[:4]
        key = f"prop:{tg}:{rg}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = dict(_MOCK_PROPAGATION)
        else:
            params = urllib.parse.urlencode({"tx": tg, "rx": rg})
            data = self._get_json(f"{_WSPR_ROCKS}/api/propagation?{params}")

        if not data:
            return {"tx_grid": tg, "rx_grid": rg, "error": "No propagation data found"}

        self._cache_set(key, data, _PATHS_TTL)
        return data
