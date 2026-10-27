"""wspr.live's request limits, and how they differ from the circuit breaker.

Three mechanisms, asserted separately:

- the 3 s gap, which is wspr.live's own stated 20 req/min and is slept
- a 429, which is wspr.live stating that limit at runtime and is refused
- the circuit breaker, which counts failures *of the service*

The third is why the second needs its own path. A 429 is not a failure -- it is
the service working and telling us to ease off -- and counting it as one
reported a rate limit as "service may be down" and opened the breaker on a
healthy server. See qso-graph-devel/planning/MCP-SECURITY-FRAMEWORK.md.
"""

from __future__ import annotations

import time
import urllib.error
from email.message import Message

import pytest

from wspr_mcp import client as c

_URL = "https://db1.wspr.live/?query=SELECT%201"


def _http_error(code: int, **headers: str) -> urllib.error.HTTPError:
    m = Message()
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    return urllib.error.HTTPError(_URL, code, "nope", m, None)


def _raises(exc):
    def f(*a: object, **k: object):
        raise exc
    return f


# --- the stated limit ------------------------------------------------------


def test_the_stated_limit_is_the_figure_used() -> None:
    """wspr.live states 20 req/min. 60/20 = 3 s, and it is named in the
    server description so a user can see whose limit it is."""
    assert c._MIN_DELAY == 3.0


def test_the_spacing_is_slept_not_refused() -> None:
    """3 s is the stated pace; refusing it would turn pacing into an error."""
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0  # the gap has passed
    cl._rate_limit()  # must not raise


# --- Retry-After parsing ---------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, c._MIN_BACKOFF),       # absent: wait the floor, do not guess
        ("", c._MIN_BACKOFF),
        ("0", c._MIN_BACKOFF),        # "now" still gets a floor
        ("5", c._MIN_BACKOFF),        # below the floor is raised to it
        ("300", 300.0),               # honoured as stated
        ("99999", c._MAX_BACKOFF),    # above the ceiling is capped
        ("Wed, 21 Oct 2026 07:28:00 GMT", c._MIN_BACKOFF),  # date form unparsed
        ("garbage", c._MIN_BACKOFF),
    ],
)
def test_retry_after_is_honoured_within_bounds(
    header: str | None, expected: float
) -> None:
    assert c._retry_after(header) == expected


# --- what a 429 does, and what it does NOT do -----------------------------


def test_a_429_is_refused_with_the_wait_named(monkeypatch: pytest.MonkeyPatch) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="120"))
    )
    with pytest.raises(c.RateLimited) as e:
        cl._query("SELECT 1")
    assert e.value.retry_after == 120
    assert "120" in str(e.value)


