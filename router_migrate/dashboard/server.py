"""Local dashboard server (stdlib http.server in a daemon thread). SPEC_V2 hard design rule 10.

Security:
- binds 127.0.0.1 only;
- a random session token (secrets.token_urlsafe) is required as `?t=` on GET / and as header `X-RM-Token` on every /api/*;
- rejects a Host header other than 127.0.0.1:<port> / localhost:<port> (DNS rebinding) and an Origin header, when present,
  other than http://127.0.0.1:<port> / http://localhost:<port>;
- request bodies are capped at 64 KiB and must be JSON;
- endpoints: GET /, GET /api/bundle, POST /api/decide, POST /api/apply. Nothing else.

Status codes: 403 auth/host/origin failures, 413 oversize, 400 bad JSON or payload, 415 non-JSON content type,
404 unknown path, 405 other methods, 500 callback error.
"""
import hmac
import json
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .render import render_page

MAX_BODY = 64 * 1024
ACTIONS = ("approve", "dismiss", "postpone", "shadow_first")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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


def validate_decision(p):
    if not isinstance(p, dict):
        raise HttpError(400, "body must be a JSON object")
    extra = set(p) - {"card_id", "action", "reason", "until"}
    if extra:
        raise HttpError(400, "unknown field(s): %s" % ", ".join(sorted(extra)))
    cid, action = p.get("card_id"), p.get("action")
    if not isinstance(cid, str) or not cid or len(cid) > 200:
        raise HttpError(400, "card_id must be a non-empty string")
    if action not in ACTIONS:
        raise HttpError(400, "action must be one of %s" % ", ".join(ACTIONS))
    reason, until = p.get("reason"), p.get("until")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
        raise HttpError(400, "reason must be a string (<= 2000 chars)")
    if until is not None and (not isinstance(until, str) or not DATE_RE.match(until)):
        raise HttpError(400, "until must be YYYY-MM-DD")
    if action == "dismiss" and not (reason or "").strip():
        raise HttpError(400, "dismiss needs a reason")
    if action == "postpone" and not until:
        raise HttpError(400, "postpone needs until (YYYY-MM-DD)")
    out = {"card_id": cid, "action": action}
    if reason is not None and reason.strip():
        out["reason"] = reason.strip()
    if until is not None:
        out["until"] = until
    return out


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

        # ---- routing ----
        def do_GET(self):
            try:
                self._check_host_origin()
                u = urlsplit(self.path)
                if u.path == "/":
                    self._check_token((parse_qs(u.query).get("t") or [""])[0])
                    self._send(200, render_page(app.get_bundle(), "live"), "text/html; charset=utf-8")
                elif u.path == "/api/bundle":
                    self._check_token(self.headers.get("X-RM-Token"))
                    self._json(200, app.get_bundle())
                elif u.path.startswith("/api/"):
                    self._check_token(self.headers.get("X-RM-Token"))
                    raise HttpError(405 if u.path in ("/api/decide", "/api/apply") else 404, "not found")
                else:
                    raise HttpError(404, "not found")
            except HttpError as e:
                self._error(e)
            except Exception as e:  # callback failure
                self._error(HttpError(500, "%s: %s" % (type(e).__name__, e)))

        def do_POST(self):
            try:
                self._check_host_origin()
                u = urlsplit(self.path)
                if not u.path.startswith("/api/"):
                    raise HttpError(404 if u.path != "/" else 405, "not found")
                self._check_token(self.headers.get("X-RM-Token"))
                if u.path not in ("/api/decide", "/api/apply"):
                    raise HttpError(404 if u.path != "/api/bundle" else 405, "not found")
                payload = self._read_json()
                if u.path == "/api/decide":
                    p = validate_decision(payload)
                    try:
                        result = app.on_decide(p)
                    except (ValueError, KeyError) as e:
                        raise HttpError(400, str(e) or type(e).__name__)
                    self._json(200, {"ok": True, "result": result})
                else:
                    if not isinstance(payload, dict) or payload:
                        raise HttpError(400, "apply takes an empty JSON object")
                    result = app.on_apply()
                    cmd = result.get("command") if isinstance(result, dict) else result
                    self._json(200, {"ok": True, "command": cmd, "result": result})
            except HttpError as e:
                self._error(e)
            except Exception as e:
                self._error(HttpError(500, "%s: %s" % (type(e).__name__, e)))

        def _not_allowed(self):
            try:
                self._check_host_origin()
                raise HttpError(405, "method not allowed")
            except HttpError as e:
                self._error(e)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _not_allowed

    return Handler


class _App(object):
    def __init__(self, get_bundle, on_decide, on_apply, token):
        self.get_bundle, self.on_decide, self.on_apply, self.token = get_bundle, on_decide, on_apply, token
        self.allowed_hosts = set()
        self.allowed_origins = set()

    def bind_port(self, port):
        self.allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        self.allowed_origins = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(get_bundle, on_decide, on_apply, host="127.0.0.1", port=0, open_browser=True):
    """Start the dashboard. Returns (server, url); url carries the session token. Stop with server.shutdown().

    get_bundle() -> dict (the window.__RM__ bundle); on_decide({card_id, action, reason?, until?}) -> JSON-able result;
    on_apply() -> JSON-able result, ideally {"command": "router-migrate apply --decisions ...", ...} (echoed to the UI).
    """
    if host != "127.0.0.1":
        raise ValueError("the dashboard binds 127.0.0.1 only")
    token = secrets.token_urlsafe(32)
    app = _App(get_bundle, on_decide, on_apply, token)
    httpd = DashboardServer(("127.0.0.1", int(port)), _make_handler(app))
    real_port = httpd.server_address[1]
    app.bind_port(real_port)
    url = "http://127.0.0.1:%d/?t=%s" % (real_port, token)
    httpd.token, httpd.url, httpd.port = token, url, real_port
    t = threading.Thread(target=httpd.serve_forever, name="router-migrate-dashboard", daemon=True)
    t.start()
    httpd.thread = t
    _orig_shutdown = httpd.shutdown

    def shutdown():
        _orig_shutdown()
        httpd.server_close()

    httpd.shutdown = shutdown
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return httpd, url
