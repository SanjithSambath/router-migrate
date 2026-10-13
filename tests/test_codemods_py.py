"""Golden before/after fixtures for the Python scanner + planner + codemods, plus idempotency and an end-to-end run."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from router_migrate import cli

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "py")
CASES = sorted(os.listdir(FIX))


def tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".router-migrate", "__pycache__")]
        for f in files:
            if f != "router_runtime.py":
                p = os.path.join(d, f)
                out[os.path.relpath(p, root)] = open(p).read()
    return out


def migrate(tmp_path, case):
    work = tmp_path / case
    shutil.copytree(os.path.join(FIX, case, "before"), work)
    cli.do_scan(str(work), quiet=True)
    cli.do_plan(str(work), quiet=True)
    cli.do_apply(str(work), quiet=True)
    return work


@pytest.mark.parametrize("case", CASES)
def test_golden(tmp_path, case):
    work = migrate(tmp_path, case)
    assert tree(work) == tree(os.path.join(FIX, case, "after"))
    exp = json.load(open(os.path.join(FIX, case, "expect.json")))
    plan = json.load(open(os.path.join(work, ".router-migrate", "plan.json")))
    assert {"%s:%d" % (s["file"], s["line"]): s["outcome"] for s in plan["sites"]} == exp["outcomes"]
    rts = sorted(os.path.relpath(os.path.join(d, f), work) for d, _, fs in os.walk(work) for f in fs if f == "router_runtime.py")
    assert rts == exp["runtime_files"]
    for rel in tree(work):
        if rel.endswith(".py"):
            compile(open(os.path.join(work, rel)).read(), rel, "exec")


@pytest.mark.parametrize("case", CASES)
def test_idempotent(tmp_path, case):
    work = migrate(tmp_path, case)
    first = tree(work)
    cli.do_scan(str(work), quiet=True)
    cli.do_plan(str(work), quiet=True)
    cli.do_apply(str(work), quiet=True)
    assert tree(work) == first


def test_dry_run_changes_nothing(tmp_path, capsys):
    work = tmp_path / "w"
    shutil.copytree(os.path.join(FIX, "openai_basic", "before"), work)
    before = tree(work)
    cli.main(["plan", str(work), "--dry-run"])
    out = capsys.readouterr().out
    assert "+client = _router_wrap_openai(OpenAI())" in out
    assert tree(work) == before


def test_patched_code_runs_against_router_sim(tmp_path, sim):
    """scan -> plan -> apply on a fixture app, then execute the app with Router on: the request must reach router-sim valid."""
    work = migrate(tmp_path, "openai_basic")
    sim.records.clear()
    env = dict(os.environ, RAMP_ROUTER_API_KEY="rm-test-key-not-a-real-key", RAMP_ROUTER_BASE_URL=sim.base, OPENAI_API_KEY="sk-provider-test")
    code = "import app; print(app.ask('hi'))"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(work), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "pong"
    rec = sim.records[-1]
    assert rec["path"] == "/v1/responses" and rec["valid"] and rec["request"]["metadata"]["feature"] == "app.py:ask"


def test_patched_code_unchanged_without_key(tmp_path):
    """Without RAMP_ROUTER_API_KEY the patched client is the original object (no transport, no requests to Router)."""
    work = migrate(tmp_path, "openai_basic")
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env["OPENAI_API_KEY"] = "sk-provider-test"
    code = "import app, openai; c = app.client; print(type(c).__name__, c._client.__class__.__name__, str(c.base_url))"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(work), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "OpenAI" and "api.openai.com" in r.stdout
