"""The dashboard (v7 item model; v6 reports still load): the bundle (one item model from the state files plus git), the frozen REPORT.html, and the live server."""
import http.client
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from router_migrate import harness
from router_migrate import report_v6 as R
from router_migrate.dashboard import items as I

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "v6_state")
STATUS = {"m1": "in code", "t1": "in code", "p1": "undo requested", "s1": "info", "d1": "postponed", "i1": "proposed",
          "i2": "approved", "i3": "applied", "i4": "check failed", "i5": "declined", "i6": "undone", "i7": "dismissed",
          "r8": "your step", "r9": "done", "n1": "info"}


def git(root, *a):
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=T"] + list(a),
                          check=True, capture_output=True, text=True).stdout.strip()


def commit_file(root, path, text, msg):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "--short=7", "HEAD")


@pytest.fixture()
def repo(tmp_path):
    """A tiny git repo with the fixture state; the fixture's commit ids don't exist in it."""
    git(tmp_path, "init", "-q", "-b", "main")
    open(os.path.join(str(tmp_path), ".git", "info", "exclude"), "a").write(".router-migrate/\n")
    commit_file(tmp_path, "app.py", "print('hi')\n", "init")
    shutil.copytree(FIX, str(tmp_path / ".router-migrate"))
    return tmp_path


def state(root, name, obj=None):
    p = root / ".router-migrate" / name
    if obj is None:
        return json.loads(p.read_text())
    p.write_text(json.dumps(obj))


# ------------------------------------------------------------------------------------------------ bundle
def test_bundle_has_every_kind_and_status(repo):
    b = I.bundle(str(repo))
    by = {i["id"]: i for i in b["items"]}
    assert {i: by[i]["status"] for i in by} == STATUS
    assert {i["kind"] for i in b["items"]} == set(R.KINDS)
    assert {"your step", "done"} <= {i["status"] for i in b["items"]}
    assert {by[i]["actor"] for i in by if by[i]["kind"] == "recommendation"} == set(R.ACTORS)
    doc = state(repo, "MIGRATION.json")
    for it in doc["items"]:
        assert by[it["id"]]["etag"] == R.item_etag(it)
    assert b["repo"] == os.path.basename(str(repo)) and b["live"] is True and b["tool_version"].startswith("router-migrate ")
    assert b["summary"].startswith("Moved all") and b["reviewed"]
    assert b["report_errors"] == []


def test_bundle_headline_calls_and_queue(repo):
    b = I.bundle(str(repo))
    assert b["headline"]["traffic"] == {"total": 6, "on_router": 4, "verified": 3, "unverified": 1, "not_routing": 0, "stays": 2}
    bh = b["headline"]["behaviour"]
    assert bh["checked"] and bh["ok"] is True and bh["tests"]["status"] == "passed"
    assert any(e["check"] == "tests" and "same as before" in e["detail"] for e in bh["entries"])
    assert sorted(b["headline"]["waiting"]["items"]) == ["i1", "i4", "i5", "r8"]  # proposed, check failed, declined, your step
    assert sorted(p["item"] for p in b["pending"]) == ["i2", "p1"]
    assert b["apply_blocked"] is None and b["running"] == []
    calls = {c["site"]: c for c in b["calls"]}
    assert calls["src/ai/complete.ts:50"]["route"] == "verified" and calls["src/ai/complete.ts:50"]["item_title"]
    assert calls["src/agents/research.ts:88"]["route"] == "unverified"
    assert calls["app/rag/index.py:12"]["route"] == "stays" and "embeddings" in calls["app/rag/index.py:12"]["reason"]
    assert calls["app/byok.py:30"]["route"] == "provider"
    assert [s["id"] for s in b["history"]] == ["A1", "A2"] and b["history"][0]["results"][2]["status"] == "declined"
    assert b["cost"]["agent_usd"] == 4.12


