# Changelog

All notable changes to `wspr-mcp` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] — 2026-10-26

- A release should appear on the Releases page
- Move every Node 20 action to its first Node 24 major
- The locked-dependency audit blocked every release
- Four upgrades arrived as 57 pull requests
- Nothing was auditing the lockfile
- Close every open security advisory in the lockfile
- A release is a workflow, not a merge
- Bump certifi from 2026.7.22 to 2026.10.19
- Make the publish gate's http:// check portable
- Assert that certifi is declared, not merely imported

## [0.4.0] — 2026-10-12

**Upgrading:** a tool that fails now returns `isError: true` instead of a successful result whose
payload contained an `error` key. Anything reading `result["error"]` should read the error flag.
No decision changed — a query matching nothing is still a successful answer with `total: 0`, and a
quiet band stays a result rather than becoming a failure.


- **`E501` is adopted** (#21, qso-graph-devel#70). It was deliberately ignored while the rules that
  find defects went in first; this is the pass of its own that deferral was waiting for. It reported
  **five lines in this repo, none of them a defect**, and all five reflowed — **no exemption was
  needed here**, unlike `adif-mcp` and `solar-mcp`, because no line is long by virtue of naming a
  publisher's field. Formatting and config only; no behaviour change.

- **`certifi` is carried, and the query verifies against it** (#20, qso-graph-devel#73). The query
  used `urllib` with no context, so it trusted whichever root store the running Python was built
  against. On macOS that is `/etc/ssl/cert.pem`, a deliberately *reduced* OpenSSL file — 128 roots
  against the system keychain's 158, and current rather than stale. A certificate chaining to one
  of the 30 it omits fails for Python while `curl` to the same URL succeeds. That is what stopped
  `meteor-mcp` reaching NASA. **`db1.wspr.live` verifies fine against the reduced file today**, so
  this was latent rather than an outage — and it would have been worse than elsewhere, because a
  failed query also trips the circuit breaker: the server would have kept refusing for the
  breaker's window with nothing pointing at trust as the cause.
- **Verification is never disabled.** `ssl.CERT_NONE` or `check_hostname = False` would also have
  made such a call "work", by accepting any certificate from anyone. Three static checks hold this
  in `test_security.py` and two runtime ones in `test_tools.py`, split that way because the release
  gate runs the first file with the package **not installed**.
- **`tests/test_release_gate.py`** asserts that precondition rather than leaving it to be
  remembered: the gate suite may import nothing but the standard library and pytest. solar-mcp's
  0.3.0 publish failed on exactly that, from the same change.
- `ssl.SSLError` is handled alongside `URLError`, and **`urllib.error` is imported** rather than
  relied on as a side effect of importing `urllib.request` — it was already being caught.

- **Tool execution errors are flagged `isError`** (#22). Every tool returned `{"error": "..."}` on
  failure, which reaches a client as a *successful* call whose payload happens to contain the word
  "error". The specification says why that matters: otherwise "the LLM would not be able to see
  that an error occurred and self-correct". They now raise `ToolError`, which `fastmcp` reports as
  `isError: true` with the message intact. **No decision changed — only how it is reported.** A
  query matching nothing is still a successful answer with `total: 0`; a quiet band is a result,
  not a failure.
- **Every tool declares all four annotation hints** (#22). Declaring nothing is not neutral: the
  defaults advertise a tool as `destructiveHint: true`, `openWorldHint: true` and not idempotent,
  and the specification tells clients to keep a human in the loop for operations like that — so a
  client was entitled to put a confirmation prompt in front of reading a beacon spot. All nine are
  read-only, non-destructive and idempotent; `openWorldHint` is read per tool, true for the eight
  that query `db1.wspr.live` and false for `get_version_info`, which reports what the wheel
  carries. Annotations are a client's UX hint and never a security control.
- **Fixed: the instructions said eight tools when there were nine.** The count was written when
  there were eight and never moved. A client reads the instructions before calling anything, and
  nobody asserted this, so it drifted the moment a tool was added.
- **`tests/test_mcp_conformance.py`** asserts all of the above, following `adif-mcp`'s file of the
  same name — including, for this repo specifically, that **the request limit named in the
  instructions is the one the client enforces**. CLAUDE.md makes wspr-mcp the fleet's pattern for
  honouring and naming a provider's stated limit; wspr.live states 20 req/min and `_MIN_DELAY` is
  3.0 seconds, and the test holds the two together so the sentence a user reads stays true.

## [0.3.6] — 2026-10-11

- **ruff and mypy run in CI** (qso-graph-devel#66), as a job the `ci-all-green` gate requires.
  Settings follow `adif-mcp`, the reference for every qso-graph Python repo, rather than a style of
  this repo's own. They run once rather than per Python version: both read the source, and neither
  answer changes with the interpreter.
- Both path-band lists are annotated. Their rows mix strings, ints, floats and lists, so an
  unannotated comprehension infers `dict[str, object]` and the `total_spots` sum beneath it cannot
  read its own rows. The ClickHouse row list is typed where it leaves `json.loads`.
- `E501` is deferred rather than adopted (qso-graph-devel#70): what it reports in these repos are
  widths, not defects, and some lines are long because they name a publisher's field exactly.
- `mcp.run` is given the literal fastmcp asks for rather than a `str` that happens to hold the
  right word.
- **`fastmcp` is bounded: `>=4.0,<5`** (qso-graph-devel#60). It was `>=3.0` with no upper bound, and
  these servers are run with `uvx`, which resolves fresh — so a `fastmcp` 5.0 would have reached
  every user automatically, before anything here had been run against it. The floor rises to 4.0
  because that is what is actually tested: every lock in the fleet held a 4.x, and nothing in CI
  has ever exercised 3.x. A claim of 3.x support that no test backs is not support.
- `fastmcp` is locked at 4.1.0, the current release, so CI runs against what a new install gets.
- **The published contact is `maintainers@qso-graph.io`** (qso-graph-devel#69). The `authors` field
  carried a personal address, and that field is what PyPI shows on the package page. Everything in
  qso-graph is open source and open to contribution, so the contact is the project's.

## [0.3.5] — 2026-10-07

- LICENSE: the full GPL-3.0 text. The file held only its opening and a link, so GitHub detected no licence.

## [0.3.4] — 2026-10-06

- PyPI: the Documentation link goes to this package's own page, https://qso-graph.io/servers/wspr/ (qso-graph/.github#15).
- CI: the release flow (qso-graph/.github TEMPLATES.md). Work lands on `develop`; a release is a
  PR from `develop` into `main`, and merging it publishes to PyPI and the MCP Registry, verifies both
  and tags the release. CI runs on `develop` too, and PRs into `main` must come from `develop` or a
  `security/` branch.

## [0.3.3] — 2026-09-28

### Added (CI hygiene)

- **MCP Registry sync** — `publish.yml` publishes to the [Official MCP Registry](https://registry.modelcontextprotocol.io)
  after each PyPI publish, using GitHub OIDC for auth. Triggered on
  `v*` tag push; no manual steps. The Registry job waits until PyPI
  serves the version, and retries. Pattern documented in
  [qso-graph/.github/TEMPLATES.md](https://github.com/qso-graph/.github/blob/main/TEMPLATES.md).
- **Registry version badge** in README — PyPI and Registry versions
  are visible side-by-side so any drift between publishing surfaces
  is immediately apparent.
- **Release gates** — the tag must match `pyproject.toml`, and a
  `verify` job fails the release unless PyPI and the MCP Registry
  both serve the new version.

### Fixed

- The Official MCP Registry listed wspr-mcp at 0.1.1. This release brings it current.

## [0.3.2] — 2026-05-15

### Added
- New tool `get_version_info` — returns `{service_name, service_version, spec_version}`
  for fleet identity attestation. Lets agents detect version drift across MCP
  deployments without going outside the protocol. Tracks
  [IONIS-AI/ionis-devel#49](https://github.com/IONIS-AI/ionis-devel/issues/49)
  (fleet rollout).
- `__spec_version__` constant in package `__init__.py`, pinned to `wspr-live-v1`
  for the current wspr.live ClickHouse schema.
- L2 unit tests WSPR-L2-046 through WSPR-L2-050 covering the new tool.
- `.github/workflows/ci.yml` — PR-gating CI workflow (py3.10-3.13 matrix,
  unit + security tests, ci-all-green aggregator).

### Changed
- `__init__.py` modernized to mirror the `adif-mcp`/`solar-mcp` pattern
  (`Final` types, explicit `PackageNotFoundError` handling).

## [0.3.1] — Previous release
- See git history for changes prior to the changelog being introduced.
