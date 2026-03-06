<!-- mcp-name: io.github.qso-graph/wspr-mcp -->
# wspr-mcp

MCP server for [WSPR](https://www.wsprnet.org/) (Weak Signal Propagation Reporter) beacon data — live spots, band activity, top beacons, propagation paths, SNR trends, and more through any MCP-compatible AI assistant.

Data from [wspr.live](https://wspr.live/) (~2.7 billion spots, 2008-present). Part of the [qso-graph](https://qso-graph.io/) project. **No authentication required** — all public data.

## Install

```bash
pip install wspr-mcp
```

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `wspr_spots` | Recent WSPR spots with flexible filtering | callsign, band, hours, grid, min/max SNR, min distance |
| `wspr_band_activity` | Per-band spot counts, station counts, distances, and SNR | hours |
| `wspr_top_beacons` | Top transmitters ranked by spot count or max distance | band, hours, sort_by (spots/distance), limit |
| `wspr_top_spotters` | Top receivers ranked by spot count or max distance | band, hours, sort_by (spots/distance), limit |
| `wspr_propagation` | Propagation between two locations (callsign or grid) | tx, rx, band, hours |
| `wspr_grid_activity` | All WSPR activity in/out of a Maidenhead grid square | grid (2 or 4 char), band, hours |
| `wspr_longest_paths` | Longest distance WSPR paths in a time window | band, hours, min_distance, limit |
| `wspr_snr_trend` | Hourly SNR trend for a specific path over time | tx, rx, band, hours |

## What is WSPR?

WSPR beacons transmit a 2-minute encoded signal at very low power (typically 200 mW to 5 W). Each decoded spot proves a propagation path exists between two locations on a specific band. With thousands of beacons worldwide transmitting 24/7, WSPR provides continuous, automated propagation monitoring across all HF bands.

## Good Neighbour Policy

wspr.live is a volunteer-run service that mirrors all wsprnet.org data into a public ClickHouse database. We take our responsibility as a good neighbour seriously:

| Measure | Detail |
|---------|--------|
| **Rate limiting** | 3 seconds between requests (20 req/min max) |
| **Circuit breaker** | Opens after 3 consecutive failures; exponential backoff up to 5 minutes. Prevents hammering a struggling service. |
| **Time-bounded queries** | Every query filters by time (max 72 hours). No unbounded full-table scans. |
| **Band filtering** | Queries filter by band whenever the user provides one — this hits wspr.live's indexes efficiently. |
| **Column selection** | We SELECT only the columns each tool needs (8-10 per query), never `SELECT *`. |
| **Result limits** | All queries cap results (200 spots, 50 leaderboard entries). |
| **Response caching** | 2-10 minute TTL per tool. Identical queries within the window hit local cache with zero network traffic. |
| **Request timeout** | 20-second timeout — we don't hold connections open on a shared service. |
| **User-Agent header** | Every request identifies itself as `wspr-mcp/{version}` so the operators can reach us if needed. |

If wspr.live is down or overloaded, the circuit breaker backs off automatically. We don't retry in a tight loop.

## Quick Start

No credentials needed — just install and configure your MCP client.

### Configure your MCP client

#### Claude Desktop

Add to `claude_desktop_config.json` (`~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

#### Claude Code

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

#### ChatGPT Desktop

```json
{
  "mcpServers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

#### Cursor

Add to `.cursor/mcp.json` (project-level) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

#### VS Code / GitHub Copilot

Add to `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

#### Gemini CLI

Add to `~/.gemini/settings.json` (global) or `.gemini/settings.json` (project):

```json
{
  "mcpServers": {
    "wspr": {
      "command": "wspr-mcp"
    }
  }
}
```

### Ask questions

> "Show me recent WSPR spots on 20m"

> "What bands are open right now?"

> "Who are the top WSPR beacons on 20m today?"

> "What are the longest WSPR paths in the last 24 hours?"

> "Is there propagation between Idaho (DN13) and central Europe (JN48)?"

> "What's happening in grid square JN48?"

> "How's the SNR trending between K9AN and G8JNJ on 20m?"

> "Who are the best WSPR receivers sorted by distance?"

## Testing Without Network

```bash
WSPR_MCP_MOCK=1 wspr-mcp
```

## MCP Inspector

```bash
wspr-mcp --transport streamable-http --port 8009
```

## Development

```bash
git clone https://github.com/qso-graph/wspr-mcp.git
cd wspr-mcp
pip install -e .
```

## License

GPL-3.0-or-later
