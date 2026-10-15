"""v6 harness end to end, with the agent's actions scripted: start (clean tree, branch), migrate + an improvement branch,
finish (outside check on every branch, never reverts), requests, the apply session (apply and undo, chosen by the agent),
apply --done, live checks through the recording pass-through."""
import json
import os
import subprocess
import sys

import pytest

from test_agent_v4 import FILES, at  # noqa: F401  (shared toy repo)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")
TEST_CMD = "%s -m pytest -q -p no:cacheprovider tests" % sys.executable


def rm(*args, cwd, env=None):
    e = {k: v for k, v in os.environ.items() if k not in ("RAMP_ROUTER_API_KEY",)}
    e.update(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="sk-ant-test")
    e.update(env or {})
    return subprocess.run([RM] + list(args), cwd=cwd, capture_output=True, text=True, env=e, timeout=600)


def git(repo, *args):
    r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    for rel, src in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def state(repo, name):
    return json.load(open(repo / ".router-migrate" / name))


def report(repo, items, calls=None):
    doc = {"version": 6, "summary": "Moved the direct OpenAI client to Router; embeddings stay.",
           "reviewed": "Model choice for the direct call.", "calls": calls or [], "items": items}
    (repo / ".router-migrate" / "MIGRATION.json").write_text(json.dumps(doc))


def migrate(repo):
    """What an agent does: wrap the client, commit, build an improvement branch, report."""
    r = rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Route the direct OpenAI client through Router")
    sha = git(repo, "rev-parse", "--short", "HEAD")
    git(repo, "checkout", "-q", "-b", "router-migrate/improve/newer-mini")
    p = repo / "svc" / "direct.py"
    p.write_text(p.read_text().replace("gpt-4o-mini", "gpt-4.1-mini"))
    git(repo, "commit", "-qam", "Try gpt-4.1-mini for ask")
    git(repo, "checkout", "-q", "router-migrate/migration")
    site = at(repo, "svc/direct.py", "chat.completions")
    report(repo, [
        {"id": "m1", "kind": "move", "title": "Route the direct OpenAI client", "summary": "The module client now goes through Router.",
         "calls": [site], "gains": "On Router", "loses": "nothing", "why": "Default endpoint, catalog model.", "commit": sha,
         "evidence": [{"check": "simulator", "result": "pass", "detail": "check --all: routes"}]},
        {"id": "s1", "kind": "stays", "title": "Embeddings stay on OpenAI", "summary": "Router has no embeddings endpoint.",
         "calls": [at(repo, "svc/emb.py", "embeddings")]},
        {"id": "i1", "kind": "improvement", "level": "quick-win", "title": "Newer mini model for ask", "summary": "ask moves to gpt-4.1-mini.",
         "calls": [site], "gains": "newer model", "loses": "a different model answers", "why": "Same price band, newer.",
         "branch": "router-migrate/improve/newer-mini"}],
        calls=[{"site": site, "feature": "ask", "model": "gpt-4o-mini", "task": "chat", "latency": "user_waiting", "now": "router", "item": "m1"}])
    return sha


def test_start_needs_a_clean_tree_and_creates_the_branch(repo):
    (repo / "svc" / "direct.py").write_text("# wip\n" + (repo / "svc" / "direct.py").read_text())
    r = rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo)
    assert r.returncode != 0 and "uncommitted" in (r.stdout + r.stderr)
    git(repo, "checkout", "--", ".")
    r = rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "router-migrate/migration"
    assert "svc/direct.py" in r.stdout and "No migration key" in r.stdout
    led = state(repo, "ledger.json")
    assert led["version"] == 6 and led["base"] and led["live"] is False
    assert ".router-migrate" not in git(repo, "status", "--porcelain")  # excluded from git
    assert rm("start", ".", cwd=repo).returncode == 0  # re-run on the branch continues


