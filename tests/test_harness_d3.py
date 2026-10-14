"""D3 harness: the outside check re-derives everything from the tree (parse, scope incl. the vendored runtime, report,
tests vs baseline, Router-on run, "moved"), and `check` answers per call whether it routes and why not."""
import json
import os
import subprocess
import sys

import pytest

from test_agent_v4 import FILES, rm, at  # noqa: F401  (shared toy repo and CLI helper)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


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
    from router_migrate import agent as AG, harness as H
    base = {"test_cmd": "%s -m pytest -q -p no:cacheprovider tests" % sys.executable}
    b = AG.run_cmd(str(tmp_path), base["test_cmd"])
    base["before"] = {k: b.get(k) for k in ("passed", "failed", "errors", "exit")}
    AG.save_ledger(str(tmp_path), H._new_ledger(str(tmp_path), "min", base))
    return tmp_path


def report(repo, sites):
    (repo / ".router-migrate" / "MIGRATION.json").write_text(json.dumps({"sites": sites}))


def test_check_all_says_what_routes_and_why_not(repo):
    r = rm("check", "--all", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "route to Router now" in r.stdout and "svc/emb.py" in r.stdout
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    from router_migrate import harness as H
    rows = {(x["file"], x["routes"]) for x in H.check_sites(str(repo))}
    assert ("svc/direct.py", True) in rows and ("svc/emb.py", True) not in rows


def test_final_check_passes_a_clean_migration_and_rederives_moved(repo):
    from router_migrate import agent as AG, harness as H
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    report(repo, [{"site": at(repo, "svc/direct.py", "chat.completions"), "outcome": "migrated", "why": "default endpoint"},
                  {"site": at(repo, "svc/emb.py", "embeddings"), "outcome": "cannot_route", "why": "no embeddings endpoint"}])
    ok, fails, info = H.final_check(str(repo), AG.load_ledger(str(repo)))
    assert ok, fails
    assert any(m["file"] == "svc/direct.py" for m in info["moved"])


def test_final_check_rejects_test_edits_runtime_edits_and_lossy_entries_without_loses(repo):
    from router_migrate import agent as AG, harness as H
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    rt = repo / "svc" / "router_runtime.py"
    rt.write_text(rt.read_text() + "\n# hand edit\n")
    (repo / "tests" / "test_basic.py").write_text("def test_imports():\n    pass\n")
    report(repo, [{"site": at(repo, "svc/direct.py", "chat.completions"), "outcome": "migrated", "levers": ["drop seed"], "why": "x"}])
    ok, fails, _ = H.final_check(str(repo), AG.load_ledger(str(repo)))
    gates = {f["gate"] for f in fails}
    assert not ok and {"scope", "report"} <= gates, fails
    assert any("vendored runtime" in f["detail"] for f in fails) and any("test file" in f["detail"] for f in fails)


def test_missing_report_fails_min_but_not_free(repo):
    from router_migrate import agent as AG, harness as H
    ok, fails, _ = H.final_check(str(repo), AG.load_ledger(str(repo)))
    assert not ok and fails[0]["gate"] == "report"
    ok, fails, _ = H.final_check(str(repo), AG.load_ledger(str(repo)), require_report=False)
    assert ok, fails