def test_items_carry_evidence_flags_actions_and_model_change(repo):
    by = {i["id"]: i for i in I.bundle(str(repo))["items"]}
    m1 = by["m1"]
    assert {e["source"] for e in m1["checked"]} == {"agent", "check"}
    assert any(e["source"] == "check" and e.get("label") == "Routing" and e["result"] == "pass" for e in m1["checked"])
    fail = [e for e in by["i4"]["checked"] if e["source"] == "check" and e["check"] == "tests"]
    assert fail and fail[0]["result"] == "fail" and "tests/reply.test.ts" in fail[0]["detail"]
    for i in by.values():  # never a bare result: every entry says what ran
        assert all(e["detail"] for e in i["checked"]), i["id"]
    assert [e for e in by["t1"]["checked"] if e["check"] == "router-live"][0]["result"] == "not-run"
    assert by["i1"]["model_change"] and by["i2"]["model_change"] and by["i4"]["model_change"]
    assert not by["i3"]["model_change"] and not by["m1"]["model_change"]
    assert by["i1"]["actions"] == ["approve", "dismiss", "postpone"]
    assert by["i5"]["actions"] == ["approve", "dismiss", "postpone"]  # declined apply: decide again
    assert by["m1"]["actions"] == ["undo"] and by["i3"]["actions"] == ["undo"]
    assert by["p1"]["actions"] == ["withdraw"] and by["i2"]["actions"] == ["withdraw"]
    assert by["s1"]["actions"] == [] and by["n1"]["actions"] == []
    assert by["r8"]["actions"] == ["done", "dismiss", "postpone"] and by["r8"]["waiting"]  # a router-setting: your step
    assert by["r9"]["actions"] == [] and not by["r9"]["waiting"]  # marked done: nothing left to do
    assert by["r8"]["checked"] == [] and not by["r8"]["model_change"]  # nothing built, nothing to check
    assert by["d1"]["actions"] == ["withdraw"] and not by["d1"]["waiting"]  # postponed until a future date
    assert by["i5"]["results"][-1]["status"] == "declined" and by["i5"]["results"][-1]["session"] == "A1"
    # the fixture's commits and branches aren't in this repo: the diff says so instead of failing
    assert by["m1"]["diff"]["error"] and by["m1"]["diff"]["text"] == ""
    assert "not found" in by["i1"]["diff"]["error"]


def test_diffs_come_from_git_and_flags_follow_the_commit(repo):
    git(repo, "checkout", "-q", "-b", R.MIGRATION_BRANCH)
    sha = commit_file(repo, "package.json", '{"dependencies": {"openai": "^5"}}\n', "Port web search")
    git(repo, "checkout", "-q", "-b", "router-migrate/improve/classifier-haiku-5-5")
    commit_file(repo, "src/ai/models.ts", 'export const M = "claude-haiku-5-5";\n' + "// pad\n" * 600, "Classifier on Haiku 5.5")
    git(repo, "checkout", "-q", R.MIGRATION_BRANCH)
    doc = state(repo, "MIGRATION.json")
    for it in doc["items"]:
        if it["id"] == "p1":
            it["commit"] = sha
    state(repo, "MIGRATION.json", doc)
    by = {i["id"]: i for i in I.bundle(str(repo))["items"]}
    p1 = by["p1"]
    assert p1["diff"]["error"] is None and p1["diff"]["files"] == ["package.json"] and "openai" in p1["diff"]["text"]
    assert [f["kind"] for f in p1["flags"]] == ["dependencies"]  # it touched package.json; the deleted-file flag isn't its
    i1 = by["i1"]["diff"]
    assert i1["files"] == ["src/ai/models.ts"] and i1["truncated"] and len(i1["text"].splitlines()) == I.DIFF_LINES
    assert i1["lines"] > I.DIFF_LINES and "claude-haiku-5-5" in i1["text"]
    assert by["i2"]["diff"]["error"]  # its branch doesn't exist here


