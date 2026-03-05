# wspr-mcp

MCP server for [WSPR](https://www.wsprnet.org/) (Weak Signal Propagation Reporter) beacon data — live spots, callsign activity, per-band activity, longest paths, and grid-to-grid propagation analysis through any MCP-compatible AI assistant.

Part of the [qso-graph](https://qso-graph.io/) project. **No authentication required** — all public data.

## Install

```bash
pip install wspr-mcp
```

## Tools

| Tool | Description |
|------|-------------|
| `wspr_spots` | Recent WSPR spots with callsign/band filters |
| `wspr_activity` | TX/RX activity summary for a callsign |
| `wspr_band_activity` | Per-band spot counts, station counts, and average distance |
| `wspr_top_paths` | Longest/best WSPR paths in the last 24 hours |
| `wspr_propagation` | WSPR-derived propagation between two grid squares |

## What is WSPR?

WSPR beacons transmit a 2-minute encoded signal at very low power (typically 200 mW to 5 W). Each decoded spot proves a propagation path exists between two locations on a specific band. With thousands of beacons worldwide transmitting 24/7, WSPR provides continuous, automated propagation monitoring across all HF bands.

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

> "What's KI7MT's WSPR activity?"

> "Which bands have the most WSPR activity right now?"

> "What are the longest WSPR paths in the last 24 hours?"

> "Is there propagation between Idaho (DN13) and central Europe (JN48)?"

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
