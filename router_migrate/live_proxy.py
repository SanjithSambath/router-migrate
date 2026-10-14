"""A recording pass-through to the real Router for `check --run CMD --live`.

The command's runtime is pointed at this local proxy (RAMP_ROUTER_BASE_URL), which forwards every request unchanged to
Router and records path, status, model, feature label, serving provider and error message. Responses are buffered (a
streamed answer arrives whole), which is fine for checks. The migration key only ever lives in the command's environment
and the forwarded Authorization header; nothing here prints or stores it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTER = "https://api.router.com"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
       "upgrade", "content-length", "content-encoding", "host"}


def serve(upstream=None, timeout=600):
    """(server, base_url, records): a running proxy on 127.0.0.1 and the list it appends one dict per request to."""
    import os
    import httpx
    upstream = (upstream or os.environ.get("ROUTER_MIGRATE_LIVE_UPSTREAM") or ROUTER).rstrip("/")
    records = []
    lock = threading.Lock()
    client = httpx.Client(timeout=timeout)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _forward(self, method):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            rec = {"method": method, "path": self.path, "feature": self.headers.get("x-router-migrate-feature"),
                   "client": self.headers.get("x-router-migrate-client")}
            try:
                j = json.loads(body or b"{}")
                rec["model"] = j.get("model") if isinstance(j, dict) else None
                rec["stream"] = bool(isinstance(j, dict) and j.get("stream"))
            except ValueError:
                rec["model"] = None
            try:
                r = client.request(method, upstream + self.path, content=body, headers=hdrs)
                data = r.content
                rec.update(status=r.status_code, provider=r.headers.get("x-router-provider"))
                if r.status_code >= 400:
                    try:
                        e = r.json().get("error")
                        rec["error"] = (e.get("message") if isinstance(e, dict) else str(e))[:300]
                    except ValueError:
                        rec["error"] = data[:300].decode("utf-8", "replace")
                self.send_response(r.status_code)
                for k, v in r.headers.items():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as ex:  # upstream unreachable: the command sees a 502, the record says why
                rec.update(status=502, error="proxy: %s" % str(ex)[:200])
                msg = json.dumps({"error": {"message": rec["error"]}}).encode()
                self.send_response(502)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            with lock:
                records.append(rec)

        def do_POST(self):
            self._forward("POST")

        def do_GET(self):
            self._forward("GET")

        def do_DELETE(self):
            self._forward("DELETE")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], records