def test_recommendations_carry_rank_level_actor_and_measured(repo):
    b = I.bundle(str(repo))
    by = {i["id"]: i for i in b["items"]}
    assert b["recommendations"] == ["d1", "i1", "i2", "i3", "i4", "i5", "i6", "i7", "r8", "r9"]  # rank order
    assert b["setup"] == {"settings": ["r8", "r9"]}  # this app's router-setting recommendations, in rank order
    i1, d1, r8 = by["i1"], by["d1"], by["r8"]
    assert (i1["rank"], i1["level"], i1["actor"], i1["built"]) == (2, "quick-win", "code", True)
    assert i1["measured"] == [{"metric": "cost per classification", "before": "$0.0021", "after": "$0.0004", "n": 3,
                               "how": "check --run --live, the classifier on 3 real tickets"}]
    assert not any(e["check"] == "router-live" for e in i1["checked"] if e["source"] == "check")  # measured live instead
    assert (d1["actor"], d1["built"]) == ("product-call", False)
    assert (r8["actor"], r8["built"], r8["where"]) == ("router-setting", None, "app.router.com > Routing > Shadow models")
    assert by["m1"]["rank"] is None and by["m1"]["actor"] is None and by["m1"]["measured"] == [] and by["m1"]["v6_kind"] is None


def test_a_v6_report_still_bundles(repo):
    shutil.copy(os.path.join(FIX, "MIGRATION_v6.json"), str(repo / ".router-migrate" / "MIGRATION.json"))
    b = I.bundle(str(repo))
    assert b["report_errors"] == []
    by = {i["id"]: i for i in b["items"]}
    assert {i["kind"] for i in b["items"]} <= set(R.KINDS)
    assert {i: by[i]["v6_kind"] for i in ("m1", "t1", "p1", "d1", "i1")} == {
        "m1": "move", "t1": "trade-off", "p1": "port", "d1": "needs-decision", "i1": "improvement"}
    assert by["d1"]["actor"] == "product-call" and by["i1"]["actor"] == "code" and by["i1"]["rank"] == 2
    assert {i: by[i]["status"] for i in by} == {k: v for k, v in STATUS.items() if k not in ("r8", "r9")}
    assert by["i1"]["actions"] == ["approve", "dismiss", "postpone"] and by["m1"]["actions"] == ["undo"]
    assert b["setup"] == {"settings": []}
    assert "production LLM calls go through Router." in render_tab(repo, "overview")


def test_bundle_tolerates_partial_state(tmp_path):
    b = I.bundle(str(tmp_path))  # nothing at all, not even git
    assert b["items"] == [] and b["calls"] == [] and not b["has_report"] and b["report_errors"] == []
    assert "finish" in b["apply_blocked"] and b["headline"]["behaviour"]["checked"] is False
    st = tmp_path / ".router-migrate"
    st.mkdir()
    shutil.copy(os.path.join(FIX, "MIGRATION.json"), str(st / "MIGRATION.json"))
    (st / "requests.json").write_text("not json")
    b = I.bundle(str(tmp_path))
    by = {i["id"]: i for i in b["items"]}
    assert by["i1"]["status"] == "proposed" and by["i4"]["status"] == "proposed"  # no validation yet
    assert any(e["result"] == "not-run" for e in by["i1"]["checked"])
    assert by["r9"]["status"] == "your step"  # no requests.json to say it's done
    assert b["history"] == [] and b["pending"] == [] and b["live"] is False and "finish" in b["apply_blocked"]
    assert by["m1"]["diff"]["error"]  # not a git repository
    doc = json.load(open(os.path.join(FIX, "MIGRATION.json")))
    doc["items"][0]["kind"] = "migrated"
    doc["items"].append("junk")
    (st / "MIGRATION.json").write_text(json.dumps(doc))
    b = I.bundle(str(tmp_path))
    assert any("kind" in e for e in b["report_errors"]) and len(b["items"]) == 15


