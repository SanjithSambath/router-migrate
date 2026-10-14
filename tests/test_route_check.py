"""route_check: the product's truth tool. Each production site's representative request goes through the real vendored
runtime under the repo's compiled policy to router-sim: a wrapped OpenAI call routes; a seed=0 call doesn't (and the reason
names the `adapt --drop seed` that would move it) until the policy drops seed; an unwrapped client doesn't (reason: `wrap`);
embeddings is a hard limit; a model Router doesn't serve gets a model-map suggestion."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RM = os.path.join(ROOT, "bin", "router-migrate")
sys.path.insert(0, ROOT)

from router_migrate import route_check as RC  # noqa: E402

CALL = "    return client.chat.completions.create(model=%r, %smessages=[{'role': 'user', 'content': q}])\n"
FILES = {
    "pyproject.toml": '[project]\nname = "rc"\ndependencies = ["openai"]\n',
    "svc/__init__.py": "",
    "svc/direct.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n" + CALL % ("gpt-4o-mini", ""),
    "svc/seeded.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n" + CALL % ("gpt-4o-mini", "seed=0, "),
    "svc/plain.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n" + CALL % ("gpt-4o-mini", ""),
    "svc/old.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n" + CALL % ("gpt-4-0314", ""),
    "svc/emb.py": "from openai import OpenAI\n\nc = OpenAI()\n\n\ndef e(t):\n    return c.embeddings.create(model='text-embedding-3-small', input=t)\n",
    "tests/test_basic.py": "def test_imports():\n    import svc.direct, svc.seeded, svc.plain, svc.old, svc.emb  # noqa\n",
}


def rm(*args, cwd):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env.update(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="sk-ant-test")
    return subprocess.run([RM] + list(args), cwd=cwd, capture_output=True, text=True, env=env, timeout=600)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("rc")
    for rel, src in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=root, capture_output=True, check=True)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    r = rm("run", str(root), "--test-cmd", "%s -m pytest -q -p no:cacheprovider tests" % sys.executable, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for f in ("svc/direct.py", "svc/seeded.py", "svc/old.py"):
        r = rm("wrap", "%s:3" % f, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    return root


def at(root, rel, needle):
    for i, l in enumerate((root / rel).read_text().splitlines(), 1):
        if needle in l:
            return "%s:%d" % (rel, i)
    raise AssertionError(needle)


def by_file(root):
    rows = RC.check_sites(str(root))
    out = {}
    for r in rows:
        out.setdefault(r["file"], r)
    return out


def test_wrapped_call_routes_and_unwrapped_doesnt(repo):
    rows = by_file(repo)
    d = rows["svc/direct.py"]
    assert d["routes"] is True and d["valid"] is True and d["reason"] == "valid Router request", d
    assert d["feature"] == "svc/direct.py:ask" and d["model"] == "gpt-4o-mini" and d["host"] is None and d["levers"] == {}
    p = rows["svc/plain.py"]
    assert p["routes"] is False and p["valid"] is None, p
    assert "client not wrapped" in p["reason"] and "`router-migrate wrap svc/plain.py:3`" in p["reason"], p
    assert p["levers"] == {"wrap": "svc/plain.py:3"}


def test_hard_limit_and_model_not_in_catalog(repo):
    rows = by_file(repo)
    e = rows["svc/emb.py"]
    assert e["routes"] is False and e["reason"].startswith("hard limit: embeddings") and e["levers"] == {}, e
    o = rows["svc/old.py"]
    assert o["routes"] is False and "model 'gpt-4-0314' not in catalog" in o["reason"] and "--model-map gpt-4-0314=" in o["reason"], o
    near = o["levers"]["model_map"]["gpt-4-0314"]
    from router_migrate.catalog import Catalog
    assert Catalog.load().resolve(near)[0] == near


def test_seed_call_stays_until_the_policy_drops_seed(repo):
    call = at(repo, "svc/seeded.py", "chat.completions")
    s = by_file(repo)["svc/seeded.py"]
    assert s["routes"] is False and "seed" in s["reason"] and "adapt %s --drop seed" % call in s["reason"], s
    assert s["levers"] == {"drop_params": ["seed"]}
    r = rm("adapt", call, "--drop", "seed", "--loses", "reproducible sampling", "--why", "seed is untranslatable", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    s = by_file(repo)["svc/seeded.py"]
    assert s["routes"] is True and s["valid"] is True, s
    # the eval form agrees, and an explicit policy=None ignores the repo's compiled policy
    site = next(x for x in RC.check_sites(str(repo)) if x["file"] == "svc/seeded.py")
    from router_migrate import agent as AG
    planned = [x for x in AG.current_plan(str(repo))["sites"] if x["id"] == site["id"]]
    assert RC.route_check(str(repo), planned, RC.load_policy(str(repo))[0])[site["id"]]["valid"] is True
    assert RC.route_check(str(repo), planned, None)[site["id"]]["routed"] is False


def test_unwrapped_reason_says_what_blocks_once_wrapped(repo, tmp_path):
    (repo / "svc" / "plain2.py").write_text("from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef ask(q):\n" + CALL % ("gpt-4o-mini", "seed=1, "))
    try:
        p = by_file(repo)["svc/plain2.py"]
        assert "client not wrapped" in p["reason"] and "once wrapped: untranslatable: seed" in p["reason"], p
        assert p["levers"] == {"wrap": "svc/plain2.py:3", "drop_params": ["seed"]}
    finally:
        (repo / "svc" / "plain2.py").unlink()
