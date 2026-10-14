"""Regression tests for the eval scorer (eval/v3/score_moved.py, scorer v5): line mapping between the pristine and the trial
tree, calls credited through a proven client, one bucket per ground-truth site, destructive edits, minutes."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval", "v3"))
os.environ.setdefault("RM_EVAL_SET", "v3")
import run_v3 as R  # noqa: E402
import score_moved as SM  # noqa: E402

# a 7-line router-migrate import block inserted after line 3, as the wrap codemod writes it
WRAP_DIFF = """diff --git a/app/main.py b/app/main.py
index 1111111..2222222 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,6 +1,13 @@
 import os
 import anthropic
 from x import y
+try:  # router-migrate
+    from .router_runtime import wrap_anthropic as _w
+except ImportError:
+    try:
+        from router_runtime import wrap_anthropic as _w
+    except ImportError:
+        _w = lambda c, *a, **k: c  # noqa: E731


 def setup():
@@ -20,7 +27,7 @@ def setup():


 class Bot:
-    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
+    client = _w(anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]))


 def reply(m):
"""


def inv_site(f, line, surface="messages", prod=True, endpoint="default", sdk="anthropic", why=""):
    return {"file": f, "line": line, "surface": surface, "prod": prod, "endpoint": endpoint, "sdk": sdk, "key_source": "env",
            "why": why, "expected": "migrated"}


def call(f, line, sid, client=None, outcome="flagged", surface="messages", line_now=None, wrapped=True):
    return {"id": sid, "file": f, "line": line, "line_now": line_now or line, "kind": "call", "surface": surface, "outcome": outcome,
            "via_client": None, "client_id": client, "_wrapped": wrapped and bool(client), "_native": False}


def dec(f, line, outcome, why="because", line_end=None):
    return {"file": f, "line": line, "line_end": line_end or line, "outcome": outcome, "bucket": SM.BUCKET_OF[outcome], "why": why,
            "pristine": True, "source": "test"}


# ------------------------------------------------------------------------------------------------ defect 1: line mapping
def test_diff_hunks_map_shifted_call_sites_back_to_pristine():
    pm = R.pristine_maps(WRAP_DIFF)["app/main.py"]
    assert pm(3) == 3  # before the inserted block
    assert pm(13) == 6  # context line after the block
    assert pm(30) == 23  # the wrapped client line (replaced in place)
    assert pm(671) == 664  # far below: shifted by the block, exactly


def test_claims_resolve_whichever_convention_the_agent_used():
    """D3 on claude-engineer named main.py:671 (after its import block) and main.py:1227 (before it) in the same report:
    both must land on the ground-truth sites (664 and 1227), with no widened window."""
    maps = R.pristine_maps(WRAP_DIFF)
    pm = lambda f, n: maps[f](n) if f in maps else n
    anchors = {"app/main.py": [664, 1227, 23]}
    assert SM.resolve_line(pm, "app/main.py", 671, anchors["app/main.py"]) == (664, "current")
    assert SM.resolve_line(pm, "app/main.py", 1227, anchors["app/main.py"]) == (1227, "pristine")
    decs = SM.resolve_decisions([dict(dec("app/main.py", 671, "migrated"), pristine=None),
                                 dict(dec("app/main.py", 1227, "migrated"), pristine=None)], pm, anchors)
    assert [d["line"] for d in decs] == [664, 1227]


def test_shifted_sites_are_credited_end_to_end():
    maps = R.pristine_maps(WRAP_DIFF)
    pm = lambda f, n: maps[f](n) if f in maps else n
    inv = [inv_site("app/main.py", 664), inv_site("app/main.py", 1227)]
    static = [call("app/main.py", pm("app/main.py", 671), "c1", "cl", line_now=671),
              call("app/main.py", pm("app/main.py", 1234), "c2", "cl", line_now=1234)]
    anchors = {"app/main.py": [664, 1227]}
    decs = SM.resolve_decisions([dict(dec("app/main.py", 671, "migrated"), pristine=None),
                                 dict(dec("app/main.py", 1227, "migrated"), pristine=None)], pm, anchors)
    out = SM.classify(inv, static, [], decs, {"c1": {"routes": True, "reason": "valid Router request"},
                                              "c2": {"routes": True, "reason": "valid Router request"}}, [])
    assert out["counts"]["moved_verified"] == 2


def test_pristine_map_reads_the_trial_trees_own_diff(tmp_path):
    d = tmp_path / "trial"
    (d / "app").mkdir(parents=True)
    src = ["import os", "import anthropic", "from x import y", "", "", "def setup():"] + ["    pass"] * 20 + ["def reply(m):", "    return client.messages.create(model='m')"]
    (d / "app" / "main.py").write_text("\n".join(src) + "\n")
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(a), cwd=d, check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    new = src[:3] + ["try:  # router-migrate", "    pass", "except ImportError:", "    pass"] + src[3:]
    (d / "app" / "main.py").write_text("\n".join(new) + "\n")
    pm = R.PristineMap(str(d), "nonexistent-repo")
    assert pm("app/main.py", 32) == 28  # the call moved down by the 4 inserted lines
    assert pm("other.py", 9) == 9  # a file the diff doesn't touch is unchanged


# ------------------------------------------------------------------------------------------------ defect 2: proven clients
def test_claim_on_a_client_construction_credits_the_calls_that_use_it():
    """gpt-cli: the report names only the client (anthropic.py:24); the calls at :86 and :109 use it."""
    f = "gptcli/providers/anthropic.py"
    inv = [inv_site(f, 86), inv_site(f, 109)]
    clients = [{"id": "cl", "file": f, "line": 24, "wrapped": True}]
    static = [call(f, 86, "c86", "cl"), call(f, 109, "c109", "cl")]
    ok = {"routes": True, "reason": "valid Router request"}
    out = SM.classify(inv, static, clients, [dec(f, 24, "migrated")], {"c86": ok, "c109": ok}, [])
    assert out["counts"]["moved_verified"] == 2
    assert {r["decision"]["how"] for r in out["sites"]} == {"client"}


def test_probe_proven_at_the_client_verifies_its_calls():
    f = "gptcli/providers/anthropic.py"
    inv = [inv_site(f, 86), inv_site(f, 109)]
    clients = [{"id": "cl", "file": f, "line": 24, "wrapped": True}]
    static = [call(f, 86, "c86", "cl"), call(f, 109, "c109", "cl")]
    no = {"routes": False, "reason": "model chosen at runtime"}
    out = SM.classify(inv, static, clients, [dec(f, 24, "migrated")], {"c86": no, "c109": no}, [{"file": f, "line": 24}])
    assert out["counts"]["moved_verified"] == 2
    assert all("dynamic" in r["evidence"] for r in out["sites"])
    # without the probe the same claim is a claim the route check contradicts
    out = SM.classify(inv, static, clients, [dec(f, 24, "migrated")], {"c86": no, "c109": no}, [])
    assert out["counts"]["claimed_not_routing"] == 2 and out["counts"]["moved_verified"] == 0


def test_keep_on_a_client_the_scanner_cant_link_covers_its_calls():
    """gpt-cli cohere.py: the scanner sees the client (:38) but not the calls (:62, :95); the only client in the file serves them."""
    f = "gptcli/providers/cohere.py"
    inv = [inv_site(f, 62, "chat", endpoint="other:cohere", sdk="other:cohere"), inv_site(f, 95, "chat", endpoint="other:cohere", sdk="other:cohere")]
    clients = [{"id": "co", "file": f, "line": 38, "wrapped": False}]
    out = SM.classify(inv, [], clients, [dec(f, 38, "cannot_route", "Cohere's own API")], {}, [])
    assert out["counts"]["correctly_kept"] == 2


# ------------------------------------------------------------------------------------------------ defect 3: buckets
def test_every_movable_site_lands_in_exactly_one_bucket():
    f, g, t = "app/llm.py", "app/other.py", "app/table.ts"
    inv = [inv_site(f, 10),                                   # moved_verified
           inv_site(f, 30),                                   # claimed_not_routing
           inv_site(f, 50),                                   # moved_unverified (scanner sees nothing there)
           inv_site(g, 10, endpoint="other:api.deepseek.com"),  # correctly_kept (moving it takes a vendor swap)
           inv_site(g, 40),                                   # missed (kept, but no trade-off needed)
           inv_site(g, 70),                                   # missed (no decision)
           inv_site(f, 90, surface="embeddings"),             # not movable; moved -> wrong
           inv_site(t, 5, "chat"), inv_site(t, 9, "chat"), inv_site(t, 13, "chat"),  # a provider table: surfaced as a group
           inv_site("app/fn.py", 20)]                         # surfaced: needs_decision on a branch line in its function
    static = [call(f, 10, "s10", "cl", outcome="migrated"), call(f, 30, "s30", "cl", outcome="migrated"),
              call(f, 90, "s90", "cl", outcome="migrated", surface="embeddings")]
    decs = [dec(f, 10, "migrated"), dec(f, 30, "migrated"), dec(f, 50, "migrated"), dec(f, 90, "migrated"),
            dec(g, 10, "cannot_route", "DeepSeek is not in the catalog"), dec(g, 40, "kept", "left as is"),
            dec(t, 40, "needs_decision", "every provider in this table is a BYOK choice"),
            dec("app/fn.py", 25, "needs_decision", "branch for vendor X")]
    checks = {"s10": {"routes": True, "reason": "valid Router request"}, "s30": {"routes": False, "reason": "stays on provider"},
              "s90": {"routes": True, "reason": "valid Router request"}}
    scope = lambda file, line: (None, "handle" if file == "app/fn.py" else "<module>")
    out = SM.classify(inv, static, [], decs, checks, [], scope)
    by = {(r["file"], r["line"]): r["bucket"] for r in out["sites"]}
    assert by[(f, 10)] == "moved_verified"
    assert by[(f, 30)] == "claimed_not_routing"
    assert by[(f, 50)] == "moved_unverified"
    assert by[(g, 10)] == "correctly_kept"
    assert by[(g, 40)] == "missed" and by[(g, 70)] == "missed"
    assert by[(f, 90)] == "wrong"
    assert by[(t, 5)] == by[(t, 9)] == by[(t, 13)] == "surfaced_for_decision"
    assert by[("app/fn.py", 20)] == "surfaced_for_decision"
    c = out["counts"]
    assert c["movable_total"] == 10 and sum(c[b] for b in SM.BUCKETS) == 10
    assert c["wrong"] == 1
    assert c["one_approval_away"] == c["moved_verified"] + c["surfaced_for_decision"] == 5


def test_arm_without_a_report_is_judged_by_its_code():
    f = "app/llm.py"
    inv = [inv_site(f, 10), inv_site(f, 30), inv_site(f, 50)]
    static = [call(f, 10, "s10", "cl", outcome="migrated"), call(f, 30, "s30", "cl2", wrapped=False)]
    out = SM.classify(inv, static, [], [], {"s10": {"routes": True, "reason": "valid Router request"}}, [], has_channel=False)
    by = {r["line"]: r["bucket"] for r in out["sites"]}
    assert by == {10: "moved_verified", 30: "missed", 50: "missed"}


def test_report_lines_with_ranges_and_comments_parse():
    assert SM.parse_loc("hedge_fund/llm/client.py:283-288 (DeepSeek branch)") == ("hedge_fund/llm/client.py", 283, 288)
    assert SM.parse_loc("`a/b.ts:12`") == ("a/b.ts", 12, 12)
    assert SM.parse_loc("no line here") is None


# ------------------------------------------------------------------------------------------------ defect 4: destructive edits
DESTRUCTIVE = """diff --git a/gptcli/providers/azure_openai.py b/gptcli/providers/azure_openai.py
deleted file mode 100644
index 1234567..0000000
--- a/gptcli/providers/azure_openai.py
+++ /dev/null
@@ -1,4 +0,0 @@
-class AzureOpenAICompletionProvider(OpenAICompletionProvider):
-    def __init__(self):
-        super().__init__()
-        self.key = os.environ["AZURE_OPENAI_API_KEY"]
diff --git a/gptcli/config.py b/gptcli/config.py
--- a/gptcli/config.py
+++ b/gptcli/config.py
@@ -1,3 +1,2 @@
-    openai_api_key: Optional[str] = os.environ.get("OPENAI_API_KEY")
     model: str = "gpt-4o"