# ------------------------------------------------------------------------------------------------ REPORT.html
def test_render_report_is_frozen_self_contained_and_shows_every_item(repo):
    html = I.render_report(str(repo))
    assert I.PLACEHOLDER not in html and 'window.__RM_MODE__ = "frozen"' in html and "read-only snapshot" in html
    m = re.search(r"window\.__RM__ = (\{.*?\}); window\.__RM_MODE__", html, re.S)
    b = json.loads(m.group(1))
    titles = [i["title"] for i in state(repo, "MIGRATION.json")["items"]]
    assert [i["title"] for i in b["items"]] == titles
    for t in titles:
        assert json.dumps(t)[1:-1] in html
    assert not re.search(r"<script[^>]+src=", html, re.I) and not re.search(r"<link[^>]+stylesheet", html, re.I)
    assert not re.search(r"<(img|iframe|object|embed)\b", html, re.I)
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    assert all(u.startswith(("https://app.router.com", "https://docs.router.com")) for u in urls), urls
    path = I.write_report(str(repo))
    assert path.endswith(os.path.join(".router-migrate", "REPORT.html")) and open(path).read().startswith("<!doctype html>")


def test_render_report_escapes_agent_text(repo):
    doc = state(repo, "MIGRATION.json")
    evil = "</script><script>alert(1)</script><!--  "
    doc["items"][0]["title"] = evil[:80]
    doc["summary"] = evil
    state(repo, "MIGRATION.json", doc)
    html = I.render_report(str(repo))
    boot = html[html.index('<script id="rm-boot">'):]
    boot = boot[:boot.index("</script>") + len("</script>")]
    assert "</script" not in boot[:-len("</script>")].lower() and "<!--" not in boot and " " not in boot
    assert html.lower().count("<script") == I.page_template().lower().count("<script")


def test_page_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = re.findall(r"<script>([\s\S]*?)</script>", I.page_template())[-1]
    r = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]


DOM_STUB = """
var els = {};
function el(id) { return els[id] || (els[id] = {id: id, innerHTML: "", firstChild: null, classList: {add: function () {}, remove: function () {}}, setAttribute: function () {}}); }
global.window = global;
global.document = {getElementById: el, addEventListener: function () {}, querySelector: function () { return null; }, activeElement: null, visibilityState: "visible", title: ""};
global.location = {search: "", hash: "", pathname: "/"};
global.history = {replaceState: function () {}};
global.localStorage = {getItem: function () { return null; }, setItem: function () {}, removeItem: function () {}};
global.requestAnimationFrame = function () {};
global.scrollTo = function () {}; global.scrollY = 0; global.addEventListener = function () {};
"""


