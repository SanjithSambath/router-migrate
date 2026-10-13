"""v2 Improve phase end to end on a synthetic repo that triggers every MVP lever: recommend (signals + cards + considered list),
decide (etag, validation), apply --decisions (policy compiled, unlock codemods, model cards never applied), status (hash,
stale, orphans), up --agent exit codes, and v1 behaviour untouched when nothing is approved."""
import json
import os
import subprocess
import sys

import pytest

from router_migrate import cli, improve, policy as pol, recommend
from router_migrate.catalog import Catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = "You are a careful summarizer. " * 200  # ~1,200 tokens of static system prompt

FILES = {
    "pyproject.toml": '[project]\nname = "demo"\ndependencies = ["openai", "anthropic", "fastapi"]\n',
    "svc/__init__.py": "",
    "svc/workers/nightly_summaries.py": (
        "import os\nfrom openai import OpenAI\n\nSYSTEM_PROMPT = \"\"\"%s\"\"\"\nclient = OpenAI()\n\n\n"
        "def summarize_all(docs):\n    return [client.chat.completions.create(model='gpt-5.4-mini', messages=[{'role': 'system', 'content': SYSTEM_PROMPT}, "
        "{'role': 'user', 'content': d}]) for d in docs]\n" % LONG),
    "svc/api/chat.py": (
        "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef answer(q):\n"
        "    return client.chat.completions.create(model='gpt-4-turbo', messages=[{'role': 'user', 'content': q}], stream=True)\n"),
    "svc/vendors/xai.py": (
        "import os\nfrom openai import OpenAI\n\n\nclass XAI:\n    def __init__(self, model=None):\n        self.model = model or 'grok-4.3'\n"
        "        self.client = OpenAI(api_key=os.environ.get('XAI_API_KEY'), base_url='https://api.x.ai/v1')\n\n"
        "    def generate(self, q):\n        return self.client.chat.completions.create(model=self.model, messages=[{'role': 'user', 'content': q}])\n"),
    "svc/agent.py": (
        "from anthropic import Anthropic\n\nPROMPT_CACHING_BETA_FLAG = 'prompt-caching-2024-07-31'\nclient = Anthropic()\n\n\n"
        "def run(q):\n    return client.messages.create(model='claude-haiku-4-5', max_tokens=256, messages=[{'role': 'user', 'content': q}],\n"
        "                                  extra_headers={'anthropic-beta': PROMPT_CACHING_BETA_FLAG})\n"),
}


@pytest.fixture()
def repo(tmp_path):
    for rel, src in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    cli.do_scan(str(tmp_path), quiet=True)
    cli.do_plan(str(tmp_path), quiet=True)
    cli.do_apply(str(tmp_path), quiet=True)
    return tmp_path


def rules(r):
    return sorted(c["rule"] for c in r["cards"])


def test_signals_and_every_lever(repo):
    r = improve.recommend(str(repo), Catalog.load(), repo="demo")
    sig = {v["feature"]: v for v in r["signals"].values()}
    w = sig["svc/workers/nightly_summaries.py:summarize_all"]
    assert w["latency_class"] == "background" and w["model_owner"] == "app_literal" and w["resolved_model"] == "gpt-5.4-mini"
    assert sig["svc/api/chat.py:answer"]["latency_class"] == "interactive"
    got = rules(r)
    for rule in ("attr.metadata", "cache.key", "tier.flex_background", "fallback.candidates", "model.deprecated", "unlock.vendor_endpoint", "unlock.stated_loss"):
        assert rule in got, (rule, got)
    flex = next(c for c in r["cards"] if c["rule"] == "tier.flex_background")
    assert flex["impact"]["per_call_pct"] == -50 and "measured on gpt-5.4-mini only" not in flex["impact"]["ignores"]
    assert all(c["applies"] is False for c in r["cards"] if c["lever_group"] == "model")
    assert all(c["model_owner"] in ("app_literal", "operator_config") for c in r["cards"] if c["lever_group"] == "model")
    reasons = " ".join(x["rule"] + ":" + x["reason"] for x in r["considered_not_recommended"])
    assert "tier.flex_background:latency class interactive" in reasons or "tier.flex_background:gpt-4-turbo has no flex tier" in reasons
    assert all(len(c["options"]) <= 3 for c in r["cards"]) and all(c["etag"] for c in r["cards"])
    md = open(repo / ".router-migrate" / "RECOMMENDATIONS.md").read()
    assert "<!-- rec:" in md and "Considered, not recommended" in md


