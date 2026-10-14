"""v3 agent primitives: run writes a ledger and worklist, check reports tool facts, wrap is the deterministic codemod at any
constructor, claim enforces evidence and "facts beat claims", finish gates scope/tests and reverts to the floor after the
attempt cap, and --hook speaks the Claude Code Stop-hook protocol."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")

FILES = {
    "pyproject.toml": '[project]\nname = "zoo"\ndependencies = ["openai"]\n',
    "svc/__init__.py": "",
    "svc/direct.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n"
                     "    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n",
    "svc/factory.py": "from openai import OpenAI\n\n\ndef make(key):\n    return OpenAI(api_key=key)\n",
    "svc/hidden.py": "from .factory import make\n\n\ndef go(q, key):\n"
                     "    return make(key).chat.completions.create(model='gpt-4o-mini', messages=[{'role': 'user', 'content': q}])\n",
    "svc/emb.py": "from openai import OpenAI\n\nc = OpenAI()\n\n\ndef e(t):\n    return c.embeddings.create(model='text-embedding-3-small', input=t)\n",
    "tests/test_basic.py": "def test_imports():\n    import svc.direct, svc.hidden, svc.emb  # noqa\n",
}


def rm(*args, cwd, stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env["OPENAI_API_KEY"] = "sk-test"
    return subprocess.run([RM] + list(args), cwd=cwd, capture_output=True, text=True, env=env, input=stdin, timeout=600)


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
    r = rm("run", str(tmp_path), "--test-cmd", "%s -m pytest -q -p no:cacheprovider tests" % sys.executable, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path


def at(repo, rel, needle):
    """FILE:LINE of the first line containing needle, in the file as it is now (the floor moves lines)."""
    for i, l in enumerate((repo / rel).read_text().splitlines(), 1):
        if needle in l:
            return "%s:%d" % (rel, i)
    raise AssertionError(needle)


def ledger(repo):
    return json.load(open(repo / ".router-migrate" / "ledger.json"))


def test_run_writes_ledger_floor_and_worklist(repo):
    led = ledger(repo)
    assert led["version"] == 3 and led["floor"]["tree"] and led["floor"]["migrated"] >= 1
    assert led["baseline"]["before"]["passed"] == 1
    assert "_router_wrap_openai(OpenAI())" in (repo / "svc/direct.py").read_text()
    # the per-call key in the factory is a judgment the rules can't settle: it is on the worklist, not migrated
    assert any(it["file"] == "svc/hidden.py" for it in led["worklist"]), led["worklist"]


def test_check_reports_facts_and_next(repo):
    r = rm("check", at(repo, "svc/direct.py", "completions.create"), "--json", cwd=repo)
    f = json.loads(r.stdout)
    assert f["routes"] is True and f["outcome"] == "migrated"
    r = rm("check", at(repo, "svc/emb.py", "embeddings.create"), "--json", cwd=repo)
    f = json.loads(r.stdout)
    assert f["routes"] is False and "embeddings" in f["reason"]
    r = rm("check", "nope.py:3", "--json", cwd=repo)
    assert r.returncode == 1 and json.loads(r.stdout)["next"]


def test_claim_requires_resolvable_evidence(repo):
    loc = at(repo, "svc/hidden.py", "completions.create")
    r = rm("claim", loc, "flagged", "--reason", "R_PER_TENANT", "--why", "per-call key", cwd=repo)
    assert r.returncode == 1 and "evidence" in r.stdout
    r = rm("claim", loc, "flagged", "--reason", "R_PER_TENANT", "--why", "per-call key", "--evidence", "svc/factory.py:999", cwd=repo)
    assert r.returncode == 1 and "outside the file" in r.stdout
    r = rm("claim", loc, "--outcome", "flagged", "--reason", "R_PER_TENANT", "--why", "key is a per-call parameter", "--evidence", at(repo, "svc/factory.py", "OpenAI("), cwd=repo)
    assert r.returncode == 0, r.stdout


def test_reason_code_required_and_trace(repo):
    loc = at(repo, "svc/hidden.py", "completions.create")
    r = rm("claim", loc, "--outcome", "kept", "--why", "x", "--evidence", "svc/factory.py:1", cwd=repo)
    assert r.returncode == 1 and "R_OTHER_HOST" in r.stdout  # the error lists the codes
    f = json.loads(rm("check", at(repo, "svc/direct.py", "completions.create"), "--json", cwd=repo).stdout)
    assert f["verdict"] == "routes" and f["condition"] is None


def test_facts_beat_claims(repo):
    """The agent cannot claim migrated for a site the runtime won't reroute (embeddings)."""
    r = rm("claim", at(repo, "svc/emb.py", "embeddings.create"), "migrated", "--why", "x", "--evidence", at(repo, "svc/emb.py", "OpenAI()"), cwd=repo)
    assert r.returncode == 1 and "does not route" in r.stdout and "next:" in r.stdout


