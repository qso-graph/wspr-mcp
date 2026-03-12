"""WSPR data client — queries wspr.live public ClickHouse database.

wspr.live (maintained by volunteers) mirrors all wsprnet.org spots into a
public ClickHouse instance at db1.wspr.live.  We follow their guidelines:

  - Always filter by time (and band when possible)
  - Select only needed columns
  - Avoid JOINs — use GROUP BY
  - Keep queries fast and cached
  - Rate-limit to 20 req/min — this is a volunteer service
  - GET only, no POST
"""

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

_DB_URL = "https://db1.wspr.live"

# ---------------------------------------------------------------------------
# Rate limiting: 20 req/min = 1 request per 3 seconds
# ---------------------------------------------------------------------------
_MIN_DELAY = 3.0

# ---------------------------------------------------------------------------
# Circuit breaker — back off on consecutive errors
# ---------------------------------------------------------------------------
_CB_THRESHOLD = 3        # open after 3 consecutive failures
_CB_RESET_TIME = 60.0    # try again after 60 seconds
_CB_MAX_BACKOFF = 300.0  # max backoff 5 minutes

# ---------------------------------------------------------------------------
# Cache TTLs (seconds)
# ---------------------------------------------------------------------------
_SPOTS_TTL = 120.0       # 2 min
_ACTIVITY_TTL = 300.0    # 5 min
_BAND_TTL = 300.0        # 5 min
_PATHS_TTL = 600.0       # 10 min

# ---------------------------------------------------------------------------
# Time window caps (hours) — keep queries fast
# ---------------------------------------------------------------------------
_MAX_HOURS_SPOTS = 72
_MAX_HOURS_BAND = 6
_MAX_HOURS_PATHS = 72
_MAX_HOURS_PROPAGATION = 72
_MAX_HOURS_GRID = 72
_MAX_HOURS_SNR = 72
_MAX_HOURS_LEADERS = 72

# ---------------------------------------------------------------------------
# Band mapping — wspr.live uses MHz integers
# ---------------------------------------------------------------------------
_BAND_MHZ: dict[str, int] = {
    "2200m": -1, "630m": 0, "160m": 1, "80m": 3, "60m": 5,
    "40m": 7, "30m": 10, "20m": 14, "17m": 18, "15m": 21,
    "12m": 24, "10m": 28, "6m": 50, "2m": 144, "70cm": 430,
}
_MHZ_BAND: dict[int, str] = {v: k for k, v in _BAND_MHZ.items()}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_CALLSIGN_RE = re.compile(r"^[A-Z0-9/\-]{2,20}$")
_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$", re.IGNORECASE)


def _is_mock() -> bool:
    return os.getenv("WSPR_MCP_MOCK") == "1"


def _validate_callsign(call: str) -> str:
    c = call.strip().upper()
    if not c:
        return ""
    if not _CALLSIGN_RE.match(c):
        raise ValueError(f"Invalid callsign: {call!r}")
    return c


def _validate_grid(grid: str) -> str:
    g = grid.strip().upper()[:6]
    if not g:
        return ""
    if not _GRID_RE.match(g):
        raise ValueError(f"Invalid grid square: {grid!r}")
    return g


def _validate_band(band: str) -> int | None:
    b = band.strip().lower()
    if not b:
        return None
    if b not in _BAND_MHZ:
        raise ValueError(
            f"Unknown band: {band!r}. "
            f"Valid: {', '.join(sorted(_BAND_MHZ, key=lambda x: _BAND_MHZ[x]))}"
        )
    return _BAND_MHZ[b]


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(val, hi))


def _band_label(mhz: int) -> str:
    return _MHZ_BAND.get(mhz, f"{mhz}MHz")


