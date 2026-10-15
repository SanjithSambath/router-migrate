"""facts --refresh: anchored lines are rewritten from (mocked) live probes; nothing else changes; the key never leaks."""
import datetime as dt
import json
import os

import httpx
import pytest

from router_migrate import facts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHEETS = [os.path.join(ROOT, "router_migrate", "data", n) for n in ("ROUTER_open.md", "ROUTER.md")]
KEY = "sk-router-test-SECRET-0123456789"
DAY = dt.date(2026, 10, 15)

MODELS = {"data": (
    [{"id": "gpt-%d" % i, "owned_by": "openai", "router": {}} for i in range(30)]
    + [{"id": i, "owned_by": "openai", "router": {}} for i in ("gpt-5.4-mini", "gpt-5.4-nano", "gpt-5", "o3")]
    + [{"id": "claude-%d" % i, "owned_by": "anthropic", "router": {}} for i in range(13)]
    + [{"id": "grok-%d" % i, "owned_by": "xai", "router": {}} for i in range(8)]
    + [{"id": i, "owned_by": "router", "router": {}} for i in ("deepseek-v4", "kimi-k3", "gpt-oss-120b", "switchyard")]
    + [{"id": "jev-1.14.0", "owned_by": "typesafe", "router": {}}]
    + [{"id": "gemini-%d" % i, "owned_by": "vertex", "router": {"requires_byok": True}} for i in range(5)]
)}


