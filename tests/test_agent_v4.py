"""v4 agent primitives: run applies nothing and proposes every site; hard limits are the only refusals; claims are recorded,
not rejected, and count once static or dynamic evidence exists; probe produces dynamic evidence (also for a call the scanner
can't see, and for a native client); unwrap inverts wrap exactly; finish needs a decision for every item and evidence for
every migrated claim, enforces the invariants, compiles latency guardrails, and reverts every edit after the attempt cap;
cards reach the human decision path and apply only when approved."""
import json
import os
import shlex
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")
sys.path.insert(0, ROOT)

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


def rm(*args, cwd, stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env.update(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="sk-ant-test")
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
    for i, l in enumerate((repo / rel).read_text().splitlines(), 1):
        if needle in l:
            return "%s:%d" % (rel, i)
    raise AssertionError(needle)


def ledger(repo):
    return json.load(open(repo / ".router-migrate" / "ledger.json"))


def code_diff(repo):
    return subprocess.run(["git", "status", "--porcelain", "--", ".", ":(exclude).router-migrate"], cwd=repo, capture_output=True, text=True).stdout


PROBE = "%s -c \"from svc.crossmod import again; from svc.claude import classify; again('hi'); classify('hi')\"" % sys.executable


def test_run_applies_nothing_and_proposes_every_site(repo):
    assert code_diff(repo) == ""  # nothing applied (the shadow floor for the Router-on baseline is reverted)
    L = ledger(repo)
    assert L["version"] == 4 and L["floor"]["applied"] is False
    props = {"%s" % p["file"]: p for p in L["proposals"]}
    assert props["svc/direct.py"]["proposal"] == "migrated" and props["svc/direct.py"]["edit"]["cmd"].startswith("router-migrate wrap svc/direct.py:")
    assert props["svc/emb.py"]["hard_limit"] == "no_endpoint" and props["svc/emb.py"]["edit"]["do"] == "none"
    assert {x["id"][0] for x in L["worklist"]} >= {"P"} and all(p in L["worklist"] for p in L["proposals"])
    assert os.path.exists(repo / ".router-migrate" / "proposals.diff")
    assert L["baseline"]["router_on_floor"] is not None and L["baseline"]["router_on_source"] == "shadow floor"


def test_hard_limits_are_the_only_refusals(repo):
    r = rm("claim", at(repo, "svc/emb.py", "embeddings.create"), "migrated", "--why", "x", "--evidence", "svc/emb.py:3", cwd=repo)
    assert r.returncode == 1 and "can't be migrated" in r.stdout and "R_NO_ENDPOINT" in r.stdout
    # not a hard limit: a migrated claim the static planner doesn't see routing is recorded (unconfirmed), never rejected
    r = rm("claim", at(repo, "svc/hidden.py", "chat.completions"), "migrated", "--why", "factory client", "--evidence", "svc/factory.py:5", cwd=repo)
    assert r.returncode == 0 and "unconfirmed" in r.stdout
    assert ledger(repo)["claims"]["svc/hidden.py:5"]["confirmed"] is None
    r = rm("claim", "svc/direct.py:7", "kept", "--reason", "R_OTHER_HOST", "--why", "x", "--evidence", "PR9", cwd=repo)
    assert r.returncode == 1 and "probe PR9 does not exist" in r.stdout


def test_check_reports_hard_limit_proposal_evidence_and_native(repo):
    r = rm("check", at(repo, "svc/emb.py", "embeddings.create"), cwd=repo)
    assert "HARD LIMIT" in r.stdout
    r = rm("check", at(repo, "svc/direct.py", "chat.completions"), cwd=repo)
    assert "proposal P" in r.stdout and "suggested edit: router-migrate wrap" in r.stdout and "evidence: static no" in r.stdout
    assert "native (base_url + key, no runtime): no:" in r.stdout and "chat.completions" in r.stdout
    r = rm("check", at(repo, "svc/claude.py", "messages.create"), cwd=repo)
    assert "native (base_url + key, no runtime): eligible" in r.stdout


def test_unwrap_inverts_wrap_exactly(repo):
    before = (repo / "svc/direct.py").read_text()
    r = rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo)
    assert r.returncode == 0 and "_router_wrap_openai(OpenAI())" in (repo / "svc/direct.py").read_text()
    r = rm("unwrap", at(repo, "svc/direct.py", "_router_wrap_openai(OpenAI())"), cwd=repo)
    assert r.returncode == 0, r.stdout
    assert (repo / "svc/direct.py").read_text() == before
    assert [e["by"] for e in ledger(repo)["edits"]][-2:] == ["agent-wrap", "agent-unwrap"]


