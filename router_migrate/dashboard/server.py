"""The dashboard server's hardening, shared by the live dashboard (items.py, which adds the routes): a stdlib
http.server bound to 127.0.0.1 in a daemon thread.

- a random session token (secrets.token_urlsafe) is required as `?t=` on GET / and as header `X-RM-Token` on every /api/*;
- a Host header other than 127.0.0.1:<port> / localhost:<port> is rejected (DNS rebinding), and an Origin header, when
  present, other than http://127.0.0.1:<port> / http://localhost:<port>;
- request bodies are capped at 64 KiB and must be JSON; methods other than GET and POST are 405.

Status codes: 403 auth/host/origin failures, 413 oversize, 400 bad JSON, 415 non-JSON content type. (The v5 dashboard's
own routes and `serve` were archived: archive/router_migrate/dashboard/.)
"""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


class HttpError(Exception):
    def __init__(self, status, msg):
        Exception.__init__(self, msg)
        self.status, self.msg = status, msg


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "router-migrate"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # never log request lines (they carry the token)
            pass

        # ---- responses ----
        def _send(self, status, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, default=str), "application/json; charset=utf-8")

        def _error(self, e):
            self.close_connection = True
            self._json(e.status, {"ok": False, "error": e.msg})

        # ---- checks ----
        def _check_host_origin(self):
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in app.allowed_hosts:
                raise HttpError(403, "forbidden host")
            origin = self.headers.get("Origin")
            if origin is not None and origin.strip().lower() not in app.allowed_origins:
                raise HttpError(403, "forbidden origin")

        def _check_token(self, given):
            if not given or not hmac.compare_digest(given.encode("utf-8"), app.token.encode("utf-8")):
                raise HttpError(403, "missing or invalid session token")

        def _read_json(self):
            if self.headers.get("Transfer-Encoding"):
                raise HttpError(400, "chunked bodies are not accepted")
            raw_len = self.headers.get("Content-Length")
            try:
                n = int(raw_len) if raw_len is not None else 0
            except ValueError:
                raise HttpError(400, "bad Content-Length")
            if n < 0:
                raise HttpError(400, "bad Content-Length")
            if n > MAX_BODY:
                raise HttpError(413, "request body over %d bytes" % MAX_BODY)
            body = self.rfile.read(n) if n else b""
            if not body:
                return {}
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise HttpError(415, "Content-Type must be application/json")
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise HttpError(400, "body is not valid JSON")

        def _not_allowed(self):
            try:
                self._check_host_origin()
                raise HttpError(405, "method not allowed")
            except HttpError as e:
                self._error(e)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _not_allowed

    return Handler


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