diff --git a/gptcli/providers/anthropic.py b/gptcli/providers/anthropic.py
--- a/gptcli/providers/anthropic.py
+++ b/gptcli/providers/anthropic.py
@@ -1,2 +1,2 @@
-    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
+    client = wrap(anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]))
"""


def test_destructive_edits_from_the_diff():
    d = SM.destructive_edits(DESTRUCTIVE)
    assert d["files_deleted"] == ["gptcli/providers/azure_openai.py"]
    assert [x["name"] for x in d["defs_deleted"]] == ["class AzureOpenAICompletionProvider"]  # its __init__ goes with it
    keys = {k["name"] for k in d["keys_removed"]}
    assert keys == {"AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "openai_api_key"}  # the wrap keeps api_key / ANTHROPIC_API_KEY
    assert d["count"] == 1 + 1 + 3  # the deleted file, its class, three key names
    assert any("azure_openai.py" in e for e in d["examples"])


def test_a_wrap_alone_is_not_destructive():
    d = SM.destructive_edits("diff --git" + DESTRUCTIVE.split("diff --git")[-1])  # only the anthropic.py wrap
    assert d["count"] == 0


# ------------------------------------------------------------------------------------------------ defect 5: minutes
def test_minutes_read_the_same_way_for_every_arm(tmp_path):
    (tmp_path / "agent.json").write_text(json.dumps({"agent": {"exit": 0}, "minutes": 8.8}))
    assert SM.run_minutes(str(tmp_path), {"minutes": None}, {"cost": {"agent_minutes": 8.6}}, None) == (8.8, "agent.json")
    os.remove(tmp_path / "agent.json")
    assert SM.run_minutes(str(tmp_path), {}, {"cost": {"agent_minutes": 8.6}}, None) == (8.6, "ledger cost")
    assert SM.run_minutes(str(tmp_path), {}, None, {"agent_minutes": 2.0}) == (2.0, "transcript")
    assert SM.run_minutes(str(tmp_path), {}, None, None) == (None, None)


# ------------------------------------------------------------------------------------------------ defect 6: coverage
def test_runs_that_never_finished_are_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RUNS", str(tmp_path))
    d = tmp_path / "B" / "kotaemon"
    d.mkdir(parents=True)
    (d / "agent.json").write_text(json.dumps({"agent": {"exit": 143}, "minutes": 13.0}))
    (d / "status.json").write_text(json.dumps({"status": "running", "pid": 999999999}))
    assert SM.run_status("B", "kotaemon") == ("not_run", "interrupted before scoring (no result.json, agent exit 143)")
    assert SM.run_status("B", "gpt-cli") == ("not_run", "no run")


def test_a_claim_on_one_branch_does_not_claim_the_rest_of_the_table():
    """D_r2 on obsidian-copilot claimed the OPENAI and ANTHROPIC entries of a provider table; the other providers in the same
    function are not claimed (and a not_a_site on a model-name list is no keep for anything else)."""
    f = "src/chatModelManager.ts"
    inv = [inv_site(f, 136, "chat"), inv_site(f, 146), inv_site(f, 165, "chat", endpoint="other:api.cohere.ai"),
           inv_site(f, 183, "chat", endpoint="other:openrouter.ai")]
    decs = [dec(f, 136, "migrated"), dec(f, 146, "migrated"), dec(f, 155, "migrated"), dec(f, 58, "not_a_site", "a model list")]
    scope = lambda file, line: (None, "getModelConfig")
    out = SM.classify(inv, [], [], decs, {}, [], scope)
    by = {r["line"]: r["bucket"] for r in out["sites"]}
    assert by == {136: "moved_unverified", 146: "moved_unverified", 165: "missed", 183: "missed"}


def test_neighbouring_entries_of_one_report_are_separate_decisions():
    """D3free on ai-hedge-fund: provider branches 4 lines apart, each with its own entry (ranges included)."""
    f = "hedge_fund/llm/client.py"
    raw = [dict(dec(f, 257, "migrated"), source="MIGRATION.json"), dict(dec(f, 261, "migrated"), source="MIGRATION.json"),
           dict(dec(f, 262, "cannot_route", "Gemini", line_end=264), source="MIGRATION.json")]
    decs = SM.resolve_decisions(raw, lambda f, n: n, {})
    assert len(decs) == 3
    out = SM.classify([inv_site(f, 257, "chat"), inv_site(f, 261, "chat")], [], [], decs, {}, [])
    assert {r["line"]: r["decision"]["outcome"] for r in out["sites"]} == {257: "migrated", 261: "migrated"}
    # a claim from a later channel still overrides the floor plan at the same place
    plan = dict(dec(f, 257, "flagged"), source="plan")
    assert [d["outcome"] for d in SM.resolve_decisions([plan, raw[0]], lambda f, n: n, {})] == ["migrated"]


def test_with_no_decision_on_a_site_the_code_is_the_report():
    f = "app/llm.py"
    inv = [inv_site(f, 10), inv_site(f, 30)]
    static = [call(f, 10, "s10", "cl", outcome="migrated"), call(f, 30, "s30", "cl", outcome="migrated")]
    ok = {"routes": True, "reason": "valid Router request"}
    out = SM.classify(inv, static, [], [dec(f, 30, "kept", "keep it")], {"s10": ok, "s30": ok}, [])
    by = {r["line"]: r["bucket"] for r in out["sites"]}
    assert by == {10: "moved_verified", 30: "missed"}  # an explicit keep overrides a harmless wrap


def test_a_probe_at_a_chat_client_does_not_verify_an_embeddings_call_in_the_same_file():
    """sec-insights: the only client the scanner sees in llama_index_settings.py is the LLM; the embeddings call beside it is
    not served by it, so the LLM client's probe can't make the embeddings site a (wrong) move."""
    f = "backend/app/llama_index_settings.py"
    inv = [inv_site(f, 14, "chat", sdk="openai"), inv_site(f, 18, "embeddings", sdk="openai")]
    clients = [{"id": "llm", "file": f, "line": 28, "wrapped": True}]
    static = [call(f, 14, "s14", "llm", surface="chat")]
    out = SM.classify(inv, static, clients, [dec(f, 28, "migrated")], {"s14": {"routes": False, "reason": "x"}}, [{"file": f, "line": 28}])
    by = {r["line"]: r["bucket"] for r in out["sites"]}
    assert by == {14: "moved_verified", 18: "not_movable"}
    assert out["counts"]["wrong"] == 0


def test_a_probe_the_scanner_cant_place_verifies_one_claimed_site_of_its_file():
    """D_r2 on obsidian-copilot: the probe proves the providerFetch helper (line 35), which no scanner site or client explains;
    it verifies the nearest claimed site of that file, once, and never an unclaimed one."""
    f = "src/LLMProviders/chatModelManager.ts"
    inv = [inv_site(f, 136, "chat"), inv_site(f, 146), inv_site(f, 165, "chat")]
    decs = [dec(f, 136, "migrated"), dec(f, 146, "migrated")]
    out = SM.classify(inv, [], [], decs, {}, [{"file": f, "line": 35}])
    by = {r["line"]: r["bucket"] for r in out["sites"]}
    assert by == {136: "moved_verified", 146: "moved_unverified", 165: "missed"}