def test_probe_confirms_what_the_scanner_cannot_see_and_native(repo):
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    r = rm("wrap", at(repo, "svc/claude.py", "Anthropic()"), "--native", cwd=repo)
    assert r.returncode == 0, r.stdout
    cross = at(repo, "svc/crossmod.py", "chat.completions")
    native = at(repo, "svc/claude.py", "messages.create")
    r = rm("check", cross, cwd=repo)
    assert "the scanner sees no LLM call" in r.stdout  # invisible to the static planner
    r = rm("probe", "%s,%s" % (cross, native), "--cmd", PROBE, cwd=repo)
    assert r.returncode == 0, r.stdout
    assert "%s: confirmed by PR1" % cross in r.stdout and "%s: confirmed by PR1 (native)" % native in r.stdout, r.stdout
    r = rm("claim", cross, "migrated", "--why", "client from svc/direct.py", "--evidence", "PR1", cwd=repo)
    assert r.returncode == 0 and "unconfirmed" not in r.stdout
    c = ledger(repo)["claims"][cross]
    assert c["confirmed"] == "dynamic" and c["origin"] == "agent"


def _decide_everything(repo):
    assert rm("wrap", at(repo, "svc/direct.py", "OpenAI()"), cwd=repo).returncode == 0
    assert rm("probe", "--cmd", PROBE, cwd=repo).returncode == 0
    rm("claim", at(repo, "svc/direct.py", "chat.completions"), "migrated", "--why", "default endpoint, operator key", "--evidence",
       "svc/direct.py:3", "--latency", "user_waiting", cwd=repo)
    rm("claim", at(repo, "svc/crossmod.py", "chat.completions"), "migrated", "--why", "same client", "--evidence", "PR1", cwd=repo)
    rm("claim", at(repo, "svc/emb.py", "embeddings.create"), "kept", "--reason", "R_NO_ENDPOINT", "--why", "embeddings", "--evidence", "svc/emb.py:7", cwd=repo)
    rm("claim", at(repo, "svc/hidden.py", "chat.completions"), "flagged", "--reason", "R_PER_TENANT", "--why", "key is a parameter",
       "--evidence", "svc/hidden.py:4", cwd=repo)
    rm("claim", at(repo, "svc/claude.py", "messages.create"), "kept", "--reason", "R_OTHER_HOST", "--why", "decided to leave it for now",
       "--evidence", "svc/claude.py:3", cwd=repo)
    left = [it for it in ledger(repo)["worklist"] if not __import__("router_migrate.agent", fromlist=["x"]).resolved(ledger(repo), it)]
    for it in left:
        rm("claim", "%s:%s" % (it["file"], it["line"]), "not_a_site", "--reason", "R_NOT_PRODUCTION", "--why", "x", "--evidence",
           "%s:%s" % (it["file"], it["line"]), cwd=repo)


def test_finish_needs_decisions_and_evidence_then_passes_with_guardrails(repo):
    r = rm("finish", str(repo), cwd=repo)
    assert r.returncode == 1 and "[decisions]" in r.stdout
    rm("claim", at(repo, "svc/hidden.py", "chat.completions"), "migrated", "--why", "x", "--evidence", "svc/factory.py:5", cwd=repo)
    r = rm("finish", str(repo), cwd=repo)
    assert "[evidence] svc/hidden.py" in r.stdout
    _decide_everything(repo)
    r = rm("finish", str(repo), cwd=repo)
    assert r.returncode == 0, r.stdout
    res = ledger(repo)["result"]
    assert res["migrated_confirmed_dynamic"] >= 1 and res["migrated_unconfirmed"] == 0
    pol = json.load(open(repo / "router.policy.json"))
    assert pol["globs"]["**svc/direct.py:ask"] == {"provider_timeout": 30}  # user_waiting + buffered; no forced tier anywhere
    assert os.path.exists(repo / "svc" / "router_policy.py")


def test_invariants_and_revert_after_cap(repo):
    (repo / "svc/config.py").write_text("import os\n\nKEY = None\n")
    r = rm("finish", str(repo), cwd=repo)
    assert "[invariant] provider key reference(s) removed: OPENAI_API_KEY" in r.stdout
    (repo / "tests/test_basic.py").write_text("def test_imports():\n    pass\n")
    for _ in range(2):  # attempts 2 and 3 (the invariant failure above was attempt 1)
        r = rm("finish", str(repo), cwd=repo)
    assert r.returncode == 0 and "reverted every edit" in r.stdout
    assert code_diff(repo).strip() == "" and ledger(repo)["finish"]["reverted"]


def test_hook_protocol(repo):
    r = rm("finish", str(repo), "--hook", cwd=repo, stdin="{}")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["decision"] == "block" and "decisions" in out["reason"]


