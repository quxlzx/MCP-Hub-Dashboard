#!/usr/bin/env python3
"""MCP Hub Dashboard -- fleet status board.

A tiny stdlib-only HTTP server that probes a list of endpoints (HTTP or raw
TCP) and serves a single-page status board with up/down + latency,
auto-refreshing in the browser.

Works on Windows, Linux and macOS with any Python 3.8+. No dependencies.

Usage:
    python probe.py                  # serve on http://127.0.0.1:8787
    python probe.py --host 0.0.0.0   # serve on the LAN
    python probe.py --open           # also open the dashboard in a browser
    python probe.py --probe-only     # print status JSON to stdout and exit
    python probe.py --config my.json # use a different config file
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TIMEOUT = 4.0
USER_AGENT = "mcp-hub-dashboard/1.0"
CONFIG_PATH = os.path.join(HERE, "config.json")  # set from --config in main()


def load_config(path):
    """Load config.json; fall back to config.example.json with a warning."""
    if not os.path.exists(path):
        example = os.path.join(HERE, "config.example.json")
        if os.path.exists(example):
            print(f"[warn] {path} not found -- using config.example.json "
                  f"(copy it to config.json and edit)", file=sys.stderr)
            path = example
        else:
            print(f"[error] no config file at {path}", file=sys.stderr)
            sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg.get("endpoints"), list) or not cfg["endpoints"]:
        print("[error] config must contain a non-empty 'endpoints' list",
              file=sys.stderr)
        sys.exit(1)
    return cfg


def _split_host_port(url):
    """Parse 'host:port' (optionally with scheme) into (host, port)."""
    rest = url
    if "://" in rest:
        rest = rest.split("://", 1)[1]
    rest = rest.split("/", 1)[0]
    if rest.startswith("["):  # IPv6 literal [::1]:8080
        host, _, port = rest.partition("]")
        host = host[1:]
        port = port.lstrip(":")
    else:
        host, _, port = rest.rpartition(":")
    if not host:
        raise ValueError(f"missing host in {url!r}")
    return host, int(port if port else 80)


def probe(endpoint, timeout):
    """Probe one endpoint. Returns a result dict; never raises."""
    url = endpoint.get("url", "")
    kind = endpoint.get("kind") or ("http" if url.startswith("http") else "tcp")
    result = {
        "name": endpoint.get("name") or url,
        "url": url,
        "kind": kind,
        "note": endpoint.get("note", ""),
    }
    started = time.monotonic()
    try:
        if kind == "http":
            req = urllib.request.Request(
                url, method=endpoint.get("method", "GET"),
                headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result["status_code"] = resp.getcode()
        else:
            host, port = _split_host_port(url)
            with socket.create_connection((host, port), timeout=timeout):
                pass
        result["up"] = True
    except urllib.error.HTTPError as exc:
        # The server answered with an error code -- it is reachable, so we
        # count it as up but record the status code (e.g. a health endpoint
        # returning 503 while a model is still loading).
        result["up"] = True
        result["status_code"] = exc.code
    except Exception as exc:  # noqa: BLE001 -- report, never crash
        result["up"] = False
        result["error"] = str(exc) or type(exc).__name__
    result["latency_ms"] = round((time.monotonic() - started) * 1000, 1) \
        if result.get("up") else None
    return result


def run_probes(cfg):
    timeout = float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
    endpoints = cfg["endpoints"]
    workers = max(1, min(16, len(endpoints)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda ep: probe(ep, timeout), endpoints))
    up = sum(1 for r in results if r.get("up"))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "refresh_seconds": int(cfg.get("refresh_seconds", 10)),
        "summary": {"up": up, "total": len(results)},
        "endpoints": results,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 -- keep console quiet
        pass

    def _send(self, code, body, ctype):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 -- stdlib naming
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"),
                          encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as exc:
                self._send(500, f"index.html missing: {exc}", "text/plain")
        elif self.path == "/status":
            cfg = load_config(CONFIG_PATH)
            self._send(200, json.dumps(run_probes(cfg)),
                       "application/json; charset=utf-8")
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send(404, "not found", "text/plain")

    # ---- minimal MCP server (streamable HTTP, JSON responses) ----
    # Speaks just enough of the MCP spec for clients like Hermes:
    # initialize / notifications/initialized / tools/list / tools/call.

    MCP_TOOLS = [
        {
            "name": "fleet_status",
            "description": ("Probe all configured fleet endpoints now and "
                            "return up/down, latency and status codes as "
                            "JSON."),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "fleet_endpoints",
            "description": "List the configured endpoints without probing.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]

    def _mcp_result(self, rpc_id, result):
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": rpc_id,
                                    "result": result}),
                   "application/json")

    def _mcp_error(self, rpc_id, code, message):
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": rpc_id,
                                    "error": {"code": code,
                                              "message": message}}),
                   "application/json")

    def do_POST(self):  # noqa: N802 -- stdlib naming
        if self.path != "/mcp":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            msg = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._mcp_error(None, -32700, "parse error")
            return
        method, rpc_id = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            self._mcp_result(rpc_id, {
                "protocolVersion": params.get("protocolVersion",
                                              "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-hub-dashboard",
                               "version": "1.0.0"}})
        elif method in ("notifications/initialized", "initialized"):
            self.send_response(202)
            self.end_headers()
        elif method == "tools/list":
            self._mcp_result(rpc_id, {"tools": self.MCP_TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            cfg = load_config(CONFIG_PATH)
            if name == "fleet_status":
                text = json.dumps(run_probes(cfg))
            elif name == "fleet_endpoints":
                text = json.dumps(cfg.get("endpoints", []))
            else:
                self._mcp_error(rpc_id, -32602, f"unknown tool {name!r}")
                return
            self._mcp_result(rpc_id, {"content": [{"type": "text",
                                                   "text": text}],
                                      "isError": False})
        elif method == "ping":
            self._mcp_result(rpc_id, {})
        else:
            if rpc_id is None:  # notification
                self.send_response(202)
                self.end_headers()
            else:
                self._mcp_error(rpc_id, -32601, f"unknown method {method!r}")


def main():
    ap = argparse.ArgumentParser(description="MCP Hub Dashboard")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; use 0.0.0.0 for LAN)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--probe-only", action="store_true",
                    help="print status JSON and exit (no server)")
    ap.add_argument("--open", action="store_true",
                    help="open the dashboard in the default browser")
    args = ap.parse_args()

    global CONFIG_PATH
    CONFIG_PATH = args.config
    cfg = load_config(CONFIG_PATH)
    if args.probe_only:
        print(json.dumps(run_probes(cfg), indent=2))
        return

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{args.port}"
    print(f"MCP Hub Dashboard serving at {url}  (Ctrl+C to stop)")
    if args.open:
        import threading
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