def handler(fail=(), switchyard_status=200):
    calls = []

    def h(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer " + KEY
        if req.url.path == "/v1/models":
            if "catalog" in fail:
                return httpx.Response(503, text="error code: 503")
            return httpx.Response(200, json=MODELS)
        body = json.loads(req.content)
        if body.get("model") == "gpt-5.4-mini":
            if "flex-tier" in fail:
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(200, json={"model": "gpt-5.4-mini", "service_tier": "flex",
                                             "usage": {"input_tokens": 12, "output_tokens": 2, "cost": 0.0000045}})
        if "prompt_cache_key" in body:
            n = sum(1 for c in calls if b"prompt_cache_key" in c.content)
            return httpx.Response(200, json={"model": "gpt-4o-mini-2024-07-18", "usage": {
                "input_tokens": 1234, "input_tokens_details": {"cached_tokens": 1152 if n > 1 else 0},
                "cost": 0.0001 if n > 1 else 0.0002}})
        if "models" in body:
            return httpx.Response(200, json={"model": "gpt-4o-mini-2024-07-18", "usage": {"cost": 0.00001}},
                                  headers={"x-router-provider": "openai"})
        if body.get("model") == "switchyard":
            if switchyard_status != 200:
                return httpx.Response(switchyard_status, json={"error": {"message": "no switchyard strategy | saved",
                                                                         "code": "strategy_not_found"}})
            return httpx.Response(200, json={"model": "gpt-5.6-luna", "service_tier": "flex", "usage": {"cost": 0.00002}})
        return httpx.Response(404, text="404 page not found")
    h.calls = calls
    return h


@pytest.fixture
def mock(monkeypatch):
    def install(**kw):
        h = handler(**kw)
        monkeypatch.setattr(facts, "_client", lambda key: httpx.Client(
            base_url=facts.ROUTER, transport=httpx.MockTransport(h), headers={"Authorization": "Bearer " + key}))
        monkeypatch.setattr(facts, "_sleep", lambda s: None)
        return h
    return install


def _sheet(tmp_path, src=SHEETS[0]):
    p = tmp_path / "sheet.md"
    p.write_text(open(src).read())
    return p


@pytest.mark.parametrize("src", SHEETS)
def test_anchors_present(src):
    a = facts.anchors(open(src).read())
    want = {"catalog-counts", "flex-eligible", "flex-tier", "cache-responses", "fallback"}
    if src.endswith("ROUTER_open.md"):
        want.add("switchyard")
    assert set(a) == want
    assert all(len(v) == 1 for v in a.values())
    owned = {x for _, _, owns in facts.PROBES for x in owns}
    assert set(a) <= owned


def test_only_anchored_lines_change_with_tag_and_date(tmp_path, mock):
    h = mock()
    p = _sheet(tmp_path)
    before = p.read_text().split("\n")
    res = facts.refresh(str(p), KEY, now=DAY)
    after = p.read_text().split("\n")
    assert all(x["ok"] for x in res["probes"]), res["probes"]
    assert {c["id"] for c in res["changed"]} == {"catalog-counts", "flex-eligible", "flex-tier", "cache-responses",
                                                 "fallback", "switchyard"}
    # exactly one inserted line (the source-table row); everything else lines up
    assert len(after) == len(before) + 1
    row = "| [R2026-10-15] | router-migrate facts --refresh, live probes on 2026-10-15 |"
    k = after.index(row)
    assert before[k - 1].startswith("| [upstream]")
    rest = after[:k] + after[k + 1:]
    diff = [i for i, (a, b) in enumerate(zip(before, rest)) if a != b]
    assert all(facts.ANCHOR.search(before[i]) for i in diff)
    assert len(diff) == 6
    for i in diff:
        assert "[R2026-10-15]" in rest[i]
        assert facts.ANCHOR.search(rest[i]).group(0) == facts.ANCHOR.search(before[i]).group(0)
    text = "\n".join(after)
    assert "OpenAI (34 ids), Anthropic (13), xAI (8), open-weight models under provider-agnostic ids " \
           "(DeepSeek, Kimi, gpt-oss: 3)" in text and "(5 ids, `requires_byok`) [R2026-10-15]" in text
    assert "came back `service_tier: \"flex\"`" in text
    assert "cached 1,152 tokens" in text
    assert "was served by `gpt-4o-mini-2024-07-18`, and the response doesn't name the failed candidate" in text
    # list prefix kept; table row keeps its four cells
    sw = next(l for l in after if "fact:switchyard" in l)
    assert sw.startswith("| Switchyard |") and sw.count("|") == 5 and "served `gpt-5.6-luna` at `flex`" in sw
    assert next(l for l in after if "fact:fallback" in l).startswith("- Behaviour: ")
    assert next(l for l in after if "fact:cache-responses" in l).startswith("  gpt-4o-mini, 1,234-token")
    assert res["total_cost_usd"] == pytest.approx(0.0000045 + 0.0003 + 0.00001 + 0.00002)
    assert len(h.calls) == 6  # models, flex, cache x2, fallback, switchyard


def test_failing_probe_leaves_line_and_is_reported(tmp_path, mock):
    mock(fail=("flex-tier", "catalog"))
    p = _sheet(tmp_path)
    before = p.read_text()
    res = facts.refresh(str(p), KEY, now=DAY)
    by = {x["id"]: x for x in res["probes"]}
    assert not by["flex-tier"]["ok"] and "502" in by["flex-tier"]["result"]
    assert not by["catalog"]["ok"] and "503" in by["catalog"]["result"]
    assert by["fallback"]["ok"]
    old = {l for l in before.split("\n") if "fact:flex-tier" in l or "fact:catalog-counts" in l}
    new = {l for l in p.read_text().split("\n") if "fact:flex-tier" in l or "fact:catalog-counts" in l}
    assert old == new and len(old) == 2
    assert "flex-tier" not in {c["id"] for c in res["changed"]}


def test_switchyard_4xx_is_recorded_honestly(tmp_path, mock):
    mock(switchyard_status=404)
    p = _sheet(tmp_path)
    res = facts.refresh(str(p), KEY, probes=["switchyard"], now=DAY)
    sw = next(l for l in p.read_text().split("\n") if "fact:switchyard" in l)
    assert res["probes"][0]["ok"] and res["probes"][0]["result"]["status"] == 404
    assert "returned HTTP 404 `strategy_not_found`" in sw and sw.count("|") == 5  # the '|' in the message is escaped


def test_dry_run_and_out_path_leave_sheet(tmp_path, mock):
    mock()
    p = _sheet(tmp_path)
    before = p.read_text()
    res = facts.refresh(str(p), KEY, now=DAY, dry_run=True)
    assert p.read_text() == before and res["written"] is None and "+| [R2026-10-15]" in res["diff"]
    out = tmp_path / "out.md"
    facts.refresh(str(p), KEY, out_path=str(out), now=DAY)
    assert p.read_text() == before and "[R2026-10-15]" in out.read_text()


def test_missing_anchor_skips_probe(tmp_path, mock):
    h = mock()
    p = _sheet(tmp_path, SHEETS[1])  # ROUTER.md has no Switchyard line
    res = facts.refresh(str(p), KEY, probes=["switchyard"], now=DAY)
    assert res["probes"][0].get("skipped") and not h.calls and not res["changed"]


def test_key_never_leaks(tmp_path, mock, monkeypatch):
    mock()
    p = _sheet(tmp_path)
    res = facts.refresh(str(p), KEY, now=DAY)
    assert KEY not in json.dumps(res) and KEY not in p.read_text()

    # even an exception that quotes the key is scrubbed from the result
    def boom(c):
        raise RuntimeError("bad header Bearer " + KEY)
    monkeypatch.setattr(facts, "PROBES", [("flex-tier", boom, ("flex-tier",))])
    res = facts.refresh(str(p), KEY, now=DAY)
    assert not res["probes"][0]["ok"] and KEY not in json.dumps(res)


def test_no_key_refuses(tmp_path, mock, capsys):
    h = mock()
    p = _sheet(tmp_path)
    before = p.read_text()
    with pytest.raises(ValueError, match="needs the migration key"):
        facts.refresh(str(p), None)
    rc = facts.cli(["--refresh", "--sheet", str(p)])
    assert rc != 0 and "needs the migration key" in capsys.readouterr().err
    assert p.read_text() == before and not h.calls


def test_cli_dry_run_json(tmp_path, mock, monkeypatch, capsys):
    mock()
    monkeypatch.setenv("ROUTER_MIGRATE_KEY", KEY)
    p = _sheet(tmp_path)
    before = p.read_text()
    assert facts.cli(["--refresh", "--sheet", str(p), "--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    assert p.read_text() == before and KEY not in out and json.loads(out)["changed"]