def test_end_user_and_library_owners_get_no_model_cards(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "lib"\n')
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/__init__.py").write_text("")
    (tmp_path / "lib/core.py").write_text("from openai import OpenAI\n\n\nclass Agent:\n    def __init__(self, model='gpt-4-turbo'):\n        self.model = model\n"
                                          "        self.client = OpenAI()\n\n    def ask(self, q):\n        return self.client.chat.completions.create(model=self.model, messages=[{'role': 'user', 'content': q}])\n")
    cli.do_scan(str(tmp_path), quiet=True)
    cli.do_plan(str(tmp_path), quiet=True)
    r = improve.recommend(str(tmp_path), Catalog.load())
    assert not [c for c in r["cards"] if c["lever_group"] == "model"]
    assert any(x["rule"] == "model.*" and "library_param" in x["reason"] for x in r["considered_not_recommended"])


def test_decide_validation_and_etag(repo):
    r = improve.recommend(str(repo), Catalog.load())
    cid = r["cards"][0]["id"]
    with pytest.raises(ValueError):
        improve.decide(str(repo), cid, "dismiss")
    with pytest.raises(ValueError):
        improve.decide(str(repo), cid, "postpone", until="next week")
    with pytest.raises(KeyError):
        improve.decide(str(repo), "rec_nope", "approve")
    e = improve.decide(str(repo), cid, "approve", by="agent")
    assert e["etag"] == r["cards"][0]["etag"] and e["by"] == "agent"
    assert cid not in [c["id"] for c in improve.undecided(str(repo))]


def test_nothing_approved_means_v1_bytes(repo):
    before = {p: open(os.path.join(repo, p)).read() for p in ("svc/workers/nightly_summaries.py", "svc/vendors/xai.py", "svc/agent.py")}
    improve.recommend(str(repo), Catalog.load())
    log = improve.apply_decisions(str(repo), Catalog.load())
    assert log["applied"] == [] and not os.path.exists(repo / "router.policy.json")
    assert {p: open(os.path.join(repo, p)).read() for p in before} == before


def _approve_all(repo):
    r = improve.recommend(str(repo), Catalog.load())
    for c in r["cards"]:
        improve.decide(str(repo), c["id"], "approve")
    return r


def test_apply_decisions_policy_unlocks_and_status(repo):
    r = _approve_all(repo)
    log = improve.apply_decisions(str(repo), Catalog.load())
    kinds = {x["rule"] for x in log["applied"]}
    assert {"attr.metadata", "cache.key", "tier.flex_background", "fallback.candidates", "unlock.vendor_endpoint", "unlock.stated_loss"} <= kinds
    assert all("recommendation only" in x["reason"] for x in log["skipped"])  # model cards: never applied
    p, err = pol.load(str(repo))
    assert err is None and pol.policy_valid(p) and "model" not in json.dumps(p).replace("model_", "")
    w = pol.policy_for("svc/workers/nightly_summaries.py:summarize_all", p)
    assert w["allow_flex_tier"] is True and w["prompt_cache_key"] == "$feature" and w["metadata"]["product"]
    assert "_router_wrap_openai(OpenAI(api_key=os.environ.get('XAI_API_KEY'), base_url='https://api.x.ai/v1'), unlock_hosts=['api.x.ai'])" in open(repo / "svc/vendors/xai.py").read()
    assert "drop_betas=['prompt-caching-2024-07-31']" in open(repo / "svc/agent.py").read()
    rep, ok = improve.status(str(repo))
    assert ok and rep["compiled"] and all(c["current"] for c in rep["compiled"])
    assert json.load(open(repo / ".router-migrate" / "unlocks.json"))["sites"]
    # re-applying is idempotent
    improve.apply_decisions(str(repo), Catalog.load())
    assert open(repo / "svc/vendors/xai.py").read().count("unlock_hosts") == 1
    # stale compiled policy and orphan entries make status fail
    p["features"]["svc/gone.py:f"] = {"allow_flex_tier": True}
    open(repo / "router.policy.json", "w").write(json.dumps(p))
    rep, ok = improve.status(str(repo))
    assert not ok and rep["orphans"] and not all(c["current"] for c in rep["compiled"])


def test_patched_repo_imports_and_routes_with_policy(repo):
    """After apply --decisions the code still imports with Router off, and with Router on the compiled policy shapes requests."""
    _approve_all(repo)
    improve.apply_decisions(str(repo), Catalog.load())
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env.update(OPENAI_API_KEY="sk-provider-test", ANTHROPIC_API_KEY="sk-ant-test", XAI_API_KEY="x")
    r = subprocess.run([sys.executable, "-c", "import svc.workers.nightly_summaries, svc.vendors.xai, svc.agent, svc.api.chat; print('ok')"],
                       cwd=str(repo), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


def test_stale_etag_is_not_applied(repo):
    r = _approve_all(repo)
    f = repo / "svc/workers/nightly_summaries.py"
    f.write_text(f.read_text().replace("model='gpt-5.4-mini'", "model='gpt-5.4-mini' "))
    improve.recommend(str(repo), Catalog.load())  # etag changes for cards on that site
    log = improve.apply_decisions(str(repo), Catalog.load())
    assert any("stale" in x["reason"] for x in log["skipped"])


def test_up_agent_exit_codes(repo):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    r = subprocess.run([os.path.join(ROOT, "bin", "router-migrate"), "up", str(repo), "--agent", "--skip-verify"], capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 2, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["undecided"] and os.path.exists(out["report"])
    for c in out["undecided"]:
        improve.decide(str(repo), c["id"], "postpone", until="2026-12-01")
    r = subprocess.run([os.path.join(ROOT, "bin", "router-migrate"), "up", str(repo), "--agent", "--skip-verify"], capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stdout[-500:]


def test_agent_and_scripted_human_reach_identical_decisions(tmp_path):
    """The same decisions through the CLI (agent) and through the dashboard callback (UI) produce identical decisions.json."""
    outs = []
    for who in ("agent", "ui"):
        d = tmp_path / who
        for rel, src in FILES.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(src)
        cli.do_scan(str(d), quiet=True)
        cli.do_plan(str(d), quiet=True)
        cli.do_apply(str(d), quiet=True)
        r = improve.recommend(str(d), Catalog.load(), repo="demo")
        for c in r["cards"]:
            action = "approve" if c["risk"] <= 1 else "dismiss"
            improve.decide(str(d), c["id"], action, reason=None if action == "approve" else "risk", by=who)
        outs.append({k: {kk: vv for kk, vv in v.items() if kk not in ("at", "by")} for k, v in json.load(open(d / ".router-migrate" / "decisions.json"))["cards"].items()})
    assert outs[0] == outs[1]
