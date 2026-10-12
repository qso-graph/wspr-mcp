"""Conformance with the Model Context Protocol, revision 2026-07-28.

Nothing in this project's documents required this until 2026-10-11 — not
CLAUDE.md, not .claude/rules/security.md, not MCP-SECURITY-FRAMEWORK.md, not
qso-graph-spec. We documented how to *publish* an MCP server in detail and
never wrote down what makes one *valid*, which is how a missing ``isError`` and
missing annotations reached all 22 servers in the fleet unnoticed.

So these are asserted rather than trusted. ``get_version_info`` is the
cautionary case: a fleet convention nobody tested, which drifted.

Follows ``adif-mcp``, ``solar-mcp`` and ``pota-mcp``. What is particular to this
server is ``TestWhatItSaysAboutItself``: CLAUDE.md names wspr-mcp as *the*
pattern for honouring and naming a provider's stated request limit, and the
server's own instructions are where a user reads it. An unasserted sentence is
how the tool count in those same instructions came to say eight when there were
nine.

Test IDs: WSPR-C01 onward.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest
from fastmcp.exceptions import ToolError

from wspr_mcp import server

ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# The eight that query db1.wspr.live, and the one that does not.
READS_WSPR_LIVE = {
    "wspr_spots",
    "wspr_band_activity",
    "wspr_top_beacons",
    "wspr_top_spotters",
    "wspr_propagation",
    "wspr_grid_activity",
    "wspr_longest_paths",
    "wspr_snr_trend",
}
ANSWERS_FROM_THE_PACKAGE = {"get_version_info"}


def _tools() -> dict[str, object]:
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def _wire(tool: object) -> dict[str, object]:
    """The serialised annotations, which is what a client reads.

    Asserted on the wire form rather than the Python attributes: the SDK names
    its fields in snake_case and the protocol does not.
    """
    annotations = getattr(tool, "annotations", None)
    assert annotations is not None, "tool declares no annotations"
    return annotations.model_dump(by_alias=True, exclude_none=True)


def _call(name: str):
    """The underlying function of a registered tool."""
    tool = getattr(server, name)
    return getattr(tool, "fn", tool)


class TestToolAnnotations:
    def test_every_tool_declares_all_four_hints(self) -> None:
        """WSPR-C01: the defaults are pessimistic, so silence is the wrong answer.

        Omitting them advertises a read-only tool as ``destructiveHint: true``
        and ``openWorldHint: true``, and the specification tells clients to keep
        a human in the loop for operations like that — so a client may
        reasonably put a confirmation prompt in front of reading a beacon spot.
        """
        for name, tool in _tools().items():
            wire = _wire(tool)
            for hint in ANNOTATION_HINTS:
                assert hint in wire, f"{name} omits {hint} on the wire; got {sorted(wire)}"

    def test_every_tool_reads_and_none_writes(self) -> None:
        """WSPR-C02: the three hints that describe effect.

        Every tool here queries and reports. Nothing in this server writes
        anything, anywhere.
        """
        for name, tool in _tools().items():
            wire = _wire(tool)
            assert wire["readOnlyHint"] is True, name
            assert wire["destructiveHint"] is False, name
            assert wire["idempotentHint"] is True, name

    def test_open_world_is_read_per_tool(self) -> None:
        """WSPR-C03: the hint that is not the same for all of them.

        Eight tools reach wspr.live; ``get_version_info`` reports what the wheel
        carries and opens no connection. Setting them alike would make one of
        the two wrong, whichever value was chosen.
        """
        tools = _tools()
        assert set(tools) == READS_WSPR_LIVE | ANSWERS_FROM_THE_PACKAGE
        for name in READS_WSPR_LIVE:
            assert _wire(tools[name])["openWorldHint"] is True, name
        for name in ANSWERS_FROM_THE_PACKAGE:
            assert _wire(tools[name])["openWorldHint"] is False, name

    def test_the_server_really_does_reach_wspr_live(self) -> None:
        """WSPR-C04: the control for the eight ``openWorldHint: True``.

        A claim in an annotation is worth what the code behind it is worth.
        """
        src = pathlib.Path(server.__file__).parent
        outbound = re.compile(
            r"urllib\.request\.(urlopen|Request)|httpx\.|requests\.(get|post)"
        )
        reached = [p.name for p in src.rglob("*.py")
                   if outbound.search(p.read_text(encoding="utf-8", errors="replace"))]
        assert reached, "no outbound call found, so openWorldHint: True is not honest"


class TestToolErrorsAreFlagged:
    """A tool execution error belongs in the result with ``isError: true``.

    The specification states the cost of getting this wrong: otherwise "the LLM
    would not be able to see that an error occurred and self-correct". These
    returned ``{"error": "..."}`` as a *successful* result until #22, which a
    client cannot tell apart from an answer.
    """

    @pytest.fixture
    def broken(self, monkeypatch):
        """A client whose every call fails the way an unreachable service does."""
        class Broken:
            def __getattr__(self, _name):
                def fail(*_args, **_kwargs):
                    raise RuntimeError("wspr.live query failed")
                return fail

        monkeypatch.setattr(server, "_get_client", lambda: Broken())

    # Valid arguments throughout: what is being exercised is the failure of the
    # query, not the refusal of the input. Three of these tools take required
    # ones, so they are given here rather than defaulted away.
    UPSTREAM_CASES = [
        ("wspr_spots", {}),
        ("wspr_band_activity", {}),
        ("wspr_top_beacons", {}),
        ("wspr_top_spotters", {}),
        ("wspr_longest_paths", {}),
        ("wspr_propagation", {"tx": "KI7MT", "rx": "DN13"}),
        ("wspr_grid_activity", {"grid": "DN13"}),
        ("wspr_snr_trend", {"tx": "KI7MT", "rx": "DN13"}),
    ]

    def test_the_cases_cover_every_tool_that_queries(self) -> None:
        """WSPR-C09: the parametrisation below cannot silently miss one.

        A list written by hand beside a set is a list that falls behind it.
        """
        assert {name for name, _ in self.UPSTREAM_CASES} == READS_WSPR_LIVE

    @pytest.mark.parametrize("name,kwargs", UPSTREAM_CASES)
    def test_an_upstream_failure_is_flagged(self, broken, name, kwargs) -> None:
        """WSPR-C10: every tool that can fail, not a sample of them.

        Parametrised because the old shape was applied by copy and paste: a
        single example would pass while seven others still returned a dict.
        """
        with pytest.raises(ToolError, match="wspr.live query failed"):
            _call(name)(**kwargs)

    def test_a_missing_path_end_is_flagged(self) -> None:
        """WSPR-C11: input the caller can fix, raised a layer down in the client.

        A propagation path needs both ends. Asking for one used to come back as
        a successful result whose payload said it had failed.
        """
        with pytest.raises(ToolError, match="Both tx and rx are required"):
            _call("wspr_propagation")(tx="KI7MT", rx="")

    def test_a_missing_grid_is_flagged(self) -> None:
        """WSPR-C12: the same, for grid activity."""
        with pytest.raises(ToolError, match="Grid square is required"):
            _call("wspr_grid_activity")(grid="")


class TestAnEmptyAnswerIsNotAnError:
    """The distinction the specification draws, and the easy one to get wrong."""

    def test_a_query_matching_nothing_is_a_successful_call(self) -> None:
        """WSPR-C20: "no spots on that band in that window" is an answer.

        The band is quiet; the query worked. Reporting it as a failure would
        make a model retry something that succeeded, and quiet bands are a
        normal and interesting result in propagation work.
        """
        answer = _call("wspr_spots")(band="2200m", callsign="ZZ0ZZZ")
        assert isinstance(answer, dict)
        assert answer["total"] == 0

    def test_a_normal_query_is_also_a_successful_call(self) -> None:
        """WSPR-C21: the control — the above refuses some things, not everything."""
        answer = _call("wspr_spots")()
        assert answer["total"] >= 0
        assert "spots" in answer


class TestWhatItSaysAboutItself:
    """The instructions a client reads before calling anything.

    CLAUDE.md makes this repo the fleet's pattern for provider limits: honour
    the limit a service states, "and name it where a user can see it — wspr-mcp
    naming wspr.live's 20 req/min is the pattern". That sentence is only worth
    something while it is true.
    """

    def test_the_stated_request_limit_is_the_one_enforced(self) -> None:
        """WSPR-C30: the number in the instructions matches the code.

        wspr.live is volunteer-run and states 20 req/min. The client sleeps
        ``_MIN_DELAY`` seconds between requests, so the two have to agree: a
        promise in prose that the code does not keep is worse than no promise.
        """
        from wspr_mcp.client import _MIN_DELAY

        instructions = server.mcp.instructions or ""
        assert "20 req/min" in instructions
        assert _MIN_DELAY == 60 / 20

    def test_the_tool_count_is_the_number_of_tools(self) -> None:
        """WSPR-C31: it said eight when there were nine.

        Nobody asserted it, so it drifted the moment a tool was added — the
        same way ``get_version_info``'s shape drifted across the fleet.
        """
        instructions = server.mcp.instructions or ""
        count = len(_tools())
        assert f"{count} tools" in instructions, (
            f"instructions do not say '{count} tools'; got: {instructions[:120]}"
        )


class TestVersionInfoShape:
    def test_it_matches_the_shape_the_fleet_returns(self) -> None:
        """WSPR-C40: the convention that drifted because nobody asserted it."""
        info = _call("get_version_info")()
        assert info["service_name"] == "wspr-mcp"
        assert info["service_version"]
        assert info["spec_version"]


class TestRegistration:
    def test_the_nine_tools_a_client_sees(self) -> None:
        """WSPR-C50: calling a function directly is not proof a client can."""
        assert set(_tools()) == READS_WSPR_LIVE | ANSWERS_FROM_THE_PACKAGE
