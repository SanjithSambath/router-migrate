"""Dashboard: hardened local server (rule 10) and the frozen REPORT.html renderer."""
import copy
import http.client
import json
import os
import re
from urllib.parse import urlsplit

import pytest

from router_migrate.dashboard import render_report, serve
from router_migrate.dashboard.render import PLACEHOLDER, page_template, render_page, safe_json
from router_migrate.dashboard.server import MAX_BODY

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "dashboard_bundle.json")


@pytest.fixture(scope="module")
def bundle():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def srv(bundle):
    calls = {"decide": [], "apply": 0}

    def on_decide(p):
        calls["decide"].append(p)
        if p["card_id"] == "boom":
            raise ValueError("unknown card")
        return {"saved": p["card_id"], "action": p["action"]}

    def on_apply():
        calls["apply"] += 1
        return {"command": "router-migrate apply --decisions /tmp/repo", "exit": 0, "output": "applied 2 cards"}

    server, url = serve(lambda: bundle, on_decide, on_apply, open_browser=False)
    u = urlsplit(url)
    ctx = {"server": server, "url": url, "port": u.port, "token": server.token, "calls": calls,
           "host": "127.0.0.1:%d" % u.port, "origin": "http://127.0.0.1:%d" % u.port}
    yield ctx
    server.shutdown()