def test_cards_reach_the_human_and_apply_only_when_approved(repo):
    from router_migrate import agent as A, improve
    from router_migrate.catalog import Catalog
    call = at(repo, "svc/direct.py", "chat.completions")
    r = rm("card", call, "--kind", "per_user_key", "--why", "some requests carry a user's key", "--evidence", "svc/direct.py:3", cwd=repo)
    assert r.returncode == 0, r.stdout
    r = rm("card", call, "--kind", "model_swap", "--to", "gpt-4.1-mini", "--why", "cheaper, same family", "--evidence", call, cwd=repo)
    assert r.returncode == 0, r.stdout
    assert rm("card", call, "--kind", "model_swap", "--to", "not-a-model", "--why", "x", "--evidence", call, cwd=repo).returncode == 1
    root = str(repo)
    rec = A.do_improve(root)["recommendations"]
    agent_cards = [c for c in rec["cards"] if c["rule"].startswith("agent.")]
    assert {c["rule"] for c in agent_cards} == {"agent.per_user_key", "agent.model_swap"}
    assert "operator_key_only" not in (repo / "svc/direct.py").read_text()  # nothing applies before approval
    for c in agent_cards:
        improve.decide(root, c["id"], "approve")
    log = improve.apply_decisions(root, Catalog.load())
    assert len(log["applied"]) == 2, log
    assert "operator_key_only=True" in (repo / "svc/direct.py").read_text()
    pol = json.load(open(repo / "router.policy.json"))
    assert pol["globs"]["**svc/direct.py:ask"]["model_map"] == {"gpt-4o-mini": "gpt-4.1-mini"}


def test_probe_attributes_framework_clients_stays_offline_and_runs_from_a_subdirectory(repo):
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
    r = rm("probe", site, "--cmd", "python -c %s" % shlex.quote(code), cwd=repo / "svc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "client=svc/fw.py:build" in r.stdout and "%s: confirmed by PR1" % site in r.stdout, r.stdout
    assert "blocked" in r.stdout and "api.openai.com:443" in r.stdout and "provider refused" in r.stdout, r.stdout
    assert ledger(repo)["probes"]["PR1"]["blocked"] == {"api.openai.com:443": 1}


def test_adapt_moves_a_seed_call_records_the_tradeoff_and_undo_reverses_it(repo):
    """Migrate by default: a call Router can't take as written (seed=0) moves once the agent drops the parameter. The
    trade-off is recorded with what is lost, compiled into policy (code unchanged), and undo takes it back out. A client
    lever (operator key only) re-wraps the client and undo leaves it wrapped without the option."""
    from router_migrate import agent as AG
    (repo / "svc" / "seeded.py").write_text("from openai import OpenAI\n\nclient = OpenAI(max_retries=0)\n\n\ndef ask(q):\n"
                                            "    return client.chat.completions.create(model='gpt-4o-mini', seed=0, messages=[{'role': 'user', 'content': q}])\n")
    assert rm("wrap", at(repo, "svc/seeded.py", "OpenAI("), cwd=repo).returncode == 0
    call = at(repo, "svc/seeded.py", "chat.completions")
    probe = "python -c %s" % shlex.quote("from svc.seeded import ask\ntry:\n    ask('hi')\nexcept Exception as e:\n    print('refused', type(e).__name__)")
    r = rm("probe", call, "--cmd", probe, cwd=repo)
    assert "NOT confirmed" in r.stdout and "untranslatable: seed" in r.stdout, r.stdout
    r = rm("adapt", call, "--drop", "seed", "--loses", "best-effort reproducibility of sampling", "--why", "seed=0 is Router-rejected", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    pol = json.load(open(repo / "router.policy.json"))
    assert any(e.get("drop_params") == ["seed"] for e in pol["globs"].values()), pol
    r = rm("probe", call, "--cmd", probe, cwd=repo)
    assert "%s: confirmed by PR2" % call in r.stdout, r.stdout
    t = ledger(repo)["tradeoffs"][0]
    assert t["loses"] and t["levers"] == {"drop_params": ["seed"]}
    assert AG.undo_tradeoff(str(repo), t["id"])
    pol = json.load(open(repo / "router.policy.json"))
    assert not any("drop_params" in e for e in (pol.get("globs") or {}).values()), pol
    assert ledger(repo)["tradeoffs"][0]["undone"]
    # a client lever
    ctor = at(repo, "svc/direct.py", "OpenAI()")
    r = rm("adapt", ctor, "--operator-key-only", "--loses", "users' own keys stay on the provider", "--why", "per-user keys", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "operator_key_only=True" in (repo / "svc" / "direct.py").read_text()
    AG.undo_tradeoff(str(repo), ledger(repo)["tradeoffs"][1]["id"])
    src = (repo / "svc" / "direct.py").read_text()
    assert "operator_key_only" not in src and "_router_wrap_openai(OpenAI())" in src, src
