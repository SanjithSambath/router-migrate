"""The whole CLI on a fixture repo: run (scan, plan, apply, verify with a real test command, report)."""
import json
import os
import shutil
import subprocess
import sys

from router_migrate import cli

HERE = os.path.dirname(os.path.abspath(__file__))


def test_run_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-test")
    work = tmp_path / "repo"
    shutil.copytree(os.path.join(HERE, "fixtures", "py", "openai_basic", "before"), work)
    (work / "test_app.py").write_text("import app\n\ndef test_client_exists():\n    assert app.client is not None\n")
    subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=work, check=True)
    cli.main(["run", str(work), "--test-cmd", "%s -m pytest -q -p no:cacheprovider test_app.py" % sys.executable])
    out = capsys.readouterr().out
    assert "migrated" in out and "verify:" in out
    v = json.load(open(work / ".router-migrate" / "verify.json"))
    assert v["tests_before"]["passed"] == 1 and v["tests_after"]["passed"] == 1
    assert v["syntax"]["failed"] == []
    assert all(r["ok"] for r in v["sim_replay"]["results"] if r.get("ok") is not None)
    rep = json.load(open(work / ".router-migrate" / "report.json"))
    assert rep["summary"]["migrated"] == 1 and rep["summary"]["kept"] == 1
    md = open(work / ".router-migrate" / "REPORT.md").read()
    assert "| 2 | 1 | 0 | 1 | 0 |" in md and "Verification" in md
    # the baseline run used git stash and restored the changes afterwards
    assert "_router_wrap_openai" in open(work / "app.py").read()
    assert subprocess.run("git stash list", shell=True, cwd=work, capture_output=True, text=True).stdout == ""


def test_scan_json_and_help(tmp_path, capsys):
    work = tmp_path / "r"
    shutil.copytree(os.path.join(HERE, "fixtures", "py", "langchain", "before"), work)
    cli.main(["scan", str(work), "--json"])
    out = capsys.readouterr().out
    assert '"kind": "client"' in out
    assert cli.main([]) == 0