def render_tab(repo, tab):
    """The page's main area for one tab, rendered by node from the frozen report."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = I.render_report(str(repo))
    js = DOM_STUB.replace('hash: ""', 'hash: "#%s"' % tab) + "\n".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)) + \
        "\nprocess.stdout.write(els.main.innerHTML);"
    r = subprocess.run([node, "-"], input=js, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


def test_overview_is_where_things_stand(repo):
    out = render_tab(repo, "overview")
    head = out[:out.index('class="doors"')]
    assert "4 of 6 production LLM calls go through Router." in head and "10 changes worth making." in head
    assert "4 things need you: 3 to approve and 1 Router setting to switch on. 2 decisions are ready to apply." in head  # i1 i4 i5 / r8; i2 and p1 pending
    doors = out[out.index('class="doors"'):out.index("Start here")]
    assert [d for d in re.findall(r'class="door[^"]*" type="button" data-tab="([a-z]+)"', doors)] == ["migrate", "review", "queue"]
    assert "Step 01" in doors and "Step 02" in doors and "Your turn" in doors
    assert "$0.0021 → $0.0004" in doors and "947 of 948 tests pass, as before" in doors
    top = out[out.index("Start here"):out.index('id="flow"')]
    rows = re.findall(r'class="srow" type="button" data-go-item="([^"]+)"><span class="rkn">(\d+)</span>', top)
    assert rows == [("i1", "2"), ("i4", "5"), ("i5", "6")]  # the three most valuable open ones; approved/applied/decided left out
    assert "$0.0021 → $0.0004" in top
    assert 'class="card' not in out  # the overview never repeats a card


def test_migrate_and_review_pages(repo):
    mig = render_tab(repo, "migrate")
    assert re.findall(r'<article class="card item" id="item-([^"]+)"', mig) == ["m1", "t1", "p1"]  # changes, as cards
    assert 'id="call-0"' in mig and 'id="item-s1"' in mig and "Stays · 1" in mig  # every call; stays as quiet rows
    assert "You give up:</b> Runs are no longer reproducible" in mig  # t1 loses something; m1 ("nothing") doesn't say so
    assert mig.count("You give up:") == len([i for i in state(repo, "MIGRATION.json")["items"] if i["kind"] == "change" and not i["loses"].startswith("nothing")])
    rev = render_tab(repo, "review")
    assert re.findall(r'<article class="card item" id="item-([^"]+)"', rev) == ["d1", "i1", "i2", "i3", "i4", "i5", "i6", "i7", "r8", "r9"]
    assert 'data-f="l" data-v="quick-win"' in rev and 'data-f="a" data-v="router-setting"' in rev  # level tiles, actor filter
    assert "Your decision" in rev and "Shadow models" in rev and "Measured" in rev and "$0.0004" in rev
    assert 'id="item-n1"' in rev and 'data-key="notes"' in rev  # notes, collapsed at the bottom
    assert 'id="item-m1"' not in rev and 'id="item-i1"' not in mig  # one job per page


def test_your_turn_groups_what_waits(repo):
    out = render_tab(repo, "queue")
    groups = re.findall(r'<h2>(To approve|Router settings|Your decisions|Ready to apply|Decided earlier)</h2>', out)
    assert groups == ["Ready to apply", "To approve", "Router settings", "Decided earlier"]  # no product call waits (d1 is postponed)
    appr = out[out.index("<h2>To approve"):out.index("<h2>Router settings")]
    assert re.findall(r'class="drow" id="q-([^"]+)"', appr) == ["i1", "i4", "i5"] and "Approve anyway" in appr and "Approve again" in appr
    sets = out[out.index("<h2>Router settings"):out.index("<h2>Decided earlier")]
    assert 'id="q-r8"' in sets and "Mark done" in sets and "Shadow models" in sets
    assert 'data-act="apply"' in out and "read-only snapshot" in out


def test_setup_page_from_router_settings(repo):
    setup = render_tab(repo, "setup")
    assert "Turn Router on in production" in setup and "Know the off switch" in setup
    assert "Set a spend cap on the production key" not in setup and "Decide the account Flex default" not in setup
    assert 'id="step-r8"' in setup and 'id="step-r9"' in setup and "Shadow Haiku 5.5" in setup and "Spend cap" in setup
    doc = state(repo, "MIGRATION.json")
    doc["items"] = [i for i in doc["items"] if i.get("actor") != "router-setting"]
    state(repo, "MIGRATION.json", doc)
    assert "No Router settings recommended for this app." in render_tab(repo, "setup")


def test_mark_done_round_trip(srv):
    st, out = req(srv, "POST", "/api/request", {"item": "r8", "action": "done"})
    assert st == 200 and out["request"]["action"] == "done"
    by = {i["id"]: i for i in req(srv, "GET", "/api/bundle")[1]["items"]}
    assert by["r8"]["status"] == "done" and by["r8"]["actions"] == [] and not by["r8"]["waiting"]
    assert "r8" not in [p["item"] for p in req(srv, "GET", "/api/bundle")[1]["pending"]]  # never goes to an apply session


def test_overview_draws_how_your_traffic_moved(repo):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = I.render_report(str(repo))
    js = DOM_STUB + "\n".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)) + "\nprocess.stdout.write(els.main.innerHTML);"
    r = subprocess.run([node, "-"], input=js, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    out = r.stdout
    assert "How your traffic moved" in out and 'id="flow"' in out and 'id="wires"' in out
    assert out.index('class="doors"') < out.index('id="flow"') < out.index('class="facts-line"')
    left = out[out.index('class="fcol"'):out.index('id="router-node"')]
    stay = out[out.index('class="flow-stay"'):out.index('class="legend"')]
    calls = I.bundle(str(repo))["calls"]
    for c in calls:
        part = left if c["route"] in ("verified", "unverified", "unchecked", "not_routing") else stay
        assert 'data-route="%s" data-go-call="%s"' % (c["route"], c["site"]) in part  # click goes to the call
    assert left.count('class="fnode') == 4 and stay.count('class="fnode') == 2
    assert 'class="fgroup-h"><span class="p" title="src/ai/complete.ts">' in left  # grouped by file
    assert "api.router.com · 4 calls" in out and "Not on Router · 2" in stay
    assert 'data-go-item="i3"' in out[out.index('id="router-node"'):out.index('class="legend"')]  # the applied improvement
    legend = out[out.index('class="legend"'):]
    assert "Confirmed going through Router <b>3</b>" in legend and "Stays with its provider <b>2</b>" in legend


# ------------------------------------------------------------------------------------------------ server
@pytest.fixture()
def srv(repo):
    server, url = I.serve(str(repo), open_browser=False)
    u = urlsplit(url)
    ctx = {"server": server, "url": url, "port": u.port, "token": server.token, "root": repo, "host": "127.0.0.1:%d" % u.port}
    yield ctx
    server.shutdown()


def req(ctx, method, path, body=None, token=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", ctx["port"], timeout=10)
    h = {"Host": host or ctx["host"]}
    if token:
        h["X-RM-Token"] = ctx["token"]
    raw = None
    if body is not None:
        raw = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
        h["Content-Length"] = str(len(raw))
    conn.request(method, path, body=raw, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data.decode("utf-8", "replace")


def test_page_and_bundle_need_the_token(srv):
    assert srv["url"].startswith("http://127.0.0.1:%d/?t=" % srv["port"])
    assert req(srv, "GET", "/", token=False)[0] == 403
    st, html = req(srv, "GET", "/?t=" + srv["token"], token=False)
    assert st == 200 and 'window.__RM_MODE__ = "live"' in html
    assert req(srv, "GET", "/api/bundle", token=False)[0] == 403
    assert req(srv, "GET", "/api/bundle", host="evil.example:%d" % srv["port"])[0] == 403
    st, b = req(srv, "GET", "/api/bundle")
    assert st == 200 and len(b["items"]) == 15
    assert req(srv, "GET", "/api/nope")[0] == 404 and req(srv, "GET", "/api/apply")[0] == 405


def test_request_round_trip_and_withdraw(srv):
    st, out = req(srv, "POST", "/api/request", {"item": "i1", "action": "apply"})
    assert st == 200 and out["request"]["action"] == "apply" and out["request"]["by"] == "ui"
    assert state(srv["root"], "requests.json")["requests"]["i1"]["etag"] == out["request"]["etag"]
    by = {i["id"]: i for i in req(srv, "GET", "/api/bundle")[1]["items"]}
    assert by["i1"]["status"] == "approved" and by["i1"]["actions"] == ["withdraw"]
    st, out = req(srv, "POST", "/api/request", {"item": "m1", "action": "undo", "reason": "keep the old client"})
    assert st == 200 and state(srv["root"], "requests.json")["requests"]["m1"]["reason"] == "keep the old client"
    st, out = req(srv, "POST", "/api/request", {"item": "i4", "action": "postpone", "until": "2026-12-01"})
    assert st == 200 and out["request"]["until"] == "2026-12-01"
    assert req(srv, "POST", "/api/request/clear", {"item": "i1"})[0] == 200
    assert "i1" not in state(srv["root"], "requests.json")["requests"]
    assert req(srv, "POST", "/api/request/clear", {"item": "i3"})[0] == 409  # session A1 already took it
    assert req(srv, "POST", "/api/request/clear", {"item": "i1"})[0] == 400  # nothing to withdraw


@pytest.mark.parametrize("body,needle", [
    ({"item": "i2", "action": "undo"}, "not in the code"),
    ({"item": "m1", "action": "apply"}, "only recommendations"),
    ({"item": "r8", "action": "apply"}, "mark it done"),
    ({"item": "i1", "action": "done"}, "only a router-setting"),
    ({"item": "r8", "action": "undo"}, "not in the code"),
    ({"item": "zz", "action": "apply"}, "unknown item"),
    ({"item": "d1", "action": "postpone"}, "needs a date"),
    ({"item": "i1", "action": "ship"}, "action must be"),
    ({"item": "i1", "action": "apply", "extra": 1}, "unknown field"),
    ({"item": "i1", "action": "postpone", "until": "soon"}, "YYYY-MM-DD"),
    ({"item": "../x", "action": "apply"}, "item id"),
])
def test_bad_requests_are_400_with_the_reason(srv, body, needle):
    st, out = req(srv, "POST", "/api/request", body)
    assert st == 400 and needle in out["error"], out


def test_apply_blocked_unless_every_request_is_an_undo(srv, monkeypatch):
    calls = []
    monkeypatch.setattr(harness, "launch_apply", lambda root: calls.append(root) or {"mode": "command", "command": "/router-migrate apply"}, raising=False)
    v = state(srv["root"], "validation.json")
    v["migration"]["ok"] = False
    v["migration"]["failures"] = [{"gate": "tests", "detail": "3 tests fail"}]
    state(srv["root"], "validation.json", v)
    st, out = req(srv, "POST", "/api/apply", {})
    assert st == 409 and "check failed" in out["error"] and not calls  # i2 (apply) and p1 (undo) pending
    assert req(srv, "POST", "/api/request/clear", {"item": "i2"})[0] == 200
    st, out = req(srv, "POST", "/api/apply", {})  # only the undo of p1 is left: always allowed
    assert st == 200 and out["result"]["mode"] == "command" and calls == [str(srv["root"])]
    st, b = req(srv, "GET", "/api/bundle")
    assert b["launch"]["mode"] == "command" and b["launch"]["requests"] == ["p1"]
    assert req(srv, "POST", "/api/apply", {"x": 1})[0] == 400


def test_apply_when_clear_and_refusals(srv, monkeypatch):
    monkeypatch.setattr(harness, "launch_apply", lambda root: {"mode": "headless", "session": "A3", "log": "/nonexistent/log"}, raising=False)
    st, out = req(srv, "POST", "/api/apply", {})
    assert st == 200 and out["result"]["session"] == "A3"
    rq = state(srv["root"], "requests.json")
    rq["requests"]["i2"]["session"] = "A3"  # the harness marks what the session took; A3 hasn't finished
    state(srv["root"], "requests.json", rq)
    st, b = req(srv, "GET", "/api/bundle")
    assert b["running"] == [{"session": "A3", "items": ["i2"]}] and b["launch"]["tail"] is None
    i2 = [i for i in b["items"] if i["id"] == "i2"][0]
    assert i2["in_session"] == "A3" and i2["actions"] == [] and not i2["waiting"]  # in progress, not "proposed"
    st, out = req(srv, "POST", "/api/apply", {})
    assert st == 409 and "A3" in out["error"]
    state(srv["root"], "requests.json", {"requests": {}})
    st, out = req(srv, "POST", "/api/apply", {})
    assert st == 409 and "Nothing to apply" in out["error"]


def test_apply_501_without_launch_apply(srv, monkeypatch):
    monkeypatch.delattr(harness, "launch_apply", raising=False)
    st, out = req(srv, "POST", "/api/apply", {})
    assert st == 501 and "/router-migrate apply" in out["error"]
