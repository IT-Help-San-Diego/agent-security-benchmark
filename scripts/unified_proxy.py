#!/usr/bin/env python3
"""
Unified Control Panel Reverse Proxy — stdlib only, zero dependencies.

Puts ONE origin (http://127.0.0.1:8080/) in front of all three real backends,
so a single Chrome "Install as App" / PWA window can navigate between them
without ever breaking out to a new browser window. This is the standard
reverse-proxy pattern (what nginx/Caddy do) -- it forwards requests, it does
NOT modify a single line of any backend's code, including Hermes's own
dashboard (which we must never touch directly).

Routing:
  /                  -> moderation_server.py   (127.0.0.1:8765/)       Pending Queue
  /audit             -> moderation_server.py   (127.0.0.1:8765/audit)  Audit Trail
  /dashboard         -> moderation_server.py   (127.0.0.1:8765/dashboard) Model Fleet
  /hermes/*          -> Hermes Agent Dashboard (127.0.0.1:9119/*)      Real Hermes app,
                        untouched, just proxied under a path prefix so it stays
                        same-origin inside the PWA shell.
  everything else    -> 404

Why this is safe for the Hermes dashboard specifically: we forward bytes
verbatim (headers rewritten only for Host/Location), we never edit Hermes's
templates, static assets, or Python. If Hermes updates, this proxy keeps
working unchanged -- it has zero coupling to Hermes's internals.

Usage:
  python3 unified_proxy.py          # listens on :8080
  Then Chrome: chrome://apps -> "Create Shortcut" for http://127.0.0.1:8080/
  (or Settings -> Install <site> as app) -- one Dock icon, one window,
  all four views reachable via the top nav without ever popping a new window.
"""
import http.server, socketserver, urllib.request, urllib.error, re

PORT = 8767
BACKENDS = {
    "moderation": "127.0.0.1:8765",
    "hermes": "127.0.0.1:9119",
}

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _proxy(self, method):
        path = self.path
        if path.startswith("/hermes"):
            backend = BACKENDS["hermes"]
            upstream_path = path[len("/hermes"):] or "/"
        else:
            backend = BACKENDS["moderation"]
            upstream_path = path

        url = f"http://{backend}{upstream_path}"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        req_headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
        req = urllib.request.Request(url, data=body, method=method, headers=req_headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                resp_body = resp.read()
                resp_headers = resp.headers
        except urllib.error.HTTPError as e:
            status = e.code
            resp_body = e.read()
            resp_headers = e.headers
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy error reaching {url}: {e}".encode())
            return

        # Rewrite absolute Location redirects and any hermes-prefixed internal
        # links so navigation stays inside the single proxied origin.
        content_type = resp_headers.get("Content-Type", "")
        if path.startswith("/hermes") and "text/html" in content_type:
            text = resp_body.decode("utf-8", errors="replace")
            # Rewrite root-relative links Hermes's own dashboard emits (href="/..."
            # or src="/...") so they stay under the /hermes prefix in this proxy.
            text = re.sub(r'(href|src)="(/(?!hermes)[^"]*)"', r'\1="/hermes\2"', text)
            resp_body = text.encode("utf-8")

        self.send_response(status)
        for k, v in resp_headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def do_HEAD(self):
        self._proxy("HEAD")

def main():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
    with ReusableServer(("127.0.0.1", PORT), ProxyHandler) as httpd:
        print(f"Unified control panel proxy running at http://127.0.0.1:{PORT}/")
        print(f"  /          -> Pending Queue")
        print(f"  /audit     -> Audit Trail")
        print(f"  /dashboard -> Model Fleet Dashboard")
        print(f"  /hermes/*  -> Hermes Agent Dashboard (proxied, untouched)")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
