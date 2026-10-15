"""Golden before/after fixtures for the Python scanner + planner + codemods (driven by tests/rules_pass.py), plus
idempotency and an end-to-end run."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import rules_pass

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
    rules_pass.migrate(work)
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
    rules_pass.migrate(work)
    assert tree(work) == first


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


# ---- review regressions: imports that cannot fail (C2), per-node idempotency (M1), foreign router_runtime.py (M2)
def _write(root, files):
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


APP = "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n"


def _run(cwd, args, **env):
    e = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    e.update(OPENAI_API_KEY="sk-provider-test", **env)
    return subprocess.run([sys.executable] + args, cwd=str(cwd), env=e, capture_output=True, text=True, timeout=60)


def _migrate_dir(work):
    return rules_pass.migrate(work)


def test_namespace_package_import_still_works(tmp_path):
    """app/ has no __init__.py (PEP 420, the FastAPI layout): `import app.main` from the repo root must keep working."""
    _write(tmp_path, {"app/main.py": APP})
    _migrate_dir(tmp_path)
    assert "_router_wrap_openai(OpenAI())" in (tmp_path / "app/main.py").read_text()
    for env in ({}, {"RAMP_ROUTER_API_KEY": "rm-test-key-not-a-real-key", "RAMP_ROUTER_BASE_URL": "http://127.0.0.1:9"}):
        r = _run(tmp_path, ["-c", "import app.main as m; print(type(m.client._client._transport).__name__)"], **env)
        assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "RouterTransport"  # relative import works in a namespace package: Router really is on


def test_script_inside_package_still_runs(tmp_path):
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/sub/__init__.py": "", "pkg/sub/tool.py": APP + "\nprint('script ok')\n"})
    _migrate_dir(tmp_path)
    r = _run(tmp_path, ["pkg/sub/tool.py"])
    assert r.returncode == 0 and "script ok" in r.stdout, r.stderr


def test_foreign_router_runtime_is_never_overwritten(tmp_path):
    _write(tmp_path, {"svc/__init__.py": "", "svc/router_runtime.py": "def route(x):\n    return 'user code'\n", "svc/api.py": APP})
    log = _migrate_dir(tmp_path)
    assert (tmp_path / "svc/router_runtime.py").read_text().startswith("def route")
    assert "_router_" not in (tmp_path / "svc/api.py").read_text()
    plan = json.load(open(tmp_path / ".router-migrate" / "plan.json"))
    s = [x for x in plan["sites"] if x["file"] == "svc/api.py" and x["kind"] == "call"][0]
    assert s["outcome"] == "flagged" and "not router-migrate's" in s["reason"]
    assert any("not router-migrate's" in (k.get("reason") or "") for k in log["skipped"])


def test_rerun_keeps_outcomes_and_patches_new_clients(tmp_path):
    _write(tmp_path, {"svc/__init__.py": "", "svc/llm.py": APP})
    _migrate_dir(tmp_path)
    plan1 = json.load(open(tmp_path / ".router-migrate" / "plan.json"))
    _migrate_dir(tmp_path)
    plan2 = json.load(open(tmp_path / ".router-migrate" / "plan.json"))
    assert plan1["summary"] == plan2["summary"] and plan2["summary"]["migrated"] == 1
    src = (tmp_path / "svc/llm.py").read_text()
    src += ("\nfrom anthropic import Anthropic\n\nclaude = Anthropic()\n\n\ndef ask2(q):\n"
            "    return claude.messages.create(model='claude-haiku-4-5', max_tokens=50, messages=[{'role': 'user', 'content': q}])\n")
    (tmp_path / "svc/llm.py").write_text(src)
    _migrate_dir(tmp_path)
    out = (tmp_path / "svc/llm.py").read_text()
    assert "_router_wrap_anthropic(Anthropic())" in out and out.count("_router_wrap_openai(OpenAI())") == 1
    assert json.load(open(tmp_path / ".router-migrate" / "plan.json"))["summary"]["migrated"] == 2
    compile(out, "llm.py", "exec")


def test_planner_opts_reach_the_wrap(tmp_path):
    from router_migrate import apply_py
    _write(tmp_path, {"svc.py": APP})
    changed, done, skipped, _ = apply_py.patch_file(str(tmp_path), "svc.py", {(3, 9): ("wrap_openai", {"responses": False})}, set())
    assert changed and "_router_wrap_openai(OpenAI(), responses=False)" in (tmp_path / "svc.py").read_text()
