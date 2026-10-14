"""The dashboard's plain-language view (router_migrate/dashboard/view.py): every call's status and the exact line that changed,
every improvement's code change and its effect on a request, every agent assumption."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from router_migrate.dashboard import view  # noqa: E402

PY_DIFF = """--- a/svc/api/chat.py
+++ b/svc/api/chat.py
@@ -1,6 +1,13 @@
+try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
+    from .router_runtime import wrap_openai as _router_wrap_openai
+except ImportError:  # router-migrate: module executed as a script
+    _router_wrap_openai = lambda c, *a, **k: c  # noqa: E731
 from openai import OpenAI
-client = OpenAI()
+client = _router_wrap_openai(OpenAI())
--- a/backend/settings.py
+++ b/backend/settings.py
@@ -1,4 +1,9 @@
+from .router_runtime import (
+        async_http_client as _router_async_http_client,
+        http_client as _router_http_client,
+    )
+    _router_http_client = lambda **k: k.get('_default')  # noqa: E731
-    api_key=KEY
-    )
+    api_key=KEY,
+    http_client=_router_http_client(), async_http_client=_router_async_http_client())
--- a/src/llm.ts
+++ b/src/llm.ts
@@ -3,3 +3,4 @@
+import { routerOptions } from "./routerRuntime"
-    dangerouslyAllowBrowser: true
+    dangerouslyAllowBrowser: true,
+    ...routerOptions()
--- /dev/null
+++ b/svc/router_runtime.py
@@ -0,0 +1,2 @@
+import json
+X = 1
"""


def test_parse_diff_keeps_the_real_change_and_drops_the_shims():
    blocks = view.parse_diff(PY_DIFF)
    assert set(blocks) == {"svc/api/chat.py", "backend/settings.py", "src/llm.ts"}  # vendored runtime files are not changes to show
    assert blocks["svc/api/chat.py"] == [{"line": 6, "before": ["client = OpenAI()"], "after": ["client = _router_wrap_openai(OpenAI())"]}]
    (b,) = blocks["backend/settings.py"]
    assert b["before"] == ["    api_key=KEY", "    )"] and "http_client=_router_http_client()" in b["after"][1]
    assert blocks["src/llm.ts"][0]["after"] == ["    dangerouslyAllowBrowser: true,", "    ...routerOptions()"]


def test_reason_codes_become_plain_sentences():
    assert view._plain("agent: R_PER_TENANT apiKey comes from each user's settings", None).startswith("Each user brings their own provider key")
    assert "calls api.x.ai directly" in view._plain("client targets api.x.ai (another vendor's endpoint)", "api.x.ai").lower()
    assert view._plain("something new", None) == "Something new"


def rm(*args, cwd):
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env["OPENAI_API_KEY"] = "sk-test"
    return subprocess.run([os.path.join(ROOT, "bin", "router-migrate")] + list(args), cwd=cwd, capture_output=True, text=True, env=env, timeout=600)


def test_view_of_a_run_after_improvements_are_applied(tmp_path):
    from test_v2 import FILES
    for rel, src in list(FILES.items()) + [("tests/test_smoke.py", "def test_ok():\n    assert True\n")]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=tmp_path, capture_output=True, check=True)  # noqa: E731
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    assert rm("run", str(tmp_path), "--skip-verify", cwd=tmp_path).returncode == 0
    from conftest import accept_proposals
    accept_proposals(tmp_path, rm)  # v4: the agent accepts the rules' proposals (v3 applied them before the agent)
    line = next(i for i, l in enumerate((tmp_path / "svc/workers/nightly_summaries.py").read_text().splitlines(), 1) if "completions.create" in l)
    loc = "svc/workers/nightly_summaries.py:%d" % line
    for key, val, ev in (("latency", "background", loc), ("task", "summarization", loc), ("cacheable", "1500", loc)):
        assert rm("judge", loc, key, val, "--why", "read it", "--evidence", ev, cwd=tmp_path).returncode == 0
    rm("improve", ".", cwd=tmp_path)
    from router_migrate import improve
    from router_migrate.catalog import Catalog
    r = json.load(open(tmp_path / ".router-migrate" / "recommendations.json"))
    by = {c["rule"]: c for c in r["cards"]}
    for rule in ("attr.metadata", "tier.flex_background", "cache.key", "unlock.vendor_endpoint"):
        improve.decide(str(tmp_path), by[rule]["id"], "approve", by="ui")
    improve.apply_decisions(str(tmp_path), Catalog.load())
    rm("improve", ".", cwd=tmp_path)
    b = improve.bundle(str(tmp_path))
    assert b.get("view_error") is None
    v = b["view"]
    calls = {c["fn"]: c for c in v["calls"]}
    # how each call reached Router, with the line that changed
    assert calls["answer"]["status"] == "lossless" and calls["answer"]["model"]["match"] == "1:1"
    assert calls["answer"]["code"][0]["after"] == ["client = _router_wrap_openai(OpenAI())"]
    assert calls["generate"]["status"] == "unlocked" and calls["generate"]["routes"]
    assert any("unlock_hosts=['api.x.ai']" in "".join(cd["after"]) for cd in calls["generate"]["code"])
    assert calls["run"]["status"] == "decide" and not calls["run"]["code"]  # beta call stays: no change to show for it
    assert v["counts"]["routed"] == 3 and v["counts"]["total"] == 4
    # each improvement: its change and its effect on a request, in plain words
    imps = {i["rule"]: i for i in v["improvements"]}
    flex = imps["tier.flex_background"]
    assert flex["title"] == "Run summarize_all on Flex" and flex["impact"]["label"] == "−50% per call"
    assert {"op": "add", "key": "allow_flex_tier", "v": "true"} in flex["request"]
    assert flex["code"][0]["file"] == "router.policy.json" and flex["code"][0]["after"] == ['"allow_flex_tier": true']
    assert flex["assumptions"][0]["text"].startswith("Nobody waits on it") and flex["assumptions"][0]["evidence"] == [loc]
    nightly_fb = [i for i in v["improvements"] if i["rule"] == "fallback.candidates" and i["fn"] == "summarize_all"][0]
    assert flex["suggested"] and flex["conflicts_with"] == [nightly_fb["id"]]  # either/or, Flex suggested
    assert {"op": "add", "key": "prompt_cache_key", "v": '"svc/workers/nightly_summaries.py:summarize_all"'} in imps["cache.key"]["request"]
    assert imps["attr.metadata"]["request"][-1]["key"] == "metadata.product"
    fb = [i for i in v["improvements"] if i["rule"] == "fallback.candidates"][0]
    assert fb["request"][0]["op"] == "del" and fb["request"][1]["key"] == "models"
    stated = imps["unlock.stated_loss"]  # not applied: a preview of the code it would write
    assert stated["code"][0]["by"] == "preview" and "drop_betas=['prompt-caching-2024-07-31']" in stated["code"][0]["after"][0]
    model = [i for i in v["improvements"] if i["kind"] == "model"][0]
    assert model["code"][0]["by"] == "you" and model["code"][0]["before"] != model["code"][0]["after"]
    # every agent judgment, in plain words, with what it drives
    a = {(x["fn"], x["key"]): x for x in v["assumptions"]}
    assert a[("summarize_all", "latency")]["drives"] == [flex["id"]]


def test_a_call_the_floor_did_not_move_is_credited_to_the_agent():
    """Lossless means the rules moved it (the floor plan, made before any agent ran). A call the rules flagged and the agent
    moved is the agent's, even though the tool's re-check of the edited code now says it routes."""
    site = {"id": "py:bot/h.py:268:25:call", "file": "bot/h.py", "line": 268, "function": "reply", "outcome": "migrated", "sdk": "openai",
            "reason": "client patched; model chosen at runtime", "model": {"value": "self.model", "static": False}}
    b = {"recommendations": {"signals": {site["id"]: {"feature": "bot/h.py:reply"}}, "cards": []},
         "ledger": {"claims": [
             {"file": "bot/h.py", "line": 261, "feature": "bot/h.py:reply", "outcome": "migrated", "origin": "agent", "tool_outcome": None,
              "why": "wrapped the client", "evidence": ["bot/h.py:113"]},
             {"file": "bot/h.py", "line": 268, "feature": "bot/h.py:reply", "outcome": "migrated", "origin": "scanner", "tool_outcome": "migrated"}],
             "worklist": [{"type": "site", "file": "bot/h.py", "line": 261, "tool_outcome": "flagged",
                           "why": "flagged: per-tenant provider credentials: all tenants would share one Router key"}]}}
    (c,) = view.calls_view(b, [site], {}, {}, {}, floor_sites=[dict(site, id="py:bot/h.py:261:25:call", line=261, outcome="flagged")])
    assert c["status"] == "agent" and c["agent"]["why"] == "wrapped the client"
    assert c["why"].startswith("The rules flagged it: each user brings their own provider key")
    (c,) = view.calls_view(b, [site], {}, {}, {}, floor_sites=[dict(site, line=261)])  # the floor moved it: lossless
    assert c["status"] == "lossless"