def test_finish_apply_and_undo_end_to_end(repo):
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    sha = migrate(repo)
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    v = state(repo, "validation.json")
    assert v["migration"]["ok"] and v["migration"]["tests"]["status"] == "passed"
    assert any("svc/direct.py" in s for s in v["migration"]["routes"]["verified"])
    assert v["branches"]["router-migrate/improve/newer-mini"]["ok"]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "router-migrate/migration"  # checkout untouched
    # the human approves the improvement; the apply session gets the whole list and decides how
    assert rm("request", "m1", "apply", cwd=repo).returncode != 0  # only improvements are applied
    assert rm("request", "i1", "apply", cwd=repo).returncode == 0
    r = rm("apply", ".", cwd=repo)
    assert r.returncode == 0 and "i1" in r.stdout and "router-migrate/improve/newer-mini" in r.stdout, r.stdout + r.stderr
    git(repo, "merge", "-q", "--no-edit", "router-migrate/improve/newer-mini")
    (repo / ".router-migrate" / "APPLY.json").write_text(json.dumps({"summary": "Merged the newer-mini branch.", "results": [
        {"item": "i1", "action": "apply", "status": "done", "commits": [git(repo, "rev-parse", "--short", "HEAD")], "note": "fast-forward"}]}))
    r = rm("apply", "--done", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    from router_migrate import report_v6 as R
    st = lambda i: R.item_status(next(x for x in state(repo, "MIGRATION.json")["items"] if x["id"] == i), state(repo, "requests.json"),
                                 state(repo, "apply_log.json"), state(repo, "validation.json"))
    assert st("i1") == "applied" and state(repo, "apply_log.json")["sessions"][0]["check"]["ok"]
    # an undo: the agent chooses how (here: revert the move's commit)
    assert rm("request", "m1", "undo", "--reason", "not yet", cwd=repo).returncode == 0
    r = rm("apply", ".", cwd=repo)
    assert "UNDO" in r.stdout and "not yet" in r.stdout
    git(repo, "revert", "--no-edit", sha)
    (repo / ".router-migrate" / "APPLY.json").write_text(json.dumps({"summary": "Reverted the move.", "results": [
        {"item": "m1", "action": "undo", "status": "done", "commits": [git(repo, "rev-parse", "--short", "HEAD")], "note": "git revert"}]}))
    r = rm("apply", "--done", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert st("m1") == "undone" and len(state(repo, "apply_log.json")["sessions"]) == 2


def test_finish_checks_outcomes_not_edit_categories(repo):
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    migrate(repo)
    (repo / "svc" / "config.py").unlink()  # a deletion: flagged for the human, not a failure
    (repo / "pyproject.toml").write_text((repo / "pyproject.toml").read_text().replace('["openai"]', '["openai", "httpx"]'))
    git(repo, "commit", "-qam", "drop config, add httpx")
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    v = state(repo, "validation.json")["migration"]
    kinds = {f["kind"] for f in v["flags"]}
    assert {"deleted-file", "dependencies"} <= kinds and v["ok"], r.stdout
    branch = state(repo, "validation.json")["branches"]["router-migrate/improve/newer-mini"]
    assert not any(f["kind"] in ("deleted-file", "dependencies") for f in branch["flags"]), branch["flags"]  # the migration's, not its own
    (repo / "tests" / "test_basic.py").write_text("def test_imports():\n    pass\n")  # editing an existing test: a failure
    git(repo, "commit", "-qam", "weaken test")
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 1 and "tests-edited" in r.stdout
    assert (repo / "tests" / "test_basic.py").read_text().startswith("def test_imports():\n    pass")  # nothing reverted


def test_finish_reports_the_exact_report_field_and_needs_commits(repo):
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    migrate(repo)
    doc = state(repo, "MIGRATION.json")
    doc["items"][0]["kind"] = "migrated"
    (repo / ".router-migrate" / "MIGRATION.json").write_text(json.dumps(doc))
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 1 and "items[m1].kind" in r.stdout
    (repo / "svc" / "emb.py").write_text("# uncommitted\n" + (repo / "svc" / "emb.py").read_text())
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 1 and "commit your work" in r.stdout


def test_live_check_needs_a_key_injects_it_and_scrubs_it(repo, tmp_path):
    from router_migrate.sim.server import serve
    from router_migrate.verify import SIM_KEY
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    migrate(repo)
    r = rm("check", "--run", "true", "--live", cwd=repo)
    assert r.returncode != 0 and "no migration key" in (r.stdout + r.stderr)
    srv, sim, base = serve()  # router-sim stands in for the real Router upstream
    try:
        env = {"ROUTER_MIGRATE_KEY": SIM_KEY, "ROUTER_MIGRATE_LIVE_UPSTREAM": base}
        cmd = "%s -c \"from svc.direct import ask; ask('hi')\"; echo key=$RAMP_ROUTER_API_KEY" % sys.executable
        r = rm("check", "--run", cmd, "--live", cwd=repo, env=env)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 request(s) to Router (1 ok" in r.stdout and SIM_KEY not in r.stdout and "[migration key]" in r.stdout
    led = state(repo, "ledger.json")
    assert led["live_probes"][-1]["requests"][0]["status"] == 200


def test_launch_apply_falls_back_to_the_claude_code_command(repo, monkeypatch):
    from router_migrate import harness as H
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    migrate(repo)
    assert rm("request", "i1", "apply", cwd=repo).returncode == 0
    monkeypatch.setattr(H, "_claude_logged_in", lambda: False)
    assert H.launch_apply(str(repo)) == {"mode": "command", "command": "/router-migrate apply"}


def test_policy_hook_in_the_vendored_ts_runtime_is_not_a_hand_edit(tmp_path):
    from router_migrate import harness as H
    from router_migrate.catalog import Catalog
    from router_migrate.runtime import render
    (tmp_path / "lib").mkdir()
    rt = tmp_path / "lib" / "routerRuntime.ts"
    rt.write_text(render("routerRuntime.ts", Catalog.load()) + '\nimport "./routerPolicy"; // router-migrate: compiled policy (router.policy.json)\n')
    assert not H._runtime_edited(str(tmp_path), "lib/routerRuntime.ts")
    rt.write_text(rt.read_text().replace("export function", "export  function", 1))
    assert H._runtime_edited(str(tmp_path), "lib/routerRuntime.ts")


def test_policy_hook_copies_the_import_the_agent_wrote(tmp_path):
    from router_migrate import improve
    git(tmp_path, "init", "-q")
    (tmp_path / "client.ts").write_text("import { routerOptions } from './lib/routerRuntime.js';\n")
    git(tmp_path, "add", "-A")
    line, name = improve._ts_policy_import(str(tmp_path), "lib/routerRuntime.ts")
    assert '"./routerPolicy.js"' in line and name == "routerPolicy.ts"


def test_adapt_and_undo_still_work_as_optional_helpers(repo):
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    site = at(repo, "svc/direct.py", "chat.completions")
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    r = rm("adapt", site, "--drop", "seed", "--loses", "no fixed seed", "--why", "Router rejects seed", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "seed" in (repo / "router.policy.json").read_text()
    assert rm("adapt", "--undo", "T1", cwd=repo).returncode == 0
    assert "seed" not in (repo / "router.policy.json").read_text()


def test_caller_feature_label_wins_in_the_python_runtime(rt):
    import httpx
    r = httpx.Request("POST", "https://api.anthropic.com/v1/messages", headers={"x-router-migrate-feature": "composer"},
                      content=json.dumps({"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}).encode())
    route = rt.plan_request(r, {})
    assert route and route[2].get("feature") == "composer"


def test_a_hand_edited_policy_must_be_compiled(repo):
    assert rm("start", ".", "--test-cmd", TEST_CMD, cwd=repo).returncode == 0
    migrate(repo)
    site = at(repo, "svc/direct.py", "chat.completions")
    assert rm("adapt", site, "--drop", "seed", "--loses", "no fixed seed", "--why", "Router rejects seed", cwd=repo).returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "drop seed")
    p = json.load(open(repo / "router.policy.json"))
    p.setdefault("defaults", {})["metadata"] = {"product": "zoo"}
    (repo / "router.policy.json").write_text(json.dumps(p))
    git(repo, "commit", "-qam", "spend tags")
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 1 and "[policy]" in r.stdout and "router-migrate policy" in r.stdout
    assert rm("policy", cwd=repo).returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "compile policy")
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode == 0, r.stdout