def test_wrap_is_idempotent_and_returns_diff(repo):
    (repo / "svc/extra.py").write_text("from openai import OpenAI\n\n\ndef build():\n    return OpenAI()\n")
    r = rm("wrap", "svc/extra.py:5", "--json", cwd=repo)
    out = json.loads(r.stdout)
    assert out.get("ok") is not False, r.stdout
    assert out["changed"] and "_router_wrap_openai(OpenAI())" in out["diff"]
    r = rm("wrap", at(repo, "svc/extra.py", "OpenAI())"), "--json", cwd=repo)
    assert json.loads(r.stdout)["changed"] is False, r.stdout


def test_finish_blocks_on_unresolved_worklist_and_hook_protocol(repo):
    r = rm("finish", ".", "--hook", cwd=repo, stdin="{}")
    d = json.loads(r.stdout)
    assert d["decision"] == "block" and "worklist" in d["reason"]


def test_finish_scope_gate_and_revert_to_floor(repo):
    led = ledger(repo)
    for it in led["worklist"]:
        rm("claim", "%s:%s" % (it["file"], it["line"]), "needs_human", "--reason", "R_UNDECIDED", "--why", "test", "--evidence", "%s:1" % it["file"], cwd=repo)
    floor_direct = (repo / "svc/direct.py").read_text()
    (repo / "tests/test_basic.py").write_text("def test_imports():\n    pass\n")  # editing tests is out of scope
    for i in range(2):
        r = rm("finish", ".", cwd=repo)
        assert r.returncode == 1 and "[scope]" in r.stdout and "test file" in r.stdout
    r = rm("finish", ".", cwd=repo)  # third failure: revert to the floor and stop
    assert r.returncode == 0 and "reverted" in r.stdout
    assert "import svc.direct" in (repo / "tests/test_basic.py").read_text()
    assert (repo / "svc/direct.py").read_text() == floor_direct
    assert ledger(repo)["finish"]["reverted"]


def test_finish_passes_and_confirms_static_claims(repo):
    led = ledger(repo)
    for it in led["worklist"]:
        rm("claim", "%s:%s" % (it["file"], it["line"]), "flagged", "--reason", "R_PER_TENANT", "--why", "per-call key", "--evidence", at(repo, "svc/factory.py", "OpenAI("), cwd=repo)
    loc = at(repo, "svc/direct.py", "completions.create")
    r = rm("claim", loc, "migrated", "--why", "wrapped client, default endpoint", "--evidence", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo)
    assert r.returncode == 0
    r = rm("finish", ".", cwd=repo)
    assert r.returncode == 0, r.stdout
    led = ledger(repo)
    assert led["claims"][loc]["confirmed"] == "static"
    assert led["result"]["migrated_confirmed_claims"] == 1


def test_measure_sees_through_wrap_and_tenancy_is_a_judgment(tmp_path):
    """After the floor, `self.client = _router_wrap_openai(AsyncOpenAI(...))` must still link to its calls (it measured as
    Azure/kept before), and a client someone deliberately wrapped routes even if the per-tenant heuristic fires."""
    from router_migrate import plan, scan_py
    from router_migrate.catalog import Catalog
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/llm.py").write_text(
        "from openai import AsyncOpenAI, AsyncAzureOpenAI\nfrom .router_runtime import wrap_openai as _router_wrap_openai\n\n\n"
        "class LLM:\n    def __init__(self, cfg):\n        if cfg.api_type == 'azure':\n            self.client = AsyncAzureOpenAI(api_key=cfg.key)\n"
        "        else:\n            self.client = _router_wrap_openai(AsyncOpenAI(api_key=cfg.api_key))\n\n"
        "    async def ask(self, m):\n        return await self.client.chat.completions.create(model='gpt-4o-mini', messages=m)\n")
    p = plan.build(scan_py.scan(str(tmp_path)), Catalog.load(), measure_only=True)
    site = [s for s in p["sites"] if s["kind"] == "call"][0]
    assert site["outcome"] == "migrated", site["reason"]


def test_wrap_keeps_worklist_lines_on_the_same_code(repo):
    """`wrap` inserts an import block; worklist lines in that file must move with the code (else agents claim stale lines)."""
    led = ledger(repo)
    item = next(it for it in led["worklist"] if it["file"] == "svc/hidden.py")
    text_before = (repo / "svc/hidden.py").read_text().splitlines()[int(item["line"]) - 1]
    (repo / "svc/hidden.py").write_text((repo / "svc/hidden.py").read_text() + "\n\nfrom openai import OpenAI\n\n\ndef extra():\n    return OpenAI()\n")
    r = rm("wrap", at(repo, "svc/hidden.py", "return OpenAI()"), "--json", cwd=repo)
    assert json.loads(r.stdout)["changed"], r.stdout
    item2 = next(it for it in ledger(repo)["worklist"] if it["id"] == item["id"])
    assert (repo / "svc/hidden.py").read_text().splitlines()[int(item2["line"]) - 1] == text_before