def _sql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_MOCK_SPOTS = [
    {"time": "2026-03-04 21:00:00", "band": "20m", "tx_call": "KI7MT",
     "tx_grid": "DN13", "rx_call": "KPH", "rx_grid": "CM87",
     "snr": -12, "distance_km": 742, "power_dbm": 23, "drift": 0},
    {"time": "2026-03-04 21:00:00", "band": "20m", "tx_call": "K9AN",
     "tx_grid": "EN50", "rx_call": "G8JNJ", "rx_grid": "IO91",
     "snr": -18, "distance_km": 6453, "power_dbm": 37, "drift": 0},
]


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class _CircuitBreaker:
    """Simple circuit breaker — opens after consecutive failures, resets on success."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures = 0
        self._last_failure: float = 0.0
        self._backoff = _CB_RESET_TIME

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._backoff = _CB_RESET_TIME

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure = time.monotonic()
            # Exponential backoff: 60s, 120s, 240s, capped at 300s
            self._backoff = min(
                _CB_RESET_TIME * (2 ** (self._failures - _CB_THRESHOLD)),
                _CB_MAX_BACKOFF,
            )

    def check(self) -> None:
        """Raise if circuit is open (too many recent failures)."""
        with self._lock:
            if self._failures < _CB_THRESHOLD:
                return
            elapsed = time.monotonic() - self._last_failure
            if elapsed < self._backoff:
                wait = int(self._backoff - elapsed)
                raise RuntimeError(
                    f"wspr.live circuit breaker open — {self._failures} consecutive "
                    f"failures. Retrying in {wait}s. The service may be down."
                )
            # Half-open: allow one request through


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class WSPRClient:
    """WSPR data client backed by wspr.live public ClickHouse.

    Rate limited to 20 req/min with circuit breaker protection.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cb = _CircuitBreaker()

    # -- infrastructure -----------------------------------------------------

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

    def _query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SELECT against db1.wspr.live, return rows as dicts."""
        self._cb.check()
        self._rate_limit()

        full_sql = sql.rstrip(";") + " FORMAT JSON"
        url = f"{_DB_URL}/?query={urllib.parse.quote(full_sql, safe='')}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", f"wspr-mcp/{__version__}")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            self._cb.record_failure()
            raise RuntimeError(
                "wspr.live query failed — service may be down"
            ) from exc

        if not body or body.strip() == "":
            self._cb.record_success()
            return []

        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            # ClickHouse returns plain text errors
            self._cb.record_failure()
            raise RuntimeError(f"wspr.live query error: {body[:200]}")

        self._cb.record_success()
        return result.get("data", [])

    # -- helpers for WHERE clauses ------------------------------------------

    @staticmethod
    def _time_filter(hours: int) -> str:
        return f"time >= now() - INTERVAL {hours} HOUR"

    @staticmethod
    def _band_filter(band_mhz: int | None) -> str:
        if band_mhz is None:
            return ""
        return f"band = {band_mhz}"

    # ======================================================================
    # Tool 1: wspr_spots — Recent spots for a callsign
    # ======================================================================

    def spots(
        self,
        callsign: str = "",
        band: str = "",
        hours: int = 24,
        limit: int = 50,
        grid: str = "",
        min_snr: int | None = None,
        max_snr: int | None = None,
        min_distance: int | None = None,
    ) -> list[dict[str, Any]]:
        """Recent WSPR spots with flexible filtering."""
        call = _validate_callsign(callsign)
        grid_prefix = _validate_grid(grid)[:4]
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_SPOTS)
        limit = _clamp(limit, 1, 200)

        key = f"spots:{call}:{band}:{grid_prefix}:{hours}:{min_snr}:{max_snr}:{min_distance}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if _is_mock():
            data = list(_MOCK_SPOTS)
            if band:
                data = [s for s in data if s.get("band", "").lower() == band.lower()]
            if call:
                data = [s for s in data if call in (
                    s.get("tx_call", "").upper(), s.get("rx_call", "").upper()
                )]
            self._cache_set(key, data[:limit], _SPOTS_TTL)
            return data[:limit]

        wheres = [self._time_filter(hours)]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)
        if call:
            esc = _sql_escape(call)
            wheres.append(f"(tx_sign = '{esc}' OR rx_sign = '{esc}')")
        if grid_prefix:
            gp = _sql_escape(grid_prefix)
            wheres.append(f"(tx_loc LIKE '{gp}%' OR rx_loc LIKE '{gp}%')")
        if min_snr is not None:
            wheres.append(f"snr >= {int(min_snr)}")
        if max_snr is not None:
            wheres.append(f"snr <= {int(max_snr)}")
        if min_distance is not None:
            wheres.append(f"distance >= {int(min_distance)}")

        sql = (
            f"SELECT time, band, tx_sign, tx_loc, rx_sign, rx_loc, "
            f"snr, distance, power, drift "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"ORDER BY time DESC LIMIT {limit}"
        )
        rows = self._query(sql)

        spots = [
            {
                "time": r.get("time", ""),
                "band": _band_label(r.get("band", 0)),
                "tx_call": r.get("tx_sign", ""),
                "tx_grid": r.get("tx_loc", ""),
                "rx_call": r.get("rx_sign", ""),
                "rx_grid": r.get("rx_loc", ""),
                "snr": r.get("snr", 0),
                "distance_km": r.get("distance", 0),
                "power_dbm": r.get("power", 0),
                "drift": r.get("drift", 0),
            }
            for r in rows
        ]

        self._cache_set(key, spots, _SPOTS_TTL)
        return spots

    # ======================================================================
    # Tool 2: wspr_band_activity — Per-band activity summary
    # ======================================================================

    def band_activity(self, hours: int = 1) -> list[dict[str, Any]]:
        """Per-band WSPR activity: spots, stations, distances."""
        hours = _clamp(hours, 1, _MAX_HOURS_BAND)

        key = f"band_activity:{hours}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        sql = (
            f"SELECT band, count() AS spots, "
            f"uniq(tx_sign) AS tx_stations, "
            f"uniq(rx_sign) AS rx_stations, "
            f"round(avg(distance)) AS avg_dist, "
            f"max(distance) AS max_dist, "
            f"round(avg(snr), 1) AS avg_snr "
            f"FROM wspr.rx "
            f"WHERE {self._time_filter(hours)} "
            f"GROUP BY band ORDER BY band"
        )
        rows = self._query(sql)

        bands = [
            {
                "band": _band_label(r.get("band", 0)),
                "spots": int(r.get("spots", 0)),
                "tx_stations": int(r.get("tx_stations", 0)),
                "rx_stations": int(r.get("rx_stations", 0)),
                "avg_distance_km": int(r.get("avg_dist", 0)),
                "max_distance_km": int(r.get("max_dist", 0)),
                "avg_snr": float(r.get("avg_snr", 0)),
            }
            for r in rows
        ]

        self._cache_set(key, bands, _BAND_TTL)
        return bands

    # ======================================================================
    # Tool 3: wspr_top_beacons — Top TX stations by spots or distance
    # ======================================================================

    def top_beacons(
        self,
        band: str = "",
        hours: int = 24,
        sort_by: str = "spots",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Top WSPR transmitters ranked by spot count or max distance."""
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_LEADERS)
        limit = _clamp(limit, 1, 50)
        order = "max_dist DESC" if sort_by == "distance" else "spots DESC"

        key = f"top_beacons:{band}:{hours}:{sort_by}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        wheres = [self._time_filter(hours)]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)

        sql = (
            f"SELECT tx_sign, any(tx_loc) AS grid, "
            f"count() AS spots, "
            f"uniq(rx_sign) AS reporters, "
            f"max(distance) AS max_dist, "
            f"round(avg(snr), 1) AS avg_snr, "
            f"groupUniqArray(band) AS bands "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"GROUP BY tx_sign "
            f"ORDER BY {order} LIMIT {limit}"
        )
        rows = self._query(sql)

        beacons = [
            {
                "callsign": r.get("tx_sign", ""),
                "grid": r.get("grid", ""),
                "spots": int(r.get("spots", 0)),
                "unique_reporters": int(r.get("reporters", 0)),
                "max_distance_km": int(r.get("max_dist", 0)),
                "avg_snr": float(r.get("avg_snr", 0)),
                "bands": sorted(
                    [_band_label(b) for b in r.get("bands", [])],
                    key=lambda x: _BAND_MHZ.get(x, 999),
                ),
            }
            for r in rows
        ]

        self._cache_set(key, beacons, _ACTIVITY_TTL)
        return beacons

    # ======================================================================
    # Tool 4: wspr_top_spotters — Top RX stations by spots or distance
    # ======================================================================

    def top_spotters(
        self,
        band: str = "",
        hours: int = 24,
        sort_by: str = "spots",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Top WSPR receivers ranked by spot count or max distance."""
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_LEADERS)
        limit = _clamp(limit, 1, 50)
        order = "max_dist DESC" if sort_by == "distance" else "spots DESC"

        key = f"top_spotters:{band}:{hours}:{sort_by}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        wheres = [self._time_filter(hours)]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)

        sql = (
            f"SELECT rx_sign, any(rx_loc) AS grid, "
            f"count() AS spots, "
            f"uniq(tx_sign) AS heard, "
            f"max(distance) AS max_dist, "
            f"round(avg(snr), 1) AS avg_snr, "
            f"groupUniqArray(band) AS bands "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"GROUP BY rx_sign "
            f"ORDER BY {order} LIMIT {limit}"
        )
        rows = self._query(sql)

        spotters = [
            {
                "callsign": r.get("rx_sign", ""),
                "grid": r.get("grid", ""),
                "spots": int(r.get("spots", 0)),
                "unique_heard": int(r.get("heard", 0)),
                "max_distance_km": int(r.get("max_dist", 0)),
                "avg_snr": float(r.get("avg_snr", 0)),
                "bands": sorted(
                    [_band_label(b) for b in r.get("bands", [])],
                    key=lambda x: _BAND_MHZ.get(x, 999),
                ),
            }
            for r in rows
        ]

        self._cache_set(key, spotters, _ACTIVITY_TTL)
        return spotters

    # ======================================================================
    # Tool 5: wspr_propagation — Path between two grids or callsigns
    # ======================================================================

    def propagation(
        self,
        tx: str,
        rx: str,
        band: str = "",
        hours: int = 24,
    ) -> dict[str, Any]:
        """Propagation between two locations (grid or callsign)."""
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_PROPAGATION)

        # Determine if input is grid or callsign
        tx_is_grid = bool(_GRID_RE.match(tx.strip()))
        rx_is_grid = bool(_GRID_RE.match(rx.strip()))

        if tx_is_grid:
            tx_val = _validate_grid(tx)[:4]
            tx_label = tx_val
        else:
            tx_val = _validate_callsign(tx)
            tx_label = tx_val
        if rx_is_grid:
            rx_val = _validate_grid(rx)[:4]
            rx_label = rx_val
        else:
            rx_val = _validate_callsign(rx)
            rx_label = rx_val

        if not tx_val or not rx_val:
            return {"error": "Both tx and rx are required (callsign or 4-char grid)"}

        key = f"prop:{tx_val}:{rx_val}:{hours}:{band}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        # Build bidirectional filter
        if tx_is_grid and rx_is_grid:
            esc_tx = _sql_escape(tx_val)
            esc_rx = _sql_escape(rx_val)
            path_filter = (
                f"((tx_loc LIKE '{esc_tx}%' AND rx_loc LIKE '{esc_rx}%') OR "
                f"(tx_loc LIKE '{esc_rx}%' AND rx_loc LIKE '{esc_tx}%'))"
            )
        elif not tx_is_grid and not rx_is_grid:
            esc_tx = _sql_escape(tx_val)
            esc_rx = _sql_escape(rx_val)
            path_filter = (
                f"((tx_sign = '{esc_tx}' AND rx_sign = '{esc_rx}') OR "
                f"(tx_sign = '{esc_rx}' AND rx_sign = '{esc_tx}'))"
            )
        else:
            # Mixed: one call, one grid
            if tx_is_grid:
                esc_grid = _sql_escape(tx_val)
                esc_call = _sql_escape(rx_val)
            else:
                esc_call = _sql_escape(tx_val)
                esc_grid = _sql_escape(rx_val)
            path_filter = (
                f"((tx_sign = '{esc_call}' AND rx_loc LIKE '{esc_grid}%') OR "
                f"(rx_sign = '{esc_call}' AND tx_loc LIKE '{esc_grid}%'))"
            )

        wheres = [self._time_filter(hours), path_filter]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)

        sql = (
            f"SELECT band, count() AS spots, "
            f"round(avg(snr), 1) AS avg_snr, "
            f"max(snr) AS best_snr, "
            f"min(snr) AS worst_snr, "
            f"round(avg(distance)) AS avg_dist, "
            f"max(distance) AS max_dist, "
            f"groupUniqArray(toHour(time)) AS open_hours "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"GROUP BY band ORDER BY band"
        )
        rows = self._query(sql)

        if not rows and tx_is_grid and rx_is_grid:
            # Proxy fallback: widen to 2-char Maidenhead fields
            tx_field = tx_val[:2]
            rx_field = rx_val[:2]
            if tx_field != tx_val or rx_field != rx_val:
                esc_tx_f = _sql_escape(tx_field)
                esc_rx_f = _sql_escape(rx_field)
                proxy_filter = (
                    f"((tx_loc LIKE '{esc_tx_f}%' AND rx_loc LIKE '{esc_rx_f}%') OR "
                    f"(tx_loc LIKE '{esc_rx_f}%' AND rx_loc LIKE '{esc_tx_f}%'))"
                )
                proxy_wheres = [self._time_filter(hours), proxy_filter]
                if bf:
                    proxy_wheres.append(bf)
                proxy_sql = (
                    f"SELECT band, count() AS spots, "
                    f"round(avg(snr), 1) AS avg_snr, "
                    f"max(snr) AS best_snr, "
                    f"min(snr) AS worst_snr, "
                    f"round(avg(distance)) AS avg_dist, "
                    f"max(distance) AS max_dist, "
                    f"groupUniqArray(toHour(time)) AS open_hours "
                    f"FROM wspr.rx WHERE {' AND '.join(proxy_wheres)} "
                    f"GROUP BY band ORDER BY band "
                    f"LIMIT 10000"
                )
                rows = self._query(proxy_sql)
                if rows:
                    proxy_bands = [
                        {
                            "band": _band_label(r.get("band", 0)),
                            "spots": int(r.get("spots", 0)),
                            "avg_snr": float(r.get("avg_snr", 0)),
                            "best_snr": int(r.get("best_snr", 0)),
                            "worst_snr": int(r.get("worst_snr", 0)),
                            "avg_distance_km": int(r.get("avg_dist", 0)),
                            "max_distance_km": int(r.get("max_dist", 0)),
                            "hours_open": sorted(r.get("open_hours", [])),
                        }
                        for r in rows
                    ]
                    data = {
                        "tx": tx_label, "rx": rx_label, "hours": hours,
                        "bands": proxy_bands,
                        "total_spots": sum(b["spots"] for b in proxy_bands),
                        "proxy": True,
                        "note": (
                            f"No exact match for {tx_val}\u2194{rx_val}. "
                            f"Showing wider field {tx_field}\u2194{rx_field}."
                        ),
                    }
                    self._cache_set(
                        f"prop:{tx_field}:{rx_field}:{hours}:{band}:proxy",
                        data, _PATHS_TTL,
                    )
                    return data

        if not rows:
            return {
                "tx": tx_label, "rx": rx_label, "hours": hours,
                "bands": [],
                "note": "No WSPR paths found between these endpoints in the time window",
            }

        bands = [
            {
                "band": _band_label(r.get("band", 0)),
                "spots": int(r.get("spots", 0)),
                "avg_snr": float(r.get("avg_snr", 0)),
                "best_snr": int(r.get("best_snr", 0)),
                "worst_snr": int(r.get("worst_snr", 0)),
                "avg_distance_km": int(r.get("avg_dist", 0)),
                "max_distance_km": int(r.get("max_dist", 0)),
                "hours_open": sorted(r.get("open_hours", [])),
            }
            for r in rows
        ]

        data = {
            "tx": tx_label, "rx": rx_label, "hours": hours,
            "bands": bands,
            "total_spots": sum(b["spots"] for b in bands),
        }

        self._cache_set(key, data, _PATHS_TTL)
        return data

    # ======================================================================
    # Tool 6: wspr_grid_activity — All spots in/out of a grid square
    # ======================================================================

    def grid_activity(
        self,
        grid: str,
        band: str = "",
        hours: int = 24,
        limit: int = 50,
    ) -> dict[str, Any]:
        """All WSPR spots involving a Maidenhead grid square."""
        grid_prefix = _validate_grid(grid)
        if not grid_prefix:
            return {"error": "Grid square is required (2 or 4 characters)"}
        # Use 2 or 4 char prefix as provided
        grid_prefix = grid_prefix[:4] if len(grid_prefix) >= 4 else grid_prefix[:2]
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_GRID)
        limit = _clamp(limit, 1, 200)

        key = f"grid:{grid_prefix}:{band}:{hours}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        gp = _sql_escape(grid_prefix)
        wheres = [
            self._time_filter(hours),
            f"(tx_loc LIKE '{gp}%' OR rx_loc LIKE '{gp}%')",
        ]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)

        # Summary stats (single query)
        summary_sql = (
            f"SELECT count() AS total_spots, "
            f"uniq(tx_sign) AS unique_tx, "
            f"uniq(rx_sign) AS unique_rx, "
            f"max(distance) AS max_dist, "
            f"round(avg(snr), 1) AS avg_snr, "
            f"groupUniqArray(band) AS bands "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)}"
        )
        summary_rows = self._query(summary_sql)
        summary = summary_rows[0] if summary_rows else {}

        # Recent spots
        spots_sql = (
            f"SELECT time, band, tx_sign, tx_loc, rx_sign, rx_loc, "
            f"snr, distance, power "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"ORDER BY time DESC LIMIT {limit}"
        )
        spot_rows = self._query(spots_sql)

        spots = [
            {
                "time": r.get("time", ""),
                "band": _band_label(r.get("band", 0)),
                "tx_call": r.get("tx_sign", ""),
                "tx_grid": r.get("tx_loc", ""),
                "rx_call": r.get("rx_sign", ""),
                "rx_grid": r.get("rx_loc", ""),
                "snr": r.get("snr", 0),
                "distance_km": r.get("distance", 0),
                "power_dbm": r.get("power", 0),
            }
            for r in spot_rows
        ]

        raw_bands = summary.get("bands", [])
        data = {
            "grid": grid_prefix,
            "hours": hours,
            "total_spots": int(summary.get("total_spots", 0)),
            "unique_transmitters": int(summary.get("unique_tx", 0)),
            "unique_receivers": int(summary.get("unique_rx", 0)),
            "max_distance_km": int(summary.get("max_dist", 0)),
            "avg_snr": float(summary.get("avg_snr") or 0),
            "bands_active": sorted(
                [_band_label(b) for b in raw_bands],
                key=lambda x: _BAND_MHZ.get(x, 999),
            ),
            "recent_spots": spots,
        }

        self._cache_set(key, data, _ACTIVITY_TTL)
        return data

    # ======================================================================
    # Tool 7: wspr_longest_paths — Longest distance spots
    # ======================================================================

    def longest_paths(
        self,
        band: str = "",
        hours: int = 24,
        limit: int = 20,
        min_distance: int = 0,
    ) -> list[dict[str, Any]]:
        """Longest WSPR paths in the given time window."""
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_PATHS)
        limit = _clamp(limit, 1, 50)
        min_distance = max(0, int(min_distance))

        key = f"longest:{band}:{hours}:{min_distance}:{limit}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        wheres = [self._time_filter(hours)]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)
        if min_distance > 0:
            wheres.append(f"distance >= {min_distance}")

        sql = (
            f"SELECT time, band, tx_sign, tx_loc, rx_sign, rx_loc, "
            f"snr, distance, power "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"ORDER BY distance DESC LIMIT {limit}"
        )
        rows = self._query(sql)

        paths = [
            {
                "time": r.get("time", ""),
                "band": _band_label(r.get("band", 0)),
                "tx_call": r.get("tx_sign", ""),
                "tx_grid": r.get("tx_loc", ""),
                "rx_call": r.get("rx_sign", ""),
                "rx_grid": r.get("rx_loc", ""),
                "snr": r.get("snr", 0),
                "distance_km": r.get("distance", 0),
                "power_dbm": r.get("power", 0),
            }
            for r in rows
        ]

        self._cache_set(key, paths, _PATHS_TTL)
        return paths

    # ======================================================================
    # Tool 8: wspr_snr_trend — SNR over time for a specific path
    # ======================================================================

    def snr_trend(
        self,
        tx: str,
        rx: str,
        band: str = "",
        hours: int = 24,
    ) -> dict[str, Any]:
        """SNR trend over time for a specific path (callsign or grid pair)."""
        band_mhz = _validate_band(band)
        hours = _clamp(hours, 1, _MAX_HOURS_SNR)

        tx_is_grid = bool(_GRID_RE.match(tx.strip()))
        rx_is_grid = bool(_GRID_RE.match(rx.strip()))

        if tx_is_grid:
            tx_val = _validate_grid(tx)[:4]
        else:
            tx_val = _validate_callsign(tx)
        if rx_is_grid:
            rx_val = _validate_grid(rx)[:4]
        else:
            rx_val = _validate_callsign(rx)

        if not tx_val or not rx_val:
            return {"error": "Both tx and rx are required (callsign or 4-char grid)"}

        key = f"snr_trend:{tx_val}:{rx_val}:{band}:{hours}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        # Build path filter (same logic as propagation)
        if tx_is_grid and rx_is_grid:
            esc_tx, esc_rx = _sql_escape(tx_val), _sql_escape(rx_val)
            path_filter = (
                f"((tx_loc LIKE '{esc_tx}%' AND rx_loc LIKE '{esc_rx}%') OR "
                f"(tx_loc LIKE '{esc_rx}%' AND rx_loc LIKE '{esc_tx}%'))"
            )
        elif not tx_is_grid and not rx_is_grid:
            esc_tx, esc_rx = _sql_escape(tx_val), _sql_escape(rx_val)
            path_filter = (
                f"((tx_sign = '{esc_tx}' AND rx_sign = '{esc_rx}') OR "
                f"(tx_sign = '{esc_rx}' AND rx_sign = '{esc_tx}'))"
            )
        else:
            if tx_is_grid:
                esc_grid, esc_call = _sql_escape(tx_val), _sql_escape(rx_val)
            else:
                esc_call, esc_grid = _sql_escape(tx_val), _sql_escape(rx_val)
            path_filter = (
                f"((tx_sign = '{esc_call}' AND rx_loc LIKE '{esc_grid}%') OR "
                f"(rx_sign = '{esc_call}' AND tx_loc LIKE '{esc_grid}%'))"
            )

        wheres = [self._time_filter(hours), path_filter]
        bf = self._band_filter(band_mhz)
        if bf:
            wheres.append(bf)

        # Hourly SNR buckets
        sql = (
            f"SELECT toStartOfHour(time) AS hour, "
            f"band, "
            f"count() AS spots, "
            f"round(avg(snr), 1) AS avg_snr, "
            f"max(snr) AS best_snr, "
            f"min(snr) AS worst_snr "
            f"FROM wspr.rx WHERE {' AND '.join(wheres)} "
            f"GROUP BY hour, band "
            f"ORDER BY hour, band"
        )
        rows = self._query(sql)

        if not rows:
            return {
                "tx": tx_val, "rx": rx_val, "hours": hours,
                "trend": [],
                "note": "No WSPR data found for this path in the time window",
            }

        trend = [
            {
                "hour": r.get("hour", ""),
                "band": _band_label(r.get("band", 0)),
                "spots": int(r.get("spots", 0)),
                "avg_snr": float(r.get("avg_snr", 0)),
                "best_snr": int(r.get("best_snr", 0)),
                "worst_snr": int(r.get("worst_snr", 0)),
            }
            for r in rows
        ]

        data = {
            "tx": tx_val, "rx": rx_val, "hours": hours,
            "total_observations": sum(t["spots"] for t in trend),
            "trend": trend,
        }

        self._cache_set(key, data, _PATHS_TTL)
        return data