def test_a_429_does_not_count_against_the_circuit_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this fixes: three 429s opened the breaker on a server that
    was working fine, and the message blamed the service."""
    cl = c.WSPRClient()
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="61"))
    )
    for _ in range(4):
        cl._last_request = time.monotonic() - 10.0
        cl._blocked_until = 0.0  # as if each wait had passed
        with pytest.raises(c.RateLimited):
            cl._query("SELECT 1")
    assert cl._cb._failures == 0, "a 429 was recorded as a failure of the service"


def test_a_429_is_not_reported_as_the_service_being_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="90"))
    )
    with pytest.raises(RuntimeError) as e:
        cl._query("SELECT 1")
    assert "may be down" not in str(e.value)
    assert "rate limiting" in str(e.value)


def test_a_block_refuses_the_next_query_without_sending_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="120"))
    )
    with pytest.raises(c.RateLimited):
        cl._query("SELECT 1")

    sent: list[str] = []
    monkeypatch.setattr(c.urllib.request, "urlopen", lambda *a, **k: sent.append("x"))
    start = time.monotonic()
    with pytest.raises(c.RateLimited):
        cl._query("SELECT 2")
    assert sent == [], "a query was sent while wspr.live had told us to stop"
    assert time.monotonic() - start < 1.0, "it slept through the backoff"


# --- the breaker still does its own job -----------------------------------


def test_a_real_failure_still_counts_against_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(urllib.error.URLError("refused"))
    )
    with pytest.raises(RuntimeError, match="may be down"):
        cl._query("SELECT 1")
    assert cl._cb._failures == 1


def test_a_bare_503_is_an_outage_not_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(c.urllib.request, "urlopen", _raises(_http_error(503)))
    with pytest.raises(RuntimeError) as e:
        cl._query("SELECT 1")
    assert not isinstance(e.value, c.RateLimited)
    assert cl._cb._failures == 1, "an outage must still count as a failure"
    assert cl._blocked_until == 0.0


def test_a_503_with_retry_after_is_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl = c.WSPRClient()
    cl._last_request = time.monotonic() - 10.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(503, Retry_After="90"))
    )
    with pytest.raises(c.RateLimited) as e:
        cl._query("SELECT 1")
    assert e.value.retry_after == 90
    assert cl._cb._failures == 0


def _client():
    cl = c.WSPRClient()
    return cl


def _raises(exc):
    def f(*a: object, **k: object):
        raise exc
    return f


# One name for "make the outbound call", so the tests below read the same in
# every repo even where the method behind it differs.
c.WSPRClient._fetch_for_test = lambda self: self._query("SELECT 1")  # type: ignore[attr-defined]


# --- defects found by review, held here so they stay fixed ----------------


@pytest.mark.parametrize("header", ["nan", "NaN", "inf", "infinity"])
def test_a_non_finite_header_still_blocks(header: str) -> None:
    """nan defeats a clamp -- every comparison against it is False -- so it
    would reach a deadline that is never in the future and leave the block off
    altogether. The floor applies instead."""
    assert c._retry_after(header) == c._MIN_BACKOFF


def test_a_shorter_later_block_does_not_shorten_a_longer_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response asking for 60 s landing while a 300 s block runs must not
    release us at 60."""
    cl = _client()
    cl._last_request = 0.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="300"))
    )
    with pytest.raises(c.RateLimited) as first:
        cl._fetch_for_test()
    assert first.value.retry_after == 300

    # A 60 s answer now, as a slower in-flight request would bring back.
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="60"))
    )
    cl._blocked_until = 0.0  # let one through, to reach the arming code again
    cl._last_request = 0.0
    with pytest.raises(c.RateLimited):
        cl._fetch_for_test()
    deadline_after_60 = cl._blocked_until

    # Arming again with 300 and then 60 must leave the 300 standing.
    cl._blocked_until = 0.0
    cl._last_request = 0.0
    monkeypatch.setattr(
        c.urllib.request, "urlopen", _raises(_http_error(429, Retry_After="300"))
    )
    with pytest.raises(c.RateLimited):
        cl._fetch_for_test()
    long_deadline = cl._blocked_until
    assert long_deadline > deadline_after_60


def test_a_block_arriving_during_the_gap_is_honoured() -> None:
    """The check must be made before the request, not before the wait: a 429
    landing while a caller sleeps out the spacing gap has to stop it."""
    import threading

    cl = _client()
    cl._last_request = time.monotonic()   # the next call sleeps out the gap
    refused: list[BaseException] = []

    def caller() -> None:
        try:
            cl._rate_limit()
        except c.RateLimited as e:
            refused.append(e)

    t = threading.Thread(target=caller)
    t.start()
    with cl._lock:
        cl._blocked_until = time.monotonic() + 300.0   # arrives mid-sleep
    t.join(timeout=5)
    assert refused, "a request went out that a 429 had already forbidden"


def test_the_spacing_sleep_does_not_hold_the_lock() -> None:
    """Sleeping under the lock would block an unrelated caller from even
    reading the state, and is how the mid-sleep 429 above gets missed."""
    import threading

    cl = _client()
    cl._last_request = time.monotonic()
    threading.Thread(target=cl._rate_limit, daemon=True).start()
    time.sleep(min(0.02, c._MIN_DELAY / 4))
    start = time.monotonic()
    acquired = cl._lock.acquire(timeout=max(1.0, c._MIN_DELAY))
    held = time.monotonic() - start
    if acquired:
        cl._lock.release()
    assert acquired, "the lock was held for the whole spacing sleep"
    assert held < c._MIN_DELAY, f"waited {held:.3f}s for the lock"
