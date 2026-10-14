"""v3 Improve phase: after the migration gate, `improve` builds cards across every Router lever from the code as it stands plus
the agent's judgments (`judge`: latency, task, cacheability, each with evidence), keeps tool facts and agent rationale apart,
gates the improve session (--hook), and the dashboard bundle carries the whole run and a repo-specific setup checklist."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")
LONG = "You are a careful summarizer of support tickets. " * 150

FILES = {
    "pyproject.toml": '[project]\nname = "imp"\ndependencies = ["openai"]\n',
    "app/__init__.py": "",
    "app/jobs.py": "from openai import OpenAI\n\nSYSTEM_PROMPT = \"\"\"%s\"\"\"\nclient = OpenAI()\n\n\ndef digest(tickets):\n"
                   "    return client.chat.completions.create(model='gpt-5.4-mini', messages=[{'role': 'system', 'content': SYSTEM_PROMPT},"
                   " {'role': 'user', 'content': tickets}])\n" % LONG,
    "app/cron.py": "from .jobs import digest\n\n\ndef nightly():\n    return digest('...')\n",
    "tests/test_basic.py": "def test_imports():\n    import app.jobs, app.cron  # noqa\n",
}


def rm(*args, cwd, stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env["OPENAI_API_KEY"] = "sk-test"
    return subprocess.run([RM] + list(args), cwd=cwd, capture_output=True, text=True, env=env, input=stdin, timeout=600)


def at(repo, rel, needle):
    for i, l in enumerate((repo / rel).read_text().splitlines(), 1):
        if needle in l:
            return "%s:%d" % (rel, i)
    raise AssertionError(needle)


@pytest.fixture()
def repo(tmp_path):
    for rel, src in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=tmp_path, capture_output=True, check=True)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    r = rm("run", str(tmp_path), "--skip-verify", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    from conftest import accept_proposals
    accept_proposals(tmp_path, rm)  # v4: the agent accepts the rules' proposals (v3 applied them before the agent)
    return tmp_path


def test_improve_lists_features_and_hook_blocks_until_judged(repo):
    out = json.loads(rm("improve", ".", "--json", cwd=repo).stdout)
    assert out["ok"] is False and [f["feature"] for f in out["features_to_judge"]] == ["app/jobs.py:digest"]
    d = json.loads(rm("improve", ".", "--hook", cwd=repo, stdin="{}").stdout)
    assert d["decision"] == "block" and "app/jobs.py:digest" in d["reason"]


def test_judge_validates(repo):
    loc = at(repo, "app/jobs.py", "completions.create")
    r = rm("judge", loc, "latency", "sometimes", "--why", "x", "--evidence", "app/cron.py:4", cwd=repo)
    assert r.returncode == 1 and "background" in r.stdout
    r = rm("judge", loc, "latency", "background", "--why", "x", cwd=repo)
    assert r.returncode == 1 and "evidence" in r.stdout
    r = rm("judge", loc, "cacheable", "lots", "--why", "x", "--evidence", "app/jobs.py:3", cwd=repo)
    assert r.returncode == 1 and "token count" in r.stdout


def test_judgments_drive_cards_with_rationale_apart_from_facts(repo):
    loc = at(repo, "app/jobs.py", "completions.create")
    for key, val, ev in (("latency", "background", "app/cron.py:4"), ("task", "summarization", "app/jobs.py:3"), ("cacheable", "1800", "app/jobs.py:3")):
        r = rm("judge", loc, key, val, "--why", "read the code", "--evidence", ev, cwd=repo)
        assert r.returncode == 0, r.stdout
    out = json.loads(rm("improve", ".", "--json", cwd=repo).stdout)
    assert out["ok"] is True and not out["features_to_judge"]
    d = json.loads(rm("improve", ".", "--hook", cwd=repo, stdin="{}").stdout)
    assert "decision" not in d
    r = json.load(open(repo / ".router-migrate" / "recommendations.json"))
    by = {c["rule"]: c for c in r["cards"]}
    flex, cache = by["tier.flex_background"], by["cache.key"]
    assert flex["tool_facts"] == ["gpt-5.4-mini has a measured flex tier"]
    assert flex["agent_rationale"] and flex["agent_rationale"][0]["evidence"] == ["app/cron.py:4"]
    assert any("cached input" in f for f in cache["tool_facts"]) and cache["agent_rationale"]
    assert cache["impact"]["detail"].startswith("-")
    assert {"attr.metadata", "fallback.candidates"} <= set(by)
    # Flex and fallbacks on one feature are either/or (Router drops Flex for a models list): both cards say so up front
    fb = by["fallback.candidates"]
    assert flex["conflicts_with"] == [fb["id"]] and fb["conflicts_with"] == [flex["id"]]
    assert "Either this or the fallback card" in flex["conflict_note"] and "Flex" in fb["conflict_note"]
    # the agent's latency replaced the heuristic in the signals
    sig = next(iter(r["signals"].values()))
    assert sig["sources"]["latency_class"] == "agent"


def test_setup_and_bundle_carry_the_run(repo):
    loc = at(repo, "app/jobs.py", "completions.create")
    rm("judge", loc, "latency", "background", "--why", "cron", "--evidence", "app/cron.py:4", cwd=repo)
    rm("judge", loc, "task", "summarization", "--why", "digest", "--evidence", "app/jobs.py:3", cwd=repo)
    rm("improve", ".", cwd=repo)
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    b = improve.bundle(str(repo))
    setup = {i["id"]: i for i in b["setup"]}
    assert setup["setup.flex_default"]["applies_to"] == ["app/jobs.py:digest"]
    assert setup["setup.switchyard"].get("na") is True
    assert b["run"]["judgments"]["app/jobs.py:digest"]["latency"]["value"] == "background"
    assert b["run"]["routes_now"] >= 1 and b["tool_version"] == "router-migrate v3"


def test_budget_item_is_repo_specific(repo):
    loc = at(repo, "app/jobs.py", "completions.create")
    rm("judge", loc, "latency", "background", "--why", "cron", "--evidence", "app/cron.py:4", cwd=repo)
    rm("judge", loc, "task", "summarization", "--why", "digest", "--evidence", "app/jobs.py:3", cwd=repo)
    rm("judge", loc, "cacheable", "1800", "--why", "fixed system prompt", "--evidence", "app/jobs.py:3", cwd=repo)
    rm("improve", ".", cwd=repo)
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    budget = next(i for i in improve.bundle(str(repo))["setup"] if i["id"] == "setup.budget")
    v = budget["values"]
    assert "gpt-5.4-mini" in v["list prices"] and "per M tokens" in v["list prices"]
    assert "per 1,000 calls" in v["per-call estimates"] and "assumed" in v["per-call estimates"]
    assert v["watch spend by"].startswith("metadata.product=")
    assert "no cap is invented" in budget["why"]
    assert v["features behind this key"] == "1"  # only features that route through Router
    assert v["per-call estimates"].count("per 1,000 calls") == 1


def test_snapshot_is_safe_under_concurrent_calls(repo):
    """The live dashboard snapshots (bundle refresh) while Apply runs the gate; the state dir must never leak into a tree."""
    sys.path.insert(0, ROOT)
    from concurrent.futures import ThreadPoolExecutor
    from router_migrate import agent
    for i in range(20):
        (repo / ".router-migrate" / ("noise%d.json" % i)).write_text("{}")
    with ThreadPoolExecutor(8) as ex:
        trees = list(ex.map(lambda _: agent.snapshot_tree(str(repo)), range(24)))
    assert len(set(trees)) == 1
    ls = subprocess.run(["git", "ls-tree", "-r", "--name-only", trees[0]], cwd=repo, capture_output=True, text=True).stdout
    assert ".router-migrate" not in ls and "app/jobs.py" in ls
    assert not [f for f in os.listdir(repo / ".router-migrate") if f.startswith("snap.")]


def test_apply_twice_reports_earlier_cards_and_audits_the_policy(repo):
    loc = at(repo, "app/jobs.py", "completions.create")
    rm("judge", loc, "latency", "background", "--why", "cron", "--evidence", "app/cron.py:4", cwd=repo)
    rm("judge", loc, "task", "summarization", "--why", "digest", "--evidence", "app/jobs.py:3", cwd=repo)
    rm("improve", ".", cwd=repo)
    r = json.load(open(repo / ".router-migrate" / "recommendations.json"))
    by = {c["rule"]: c for c in r["cards"]}
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    from router_migrate.catalog import Catalog
    improve.decide(str(repo), by["attr.metadata"]["id"], "approve", by="ui")
    first = improve.apply_decisions(str(repo), Catalog.load())
    assert [x["card"] for x in first["applied"]] == [by["attr.metadata"]["id"]] and not first["already_applied"]
    improve.decide(str(repo), by["fallback.candidates"]["id"], "approve", by="ui")
    second = improve.apply_decisions(str(repo), Catalog.load())
    assert [x["card"] for x in second["applied"]] == [by["fallback.candidates"]["id"]]
    assert second["already_applied"] == [by["attr.metadata"]["id"]]
    pol = json.load(open(repo / "router.policy.json"))
    assert pol["defaults"]["metadata"]["product"] and pol["features"]["app/jobs.py:digest"]["fallbacks"]
    led = json.load(open(repo / ".router-migrate" / "ledger.json"))
    edits = [e for e in led["edits"] if e["by"] == "decisions"]
    assert len(edits) == 2 and "router.policy.json" in edits[0]["files"] and edits[1]["cards"] == [by["fallback.candidates"]["id"]]


def test_applied_unlock_stays_applied_after_its_call_moves(tmp_path):
    """Applying an unlock patches the client, which shifts the call's line, so the rebuilt card has a new site id and card id.
    It must come back as applied (matched by feature), not as a new card waiting for a decision."""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_v2 import FILES as V2
    for rel, src in list(V2.items()) + [("tests/test_smoke.py", "def test_ok():\n    assert True\n")]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=tmp_path, capture_output=True, check=True)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    assert rm("run", str(tmp_path), "--skip-verify", cwd=tmp_path).returncode == 0
    from conftest import accept_proposals
    accept_proposals(tmp_path, rm)
    rm("improve", ".", cwd=tmp_path)
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    from router_migrate.catalog import Catalog
    r = json.load(open(tmp_path / ".router-migrate" / "recommendations.json"))
    unl = [c for c in r["cards"] if c["rule"] == "unlock.vendor_endpoint"]
    assert unl
    improve.decide(str(tmp_path), unl[0]["id"], "approve", by="ui")
    log = improve.apply_decisions(str(tmp_path), Catalog.load())
    assert [x["rule"] for x in log["applied"]] == ["unlock.vendor_endpoint"]
    rm("improve", ".", cwd=tmp_path)
    r = json.load(open(tmp_path / ".router-migrate" / "recommendations.json"))
    now = [c for c in r["cards"] if c["rule"] == "unlock.vendor_endpoint"]
    assert all(c["state"] == "applied" for c in now)
    assert not [c for c in improve.undecided(str(tmp_path)) if c["rule"] == "unlock.vendor_endpoint"]
    # the unlocked feature now routes through the same key: the budget counts and prices it
    budget = next(i for i in improve.bundle(str(tmp_path))["setup"] if i["id"] == "setup.budget")["values"]
    assert "grok-4.3" in budget["list prices"] and "xai.py:generate" in budget["list prices"]
    assert int(budget["features behind this key"]) >= 3


def test_unlock_on_an_already_wrapped_client_adds_its_option_once(tmp_path):
    """When the client is already wrapped (in v4: the agent wrapped it), the stated-loss unlock must add drop_betas to that
    wrap, not wrap it again."""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_v2 import FILES as V2
    for rel, src in list(V2.items()) + [("tests/test_smoke.py", "def test_ok():\n    assert True\n")]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=tmp_path, capture_output=True, check=True)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    assert rm("run", str(tmp_path), "--skip-verify", cwd=tmp_path).returncode == 0
    from conftest import accept_proposals
    accept_proposals(tmp_path, rm)
    ln = next(i for i, l in enumerate((tmp_path / "svc/agent.py").read_text().splitlines(), 1) if l.startswith("client = Anthropic()"))
    assert rm("wrap", "svc/agent.py:%d" % ln, cwd=tmp_path).returncode == 0  # v4: the beta-only client is wrapped by the agent
    assert "_router_wrap_anthropic(Anthropic())" in (tmp_path / "svc/agent.py").read_text()
    rm("improve", ".", cwd=tmp_path)
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    from router_migrate.catalog import Catalog
    r = json.load(open(tmp_path / ".router-migrate" / "recommendations.json"))
    card = next(c for c in r["cards"] if c["rule"] == "unlock.stated_loss")
    improve.decide(str(tmp_path), card["id"], "approve", by="ui")
    improve.apply_decisions(str(tmp_path), Catalog.load())
    line = next(l for l in (tmp_path / "svc/agent.py").read_text().splitlines() if l.startswith("client ="))
    assert line == "client = _router_wrap_anthropic(Anthropic(), drop_betas=['prompt-caching-2024-07-31'])", line


def _with_tradeoffs(repo):
    """v4 migrate-by-default: the agent took three moves at a cost (one later undone, one a hand edit) and recorded them."""
    site = at(repo, "app/jobs.py", "completions.create")
    led_p = repo / ".router-migrate" / "ledger.json"
    led = json.load(open(led_p))
    base = {"site": site, "file": "app/jobs.py", "line": int(site.split(":")[1]), "feature": "app/jobs.py:digest", "at": "2026-10-14T00:00:00"}
    led["tradeoffs"] = [
        dict(base, id="T1", levers={"drop_params": ["seed"]}, loses="seed: replies are no longer reproducible run to run",
             why="Router rejects seed; nothing reads the seeded output", evidence=["app/jobs.py:8"], undone=False),
        dict(base, id="T2", levers={}, loses="the retry wrapper's custom header", why="moved by hand", evidence=["app/jobs.py:9"], undone=False),
        dict(base, id="T3", levers={"model_map": {"gpt-x": "gpt-5.4-mini"}}, loses="gpt-x", why="not in catalog", evidence=["PR2"], undone=True)]
    json.dump(led, open(led_p, "w"))
    rm("judge", site, "latency", "background", "--why", "cron", "--evidence", "app/cron.py:4", cwd=repo)
    rm("judge", site, "task", "summarization", "--why", "digest", "--evidence", "app/jobs.py:3", cwd=repo)
    rm("improve", ".", cwd=repo)
    return {c["id"]: c for c in json.load(open(repo / ".router-migrate" / "recommendations.json"))["cards"]}


def test_tradeoffs_become_applied_cards_and_reach_the_page(repo):
    cards = _with_tradeoffs(repo)
    t1, t2, t3 = cards["tradeoff:T1"], cards["tradeoff:T2"], cards["tradeoff:T3"]
    assert t1["rule"] == "tradeoff.drop_params" and t1["lever_group"] == "tradeoff" and t1["state"] == "applied" and t1["applies"] is False
    assert t1["title"] == "Moved to Router: app/jobs.py:digest (drops seed)" and t1["loses"] == ["seed: replies are no longer reproducible run to run"]
    assert json.loads(t1["change"]["diff"]) == {"features": {"app/jobs.py:digest": {"drop_params": ["seed"]}}}
    assert t1["evidence"]["results"][0]["source"] == "app/jobs.py:8"
    assert t2["rule"] == "tradeoff.edit" and "hand edit in app/jobs.py" in t2["change"]["diff"]
    assert t3["state"] == "undone"
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    assert not [c for c in improve.undecided(str(repo)) if c["rule"].startswith("tradeoff.")]  # already applied: nothing to decide
    b = improve.bundle(str(repo))
    assert [t["id"] for t in b["tradeoffs"]] == ["T1", "T2", "T3"]
    m = b["moved"]  # one call on Router with two live trade-offs (T3 is undone): counted once, from the calls the page lists
    assert (m["moved"], m["with_tradeoffs"], m["stay"], m["total"]) == (1, 1, 0, 1)
    (call,) = [c for c in b["view"]["calls"] if c["routes"]]
    assert call["status"] == "tradeoff" and [t["card"] for t in call["tradeoffs"]] == ["tradeoff:T1", "tradeoff:T2", "tradeoff:T3"]
    assert [t["undone"] for t in call["tradeoffs"]] == [False, False, True] and call["tradeoffs"][0]["levers"] == "drops seed"
    imps = {i["id"]: i for i in b["view"]["improvements"]}
    v1 = imps["tradeoff:T1"]
    assert v1["kind"] == "tradeoff" and v1["lever"] == "tradeoff" and v1["tradeoffs"] == t1["loses"]
    assert {"op": "del", "key": "seed", "v": "(dropped)"} in v1["request"]
    assert v1["code"][0]["file"] == "router.policy.json" and v1["code"][0]["after"] == ['"drop_params": ["seed"]']
    assert v1["why"].startswith("Router rejects seed") and v1["evidence"] == ["app/jobs.py:8"] and not v1["undone"]
    assert imps["tradeoff:T2"]["hand_edit"] and imps["tradeoff:T3"]["undone"] and imps["tradeoff:T3"]["impact"]["label"] == "Undone"


def test_dismissing_a_tradeoff_undoes_it_on_apply(repo, monkeypatch):
    _with_tradeoffs(repo)
    sys.path.insert(0, ROOT)
    from router_migrate import agent, improve
    from router_migrate.catalog import Catalog
    calls = []

    def undo(root, tid):  # stand-in for agent.undo_tradeoff: reverse the levers, mark it undone, return the files changed
        calls.append(tid)
        p = os.path.join(root, ".router-migrate", "ledger.json")
        led = json.load(open(p))
        t = next(x for x in led["tradeoffs"] if x["id"] == tid)
        if not t["levers"]:
            return []
        t["undone"] = True
        json.dump(led, open(p, "w"))
        open(os.path.join(root, "app/jobs.py"), "a").write("# seed restored\n")
        return ["app/jobs.py"]

    monkeypatch.setattr(agent, "undo_tradeoff", undo, raising=False)
    improve.decide(str(repo), "tradeoff:T1", "dismiss", reason="undo trade-off", by="ui")
    improve.decide(str(repo), "tradeoff:T2", "dismiss", reason="undo trade-off", by="ui")
    log = improve.apply_decisions(str(repo), Catalog.load())
    assert calls == ["T1"]  # a hand edit is never handed to undo: nothing can reverse it automatically
    assert {"card": "tradeoff:T1", "rule": "tradeoff.drop_params", "kind": "undo", "files": ["app/jobs.py"]} in log["applied"]
    assert any(x["card"] == "tradeoff:T2" and "hand edit in app/jobs.py" in x["reason"] for x in log["skipped"])
    led = json.load(open(repo / ".router-migrate" / "ledger.json"))
    edit = [e for e in led["edits"] if e["by"] == "decisions"][-1]
    assert "app/jobs.py" in edit["files"] and "tradeoff:T1" in edit["cards"]
    again = improve.apply_decisions(str(repo), Catalog.load())  # undone once: never undone twice
    assert "tradeoff:T1" in again["already_applied"] and calls.count("T1") == 1
    rm("improve", ".", cwd=repo)
    cards = {c["id"]: c for c in json.load(open(repo / ".router-migrate" / "recommendations.json"))["cards"]}
    assert cards["tradeoff:T1"]["state"] == "undone" and cards["tradeoff:T2"]["state"] == "dismissed"


def test_undo_reverses_a_real_adapt_end_to_end(repo):
    """The agent drops seed with `adapt`; the dashboard's Undo (dismiss) + Apply takes it back out of the compiled policy."""
    site = at(repo, "app/jobs.py", "completions.create")
    r = rm("adapt", site, "--drop", "seed", "--loses", "seed: no reproducible replies", "--why", "Router rejects seed", "--evidence", site, "--json", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    tid = json.loads(r.stdout)["tradeoff"]["id"]
    rm("improve", ".", cwd=repo)
    sys.path.insert(0, ROOT)
    from router_migrate import improve
    from router_migrate.catalog import Catalog
    assert "seed" in (repo / "router.policy.json").read_text()
    improve.decide(str(repo), "tradeoff:" + tid, "dismiss", reason="undo trade-off", by="ui")
    log = improve.apply_decisions(str(repo), Catalog.load())
    undo = next(x for x in log["applied"] if x["card"] == "tradeoff:" + tid)
    assert undo["kind"] == "undo" and "router.policy.json" in undo["files"]
    assert "seed" not in (repo / "router.policy.json").read_text()
    led = json.load(open(repo / ".router-migrate" / "ledger.json"))
    assert next(t for t in led["tradeoffs"] if t["id"] == tid)["undone"]
    assert "tradeoff:" + tid in [e for e in led["edits"] if e["by"] == "decisions"][-1]["cards"]
