# wspr-mcp

MCP server for WSPR beacon data analytics -- band openings, path analysis, and solar correlation from 10.8 billion WSPR spots.

Part of the [qso-graph](https://qso-graph.io/) collection of amateur radio MCP servers.

## Planned Tools

| Tool | Description |
|------|-------------|
| `wspr_band_openings` | Hour-by-hour propagation for a path on a band |
| `wspr_path_analysis` | Complete path analysis across bands, hours, months |
| `wspr_solar_correlation` | SFI effect on propagation by band |
| `wspr_beacon_activity` | Beacon activity and coverage maps |
| `wspr_distance_analysis` | Distance vs SNR analysis for propagation studies |

## Install

Coming soon. This package is not yet published to PyPI.

```bash
pip install wspr-mcp
```

## Data Source

Data source TBD. May use SQLite datasets distributed via SourceForge (similar to [ionis-mcp](https://github.com/IONIS-AI/ionis-mcp)).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
