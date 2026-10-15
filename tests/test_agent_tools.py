"""The agent's optional code tools on a toy repo after `start`: unwrap inverts wrap exactly; `check --run` sees requests the
scanner can't attribute (a client imported from another module, a native client, a framework client built in a helper),
stays offline, and works from a subdirectory; a client lever taken with `adapt` re-wraps the client and undo leaves it
wrapped without the option; git snapshots are safe under concurrent calls. (Ported from the v4 tests, now in
archive/tests/test_agent_v4.py.)"""
import json
import os
import shlex
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")
sys.path.insert(0, ROOT)
TEST_CMD = "%s -m pytest -q -p no:cacheprovider tests" % sys.executable

FILES = {
    "pyproject.toml": '[project]\nname = "zoo"\ndependencies = ["openai"]\n',
    "svc/__init__.py": "",
    "svc/direct.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n"
                     "    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n",
    "svc/crossmod.py": "from .direct import client\n\n\ndef again(q):\n"
                       "    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n",
    "svc/factory.py": "from openai import OpenAI\n\n\ndef make(key):\n    return OpenAI(api_key=key)\n",
    "svc/hidden.py": "from .factory import make\n\n\ndef go(q, key):\n"
                     "    return make(key).chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n",
    "svc/emb.py": "from openai import OpenAI\n\nc = OpenAI()\n\n\ndef e(t):\n    return c.embeddings.create(model='text-embedding-3-small', input=t)\n",
    "svc/claude.py": "from anthropic import Anthropic\n\nclaude = Anthropic()\n\n\ndef classify(t):\n"
                     "    return claude.messages.create(model='claude-haiku-4-5', max_tokens=16, messages=[{'role': 'user', 'content': t}])\n",
    "svc/config.py": "import os\n\nKEY = os.environ.get('OPENAI_API_KEY')\n",
    "tests/test_basic.py": "def test_imports():\n    import svc.direct, svc.crossmod, svc.hidden, svc.emb, svc.claude  # noqa\n",
}


def rm(*args, cwd):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env.update(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="sk-ant-test")
    return subprocess.run([RM] + list(args), cwd=cwd, capture_output=True, text=True, env=env, timeout=600)


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
    r = rm("start", ".", "--test-cmd", TEST_CMD, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path


def at(repo, rel, needle):
    for i, l in enumerate((repo / rel).read_text().splitlines(), 1):
        if needle in l:
            return "%s:%d" % (rel, i)
    raise AssertionError(needle)


def ledger(repo):
    return json.load(open(repo / ".router-migrate" / "ledger.json"))


PROBE = "%s -c \"from svc.crossmod import again; from svc.claude import classify; again('hi'); classify('hi')\"" % sys.executable


def test_unwrap_inverts_wrap_exactly(repo):
    before = (repo / "svc/direct.py").read_text()
    r = rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo)
    assert r.returncode == 0 and "_router_wrap_openai(OpenAI())" in (repo / "svc/direct.py").read_text()
    r = rm("unwrap", at(repo, "svc/direct.py", "_router_wrap_openai(OpenAI())"), cwd=repo)
    assert r.returncode == 0, r.stdout
    assert (repo / "svc/direct.py").read_text() == before
    assert [e["by"] for e in ledger(repo)["edits"]][-2:] == ["agent-wrap", "agent-unwrap"]


def test_check_run_confirms_what_the_scanner_cannot_see_and_native(repo):
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    r = rm("wrap", at(repo, "svc/claude.py", "Anthropic()"), "--native", cwd=repo)
    assert r.returncode == 0, r.stdout
    cross = at(repo, "svc/crossmod.py", "chat.completions")
    native = at(repo, "svc/claude.py", "messages.create")
    r = rm("check", "%s,%s" % (cross, native), "--run", PROBE, cwd=repo)
    assert r.returncode == 0, r.stdout
    assert "%s: confirmed by PR1" % cross in r.stdout and "%s: confirmed by PR1 (native)" % native in r.stdout, r.stdout


def test_check_run_attributes_framework_clients_stays_offline_and_runs_from_a_subdirectory(repo):
    """A framework call starts in library code, so the request's calling frame is not the site: the client it went through
    names it (where the wrapped client was built). Provider traffic is blocked at the sim (nothing leaves the machine), and
    the commands find the ledger from a subdirectory with the repo's `python` first on PATH."""
    (repo / "svc" / "fw.py").write_text("from openai import OpenAI\n\n\ndef build():\n    return OpenAI(max_retries=0)\n")
    assert rm("wrap", at(repo, "svc/fw.py", "OpenAI(max_retries"), cwd=repo).returncode == 0
    site = at(repo, "svc/fw.py", "OpenAI(max_retries")  # after the wrap's import block
    code = ("from svc.fw import build; from svc import emb; "
            "build().chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': 'hi'}])\n"
            "try:\n    emb.c.with_options(max_retries=0).embeddings.create(model='text-embedding-3-small', input='x')\n"
            "except Exception as e:\n    print('provider refused:', type(e).__name__)")
    r = rm("check", site, "--run", "python -c %s" % shlex.quote(code), cwd=repo / "svc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "client=svc/fw.py:build" in r.stdout and "%s: confirmed by PR1" % site in r.stdout, r.stdout
    assert "blocked" in r.stdout and "api.openai.com:443" in r.stdout and "provider refused" in r.stdout, r.stdout
    assert ledger(repo)["probes"]["PR1"]["blocked"] == {"api.openai.com:443": 1}


def test_a_client_lever_rewraps_and_undo_keeps_the_wrap(repo):
    """A client lever (operator key only) re-wraps the client; undo leaves it wrapped without the option."""
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    ctor = at(repo, "svc/direct.py", "OpenAI()")
    r = rm("adapt", ctor, "--operator-key-only", "--loses", "users' own keys stay on the provider", "--why", "per-user keys", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "operator_key_only=True" in (repo / "svc" / "direct.py").read_text()
    r = rm("adapt", "--undo", ledger(repo)["tradeoffs"][0]["id"], cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    src = (repo / "svc" / "direct.py").read_text()
    assert "operator_key_only" not in src and "_router_wrap_openai(OpenAI())" in src, src


def test_snapshot_is_safe_under_concurrent_calls(repo):
    """The live dashboard snapshots (bundle refresh) while an apply session runs the check; the state dir must never leak
    into a tree."""
    from concurrent.futures import ThreadPoolExecutor
    from router_migrate import agent
    for i in range(20):
        (repo / ".router-migrate" / ("noise%d.json" % i)).write_text("{}")
    with ThreadPoolExecutor(8) as ex:
        trees = list(ex.map(lambda _: agent.snapshot_tree(str(repo)), range(24)))
    assert len(set(trees)) == 1
    ls = subprocess.run(["git", "ls-tree", "-r", "--name-only", trees[0]], cwd=repo, capture_output=True, text=True).stdout
    assert ".router-migrate" not in ls and "svc/direct.py" in ls
    assert not [f for f in os.listdir(repo / ".router-migrate") if f.startswith("snap.")]