def req(ctx, method, path, body=None, headers=None, host=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", ctx["port"], timeout=5)
    h = {"Host": host or ctx["host"]}
    if body is not None:
        raw = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if raw is not None:
        h["Content-Length"] = str(len(raw))
    h.update(headers or {})
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        conn.putheader(k, v)
    conn.endheaders()
    if raw:
        try:
            conn.send(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data, r


def auth(ctx, extra=None):
    h = {"X-RM-Token": ctx["token"]}
    h.update(extra or {})
    return h


# ---------------- server ----------------

def test_url_binds_loopback_with_token(srv):
    assert srv["url"].startswith("http://127.0.0.1:%d/?t=" % srv["port"])
    assert srv["server"].server_address[0] == "127.0.0.1"
    assert len(srv["token"]) >= 32
    with pytest.raises(ValueError):
        serve(dict, dict, dict, host="0.0.0.0", open_browser=False)


def test_get_page_requires_token(srv):
    assert req(srv, "GET", "/")[0] == 403
    assert req(srv, "GET", "/?t=wrong")[0] == 403
    st, data, r = req(srv, "GET", "/?t=" + srv["token"])
    assert st == 200
    assert r.getheader("Content-Type").startswith("text/html")
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    assert r.getheader("Referrer-Policy") == "no-referrer"
    html = data.decode()
    assert 'window.__RM_MODE__ = "live"' in html
    assert srv["token"] not in html  # the page reads the token from its own URL


def test_api_requires_token_header(srv):
    for method, path, body in (("GET", "/api/bundle", None), ("POST", "/api/decide", {"card_id": "x", "action": "approve"}),
                               ("POST", "/api/apply", {})):
        assert req(srv, method, path, body=body)[0] == 403
        assert req(srv, method, path, body=body, headers={"X-RM-Token": "nope"})[0] == 403
    # token in the query string does not authorize the API
    assert req(srv, "GET", "/api/bundle?t=" + srv["token"])[0] == 403
    assert srv["calls"] == {"decide": [], "apply": 0}


def test_bundle_endpoint(srv, bundle):
    st, data, r = req(srv, "GET", "/api/bundle", headers=auth(srv))
    assert st == 200 and r.getheader("Content-Type").startswith("application/json")
    assert json.loads(data)["repo"] == bundle["repo"]


def test_foreign_origin_rejected(srv):
    for origin in ("http://evil.example", "http://127.0.0.1:1", "https://127.0.0.1:%d" % srv["port"], "null"):
        assert req(srv, "GET", "/api/bundle", headers=auth(srv, {"Origin": origin}))[0] == 403
        assert req(srv, "POST", "/api/decide", body={"card_id": "c", "action": "approve"}, headers=auth(srv, {"Origin": origin}))[0] == 403
    assert srv["calls"]["decide"] == []
    for origin in (srv["origin"], "http://localhost:%d" % srv["port"]):
        assert req(srv, "GET", "/api/bundle", headers=auth(srv, {"Origin": origin}))[0] == 200


def test_foreign_host_rejected(srv):
    for host in ("evil.example", "evil.example:%d" % srv["port"], "127.0.0.1", "127.0.0.1:1", "0.0.0.0:%d" % srv["port"]):
        assert req(srv, "GET", "/?t=" + srv["token"], host=host)[0] == 403
        assert req(srv, "GET", "/api/bundle", headers=auth(srv), host=host)[0] == 403
    assert req(srv, "GET", "/api/bundle", headers=auth(srv), host="localhost:%d" % srv["port"])[0] == 200


def test_oversize_body_413(srv):
    big = {"card_id": "c", "action": "dismiss", "reason": "x" * (MAX_BODY + 10)}
    st, data, _ = req(srv, "POST", "/api/decide", body=big, headers=auth(srv))
    assert st == 413
    assert srv["calls"]["decide"] == []


def test_bad_json_and_payload_400(srv):
    assert req(srv, "POST", "/api/decide", raw=b"{nope", headers=auth(srv, {"Content-Type": "application/json"}))[0] == 400
    assert req(srv, "POST", "/api/decide", raw=b'{"card_id":"c","action":"approve"}', headers=auth(srv, {"Content-Type": "text/plain"}))[0] == 415
    for bad in ([], {"card_id": "c"}, {"card_id": "c", "action": "delete"}, {"card_id": "", "action": "approve"},
                {"card_id": "c", "action": "approve", "extra": 1}, {"card_id": "c", "action": "dismiss"},
                {"card_id": "c", "action": "postpone"}, {"card_id": "c", "action": "postpone", "until": "next week"}):
        assert req(srv, "POST", "/api/decide", body=bad, headers=auth(srv))[0] == 400, bad
    assert req(srv, "POST", "/api/apply", body={"x": 1}, headers=auth(srv))[0] == 400
    assert srv["calls"] == {"decide": [], "apply": 0}


def test_decide_calls_callback(srv):
    st, data, _ = req(srv, "POST", "/api/decide", body={"card_id": "rec_cache_key_1", "action": "approve"}, headers=auth(srv, {"Origin": srv["origin"]}))
    assert st == 200
    assert json.loads(data) == {"ok": True, "result": {"saved": "rec_cache_key_1", "action": "approve"}}
    req(srv, "POST", "/api/decide", body={"card_id": "c2", "action": "dismiss", "reason": " not now "}, headers=auth(srv))
    req(srv, "POST", "/api/decide", body={"card_id": "c3", "action": "postpone", "until": "2026-11-01"}, headers=auth(srv))
    assert srv["calls"]["decide"] == [{"card_id": "rec_cache_key_1", "action": "approve"},
                                      {"card_id": "c2", "action": "dismiss", "reason": "not now"},
                                      {"card_id": "c3", "action": "postpone", "until": "2026-11-01"}]
    # a ValueError from the callback is a 400, not a crash
    assert req(srv, "POST", "/api/decide", body={"card_id": "boom", "action": "approve"}, headers=auth(srv))[0] == 400


def test_apply_calls_callback_and_echoes_command(srv):
    st, data, _ = req(srv, "POST", "/api/apply", body={}, headers=auth(srv))
    assert st == 200
    j = json.loads(data)
    assert j["ok"] and j["command"] == "router-migrate apply --decisions /tmp/repo"
    assert j["result"]["output"] == "applied 2 cards"
    assert srv["calls"]["apply"] == 1
    assert req(srv, "POST", "/api/apply", raw=b"", headers=auth(srv))[0] == 200  # empty body is fine
    assert srv["calls"]["apply"] == 2


def test_no_other_endpoints(srv):
    t = "?t=" + srv["token"]
    for method, path in (("GET", "/index.html" + t), ("GET", "/api/other"), ("GET", "/static/x.js" + t), ("POST", "/api/bundle"),
                         ("GET", "/api/decide"), ("PUT", "/api/decide"), ("DELETE", "/api/bundle"), ("OPTIONS", "/api/decide"), ("POST", "/" + t)):
        st = req(srv, method, path, headers=auth(srv), raw=b"" if method in ("POST", "PUT") else None)[0]
        assert st in (404, 405), (method, path, st)
    assert srv["calls"] == {"decide": [], "apply": 0}


# ---------------- frozen report ----------------

URL_RE = re.compile(r"""https?://[^\s"'<>)\\]+""")
ALLOWED = ("https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://app.router.com", "https://docs.router.com")


def test_render_report_embeds_bundle_frozen(bundle):
    html = render_report(bundle)
    assert PLACEHOLDER not in html
    assert 'window.__RM_MODE__ = "frozen"' in html
    assert "frozen report" in html and "router-migrate up" in html
    m = re.search(r"window\.__RM__ = (\{.*?\}); window\.__RM_MODE__", html, re.S)
    assert json.loads(m.group(1)) == bundle


def test_render_report_escapes_script_close(bundle):
    b = copy.deepcopy(bundle)
    evil = "</script><script>alert(1)</script><!-- \u2028 & </SCRIPT >"
    b["repo"] = evil
    b["recommendations"]["cards"][0]["title"] = evil
    b["diff"] += "\n+" + evil
    html = render_report(b)
    boot = html[html.index('<script id="rm-boot">'):]
    boot = boot[:boot.index("</script>") + len("</script>")]
    assert "</script" not in boot[:-len("</script>")].lower()
    assert "<!--" not in boot and "\u2028" not in boot
    # exactly the page's own <script> elements, nothing injected
    assert html.lower().count("<script") == page_template().lower().count("<script")
    m = re.search(r"window\.__RM__ = (\{.*?\}); window\.__RM_MODE__", html, re.S)
    assert json.loads(m.group(1))["repo"] == evil
    assert safe_json("</script>") == '"\\u003c/script\\u003e"'


def test_render_report_has_no_external_urls(bundle):
    html = render_report(bundle)
    urls = URL_RE.findall(html)
    assert urls, "expected at least the Google Fonts and Router links"
    bad = [u for u in urls if not u.startswith(ALLOWED)]
    assert not bad, bad
    # no remote scripts, images or iframes; only the Google Fonts stylesheet is loaded
    assert not re.search(r"<script[^>]+src=", html, re.I)
    assert not re.search(r"<(img|iframe|object|embed)\b", html, re.I)
    for href in re.findall(r'<link[^>]+href="([^"]+)"', html):
        assert href.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com", "data:")), href
    assert "fetch(" in html and "/api/bundle" in html  # live-only code path, relative URLs


def test_render_page_rejects_unknown_mode(bundle):
    with pytest.raises(ValueError):
        render_page(bundle, "edit")


def test_page_script_parses():
    """A syntax error in page.html blanks the whole dashboard; parse the inline script with node when it is installed."""
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    import subprocess
    src = page_template()
    script = re.findall(r"<script>([\s\S]*?)</script>", src)[-1]
    r = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
