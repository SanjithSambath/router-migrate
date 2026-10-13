"""v2 compiled policy in the Python runtime: every shared fixture (tests/fixtures/policy/*.json, also run by the TS runtime in
tests/test_policy_ts.py) is sent through the vendored transport to router-sim and the Router-bound body is checked."""
import glob
import json
import os

import httpx
import pytest

from conftest import Split

FIX = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "policy", "*.json")))


def subset(have, want):
    if isinstance(want, dict):
        return isinstance(have, dict) and all(k in have and subset(have[k], v) for k, v in want.items())
    return have == want


def send(rt, fx):
    host = "api.anthropic.com" if fx["request_path"].startswith("/v1/messages") else "api.openai.com"
    headers = {"authorization": "Bearer sk-provider-test"} if host == "api.openai.com" else {"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"}
    with httpx.Client(transport=rt.RouterTransport(inner=Split())) as c:
        return c.post("https://%s%s" % (host, fx["request_path"]), json=fx["request"], headers=headers)


@pytest.mark.parametrize("path", FIX, ids=[os.path.basename(p)[:-5] for p in FIX])
def test_policy_fixture(rt, sim, path):
    fx = json.load(open(path))
    rt.set_policy(fx["policy"], "fixturehash00" if fx["policy"] else None)
    try:
        r = send(rt, fx)
        assert r.status_code == 200, r.text
        rec = sim.records[-1]
        assert rec["path"] == fx["expect"]["router_path"] and rec["valid"], rec
        body = rec["request"]
        assert subset(body, fx["expect"]["body_has"]), json.dumps(body)[:800]
        for k in fx["expect"]["body_lacks"]:
            assert k not in body, (k, json.dumps(body)[:800])
    finally:
        rt.set_policy(None)


def _bodies(rt, sim, fx, policy, env=None, monkeypatch=None):
    rt.set_policy(policy, "h")
    send(rt, fx)
    return sim.records[-1]["request"]


def test_no_policy_off_switch_and_invalid_are_all_v1(rt, sim, monkeypatch):
    """No policy, ROUTER_MIGRATE_POLICY=off and an invalid policy produce byte-identical Router bodies."""
    for path in FIX:
        fx = json.load(open(path))
        base = _bodies(rt, sim, fx, None)
        assert _bodies(rt, sim, fx, {"version": 1, "defaults": {"model": "x"}}) == base
        if fx["policy"]:
            monkeypatch.setenv("ROUTER_MIGRATE_POLICY", "off")
            assert _bodies(rt, sim, fx, fx["policy"]) == base
            monkeypatch.delenv("ROUTER_MIGRATE_POLICY")
    rt.set_policy(None)


def test_policy_logs_once_without_content(rt, sim, capsys):
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "policy", "flex_guard_skips_openai_model_without_flex_tier.json")))
    fx["request"]["messages"][0]["content"] = "SECRET-PROMPT"
    rt.set_policy(fx["policy"], "abcdef1234567890")
    for _ in range(3):
        send(rt, fx)
    err = capsys.readouterr().err
    assert err.count("router-migrate policy abcdef12") == 1 and err.count("allow_flex_tier skipped") == 1 and "SECRET-PROMPT" not in err
    rt.set_policy(None)


def test_compiled_module_is_loaded_from_beside_the_runtime(tmp_path, sim, monkeypatch):
    """apply --decisions writes router_policy.py next to router_runtime.py; the runtime picks it up with no other change."""
    import importlib.util
    import subprocess
    import sys
    from router_migrate import policy as pol
    from router_migrate.catalog import Catalog
    from router_migrate.runtime import render
    (tmp_path / "router_runtime.py").write_text(render("router_runtime.py", Catalog.load()))
    p = {"version": 1, "defaults": {"metadata": {"team": "t1"}, "provider_timeout": 42}}
    (tmp_path / "router_policy.py").write_text(pol.compile_py(p))
    code = ("import sys, json, httpx; sys.path.insert(0, %r); import router_runtime as rt\n"
            "c = httpx.Client(transport=rt.RouterTransport())\n"
            "r = c.post('https://api.openai.com/v1/chat/completions', json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'hi'}]}, headers={'authorization': 'Bearer x'})\n"
            "print(r.status_code)") % str(tmp_path)
    env = dict(os.environ, RAMP_ROUTER_API_KEY="rm-test-key-not-a-real-key", RAMP_ROUTER_BASE_URL=sim.base)
    sim.records.clear()
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "200", out.stderr
    body = sim.records[-1]["request"]
    assert body["metadata"]["team"] == "t1" and body["provider_timeout"] == 42
    assert "router-migrate policy " + pol.policy_hash(p)[:8] in out.stderr
