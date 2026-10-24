# MCP Hub Dashboard

A single-page status board for a fleet of self-hosted services (MCP servers,
inference endpoints, anything with a port). One HTML file + one Python probe
script, **stdlib only** — no dependencies, no build step, no CDN.

- Pings every endpoint in parallel (HTTP `GET` or raw TCP connect)
- Shows **up / down + latency** per endpoint, with a rolling latency sparkline
- Auto-refreshes in the browser (interval configurable)
- Works on **Windows, Linux and macOS** with any Python 3.8+

## Quick start

```bash
cp config.example.json config.json   # edit with your own endpoints
python probe.py                      # serves http://127.0.0.1:8787
```

Open <http://127.0.0.1:8787> in a browser. To view it from other machines on
your network: `python probe.py --host 0.0.0.0`.

Useful flags:

| Flag | What it does |
|---|---|
| `--host 0.0.0.0` | serve on the LAN instead of loopback only |
| `--port 8787` | change the dashboard port |
| `--open` | auto-open the page in your default browser |
| `--probe-only` | print the status JSON to stdout and exit (cron-friendly) |
| `--config other.json` | point at a different config file |

## Config

`config.json` (git-ignored — it contains your private hosts/IPs):

```json
{
  "refresh_seconds": 10,
  "timeout_seconds": 4,
  "endpoints": [
    { "name": "MCP Core",        "url": "http://HUB_HOST:8090/health" },
    { "name": "MCP Replica A",   "url": "HUB_HOST:8110", "kind": "tcp" },
    { "name": "Inference",       "url": "http://GPU_HOST:8080/health",
      "note": "DOWN while the model is still loading" }
  ]
}
```

- `kind` is inferred: URLs starting with `http` get an HTTP probe, everything
  else (`host:port`) gets a TCP connect probe. Override with `"kind": "http"`
  or `"kind": "tcp"`.
- An HTTP endpoint that answers with `4xx/5xx` counts as **up** (the server
  is reachable) and shows its status code.
- `note` is displayed on the card — handy for known quirks.

## MCP server mode

The same port also speaks **MCP** (streamable HTTP at `/mcp`, stdlib-only —
no pip installs). Agents that support HTTP MCP servers get two tools:

- `fleet_status` — probe everything now, returns up/down + latency JSON
- `fleet_endpoints` — list the configured endpoints without probing

Register with an MCP client (e.g. Hermes):

```bash
hermes config set mcp_servers.hub-dashboard.url http://DASHBOARD_HOST:8787/mcp
```

## Gotchas

- **Loopback-only services read as DOWN.** A service bound to `127.0.0.1`
  (docker loopback mapping, hardcoded bind) is invisible to probes from other
  machines. Run the dashboard on the same machine as those services, or
  tunnel (`ssh -L 9999:127.0.0.1:9999 host`) and probe the tunnel.
- **Big models load slowly.** llama.cpp-style servers keep the port closed for
  minutes after the process starts; a DOWN card during a restart is expected.
- The dashboard server itself is unauthenticated — only bind `0.0.0.0` on a
  trusted network.

## Privacy

`config.json` is git-ignored on purpose. The repo ships only
`config.example.json` with placeholders — no hosts, IPs, tokens or keys.

## License

MIT
