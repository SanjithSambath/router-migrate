"""Planner rules on synthetic scans (no files needed)."""
from router_migrate.catalog import Catalog
from router_migrate import plan

CAT = Catalog.load()


def site(i, kind, sdk, surface, **kw):
    d = {"id": "py:f.py:%d:0:%s" % (i, kind), "lang": "py", "file": "f.py", "line": i, "col": 0, "kind": kind, "sdk": sdk, "surface": surface,
         "stream": False, "async": False, "model": None, "base_url": None, "receiver": "client", "prod": True, "snippet": ""}
    d.update(kw)
    return d


def outcomes(sites):
    p = plan.build({"sites": sites}, CAT)
    return {s["line"]: (s["outcome"], s["reason"]) for s in p["sites"]}, p


def test_native_router_client_counts_as_migrated():
    c = site(1, "client", "openai", "OpenAI", base_url={"value": "https://api.router.com/v1", "static": True}, target="client")
    a = site(2, "call", "openai", "responses", client_id=c["id"], client_link="same-file", model={"value": "gpt-4o-mini", "static": True})
    b = site(3, "call", "openai", "chat.completions", client_id=c["id"], client_link="same-file")
    o, p = outcomes([c, a, b])
    assert o[2][0] == "migrated" and "natively" in o[2][1]
    assert o[3][0] == "flagged" and "404" in o[3][1]
    assert p["patches"] == []  # never wrap a client that already targets Router


def test_env_driven_router_base():
    c = site(1, "client", "openai", "OpenAI", base_url={"value": 'os.environ["RAMP_ROUTER_BASE_URL"]', "static": False}, target="client")
    a = site(2, "call", "openai", "responses", client_id=c["id"], client_link="same-file")
    o, _ = outcomes([c, a])
    assert o[2][0] == "migrated"


def test_ambiguous_client_reason_names_both():
    c1 = site(1, "client", "openai", "OpenAI", target="self.client")
    c2 = site(2, "client", "azure-openai", "AzureOpenAI", target="self.client")
    call = site(3, "call", "openai", "chat.completions", client_candidates=[c1["id"], c2["id"]], client_link="ambiguous")
    o, _ = outcomes([c1, c2, call])
    assert o[3][0] == "migrated" and "AzureOpenAI" in o[3][1]


def test_other_vendor_with_router_model_is_handoff():
    a = site(1, "call", "other", "other.chat", model={"value": "grok-4.3", "static": True})
    b = site(2, "call", "other", "other.chat", model={"value": "some-local-model", "static": True})
    c = site(3, "call", "other", "other.chat", model={"value": "gemini-2.5-flash", "static": True})
    o, _ = outcomes([a, b, c])
    assert o[1][0] == "handoff" and "grok-4.3" in o[1][1]
    assert o[2][0] == "kept"
    assert o[3][0] == "kept" and "bring-your-own-key" in o[3][1]  # Gemini needs BYOK on this account [measured catalog]


def test_coverage_math():
    c = site(1, "client", "openai", "OpenAI", target="client")
    sites = [c, site(2, "call", "openai", "chat.completions", client_id=c["id"], client_link="same-file"),
             site(3, "call", "openai", "embeddings", client_id=c["id"], client_link="same-file"),
             site(4, "call", "openai", "chat.completions", prod=False)]
    _, p = outcomes(sites)
    sm = p["summary"]
    assert (sm["sites_found"], sm["migrated"], sm["kept"], sm["nonprod_sites"]) == (2, 1, 1, 1)
    assert sm["callsite_coverage"] == 0.5 and sm["weighted_coverage"] == 1.0


def test_unmapped_static_model_is_flagged_not_migrated():
    c = site(1, "client", "anthropic", "Anthropic", target="client")
    a = site(2, "call", "anthropic", "messages", client_id=c["id"], client_link="same-file", model={"value": "claude-3-5-sonnet-20241022", "static": True})
    o, _ = outcomes([c, a])
    assert o[2][0] == "flagged" and "not in the Router catalog" in o[2][1]


def test_catalog_resolution_rules():
    assert CAT.resolve("gpt-4o-mini-2024-07-18")[0] == "gpt-4o-mini"
    assert CAT.resolve("gpt-4-turbo-preview")[0] is None
    assert CAT.resolve("gpt-4o-2024-05-13")[0] is None
    assert CAT.resolve("openai:gpt-4o")[0] == "gpt-4o"
    assert CAT.resolve("sonnet-4.6")[0] == "claude-sonnet-4-6"
    assert CAT.resolve("global.anthropic.claude-sonnet-5-5")[0] is None  # requires BYOK


def test_config_base_url_naming_another_vendor_is_kept():
    """mem0-style: base_url from config/env with another vendor's fallback must not count as migrated."""
    cases = {
        2: ('self.config.deepseek_base_url or os.getenv("DEEPSEEK_API_BASE") or "https://api.deepseek.com"', "kept"),
        4: ('os.getenv("XAI_API_BASE")', "kept"),
        6: ("self.config.lmstudio_base_url", "kept"),
        8: ('os.getenv("OPENAI_BASE_URL")', "migrated"),
        10: ("settings.base_url", "migrated"),
    }
    sites = []
    for line, (expr, _) in cases.items():
        c = site(line - 1, "client", "openai", "OpenAI", base_url={"value": expr, "static": False}, target="c%d" % line)
        sites += [c, site(line, "call", "openai", "chat.completions", client_id=c["id"], client_link="same-file")]
    o, _ = outcomes(sites)
    for line, (expr, want) in cases.items():
        assert o[line][0] == want, (expr, o[line])


def test_vendor_module_with_unresolved_client_is_kept():
    c = site(1, "client", "openai", "OpenAI", target="this.client", lang="ts")
    a = dict(site(2, "call", "openai", "chat.completions", lang="ts", client_link=None), file="src/engine/deepseek.ts")
    b = dict(site(3, "call", "openai", "chat.completions", lang="ts", client_link=None), file="src/engine/openAi.ts")
    o, _ = outcomes([c, a, b])
    assert o[2][0] == "kept" and "deepseek" in o[2][1]
    assert o[3][0] == "migrated"


def test_raw_http_model_listing_is_kept():
    r = site(1, "raw_http", "openai", "models", url="https://api.openai.com/v1/models")
    o, _ = outcomes([r])
    assert o[1][0] == "kept"


# ---- fixture repos (tests/fixtures/plan/<name>): real scanner + planner
import os  # noqa: E402

from router_migrate import scan_py  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "plan")


def planned(name, **scan_extra):
    sc = scan_py.scan(os.path.join(FIX, name))
    sc.update(scan_extra)
    p = plan.build(sc, CAT)
    return {(s["file"], s["line"]): s for s in p["sites"]}, p, {s["id"]: s for s in sc["sites"]}


def test_no_cross_file_by_name_link_and_vendor_module_kept():
    """H1: TaskWeaver-style ZhipuAI service: self.client is never linked to openai_llm.py's self.client by name."""
    o, _, raw = planned("crossfile")
    zh = raw[o[("llm/zhipuai.py", 11)]["id"]]
    assert zh.get("client_link") == "unresolved" and not zh.get("client_id")
    assert o[("llm/zhipuai.py", 11)]["outcome"] == "kept" and "zhipu" in o[("llm/zhipuai.py", 11)]["reason"]
    own = o[("llm/openai_llm.py", 9)]
    assert own["outcome"] == "migrated" and own["via_client"] == "py:llm/openai_llm.py:6:22:client"


def test_vendor_in_path_applies_to_linked_calls():
    """H1: opencommit deepseek.ts linked (by the TS scanner) to openAi.ts's patched client is still kept."""
    c = dict(site(1, "client", "openai", "OpenAI", target="this.client", lang="ts"), file="src/engine/openAi.ts")
    a = dict(site(2, "call", "openai", "chat.completions", lang="ts", client_id=c["id"]), file="src/engine/deepseek.ts")
    b = dict(site(3, "call", "openai", "chat.completions", lang="ts", client_id=c["id"]), file="src/engine/openAi.ts")
    o, _ = outcomes([c, a, b])
    assert o[2][0] == "kept" and "deepseek" in o[2][1]
    assert o[3][0] == "migrated"


def test_vendor_in_path_regex_coverage():
    for f in ["taskweaver/llm/zhipuai.py", "src/engine/deepseek.ts", "moonshot.py", "qwen_llm.py", "dashscope.py", "mistral.py", "groq.py",
              "togetherai.py", "fireworks.py", "ollama.py", "vllm.py", "lmstudio.py", "lm_studio.py", "xai.py", "grok.py", "openrouter.py",
              "perplexity.py", "cohere.py", "gemini.py", "bedrock.py", "vertexai.py", "azure_openai.py", "providers/groq/route.ts"]:
        assert plan.VENDOR_IN_PATH.search(f.lower()), f
    for f in ["llm/openai.py", "openai_models.py", "anthropic.py", "src/engine/openAi.ts", "app/api/chat/anthropic/route.ts", "agents/agent.py"]:
        assert not plan.VENDOR_IN_PATH.search(f.lower()), f


def test_anthropic_beta_header_and_betas_flagged():
    """H1: extra_headers carrying anthropic-beta (merged in the function), betas=, client.beta.messages, and the plain branch of an
    `if betas: beta.messages... else: messages...` split are flagged; a call with unrelated extra_headers stays migrated."""
    o, _, raw = planned("beta")
    for line in (11, 14, 17, 22, 24):
        assert o[("agent.py", line)]["outcome"] == "flagged", (line, o[("agent.py", line)]["reason"])
        assert o[("agent.py", line)]["reason"].startswith(plan.BETA_REASON)
    assert raw[o[("agent.py", 11)]["id"]]["has_beta"] and raw[o[("agent.py", 14)]["id"]]["has_beta"]
    assert raw[o[("agent.py", 24)]["id"]]["beta_split"]
    assert o[("agent.py", 27)]["outcome"] == "migrated"
    assert o[("agent.py", 11)]["via_client"] == "py:agent.py:6:32:client"  # `client or Anthropic()` is linked


def test_dynamic_model_with_no_catalog_names_is_flagged():
    """H1: chatbot-ui-style: model chosen at runtime, every Anthropic model name in the repo is outside the catalog."""
    c = site(1, "client", "anthropic", "Anthropic", target="client")
    a = site(2, "call", "anthropic", "messages", client_id=c["id"], model={"value": "chatSettings.model", "static": False})
    o = {s["line"]: s for s in plan.build({"sites": [c, a], "model_literals": {"anthropic": ["claude-2.1", "claude-instant-1.2",
                                                                                            "claude-3-opus-20240229"]}}, CAT)["sites"]}
    assert o[2]["outcome"] == "flagged"
    assert "none of the repo's anthropic model names are in Router's catalog: claude-2.1, claude-3-opus-20240229, claude-instant-1.2" in o[2]["reason"]
    o = {s["line"]: s for s in plan.build({"sites": [c, a], "model_literals": {"anthropic": ["claude-2.1", "claude-sonnet-4-5"]}}, CAT)["sites"]}
    assert o[2]["outcome"] == "migrated"
    o = {s["line"]: s for s in plan.build({"sites": [c, a], "model_literals": {"openai": ["gpt-2"]}}, CAT)["sites"]}
    assert o[2]["outcome"] == "migrated"  # no names of this family: no evidence either way


def test_collect_model_literals_reads_py_and_ts(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "models.ts").write_text('export const M = ["claude-2.1", "gpt-4o", `o3-mini`, "gpt-4o-mini-tts"]\n')
    (tmp_path / "app.py").write_text('DEFAULT = "claude-sonnet-4-5"\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text('M = "claude-3-haiku-20240307"\n')  # non-production: ignored
    lits = scan_py.collect_model_literals(str(tmp_path))
    assert lits == {"anthropic": ["claude-2.1", "claude-sonnet-4-5"], "openai": ["gpt-4o", "o3-mini"]}


def test_per_tenant_api_key_is_flagged_and_not_patched():
    """M6: keys from a per-request object or a per-user lookup are flagged and their clients left alone; env keys and constructor
    injection (configuration) still migrate."""
    o, p, raw = planned("tenant")
    for line in (15, 20):
        assert o[("app.py", line)]["outcome"] == "flagged" and plan.TENANT_REASON in o[("app.py", line)]["reason"], o[("app.py", line)]
    assert o[("app.py", 28)]["outcome"] == "migrated" and o[("app.py", 32)]["outcome"] == "migrated"
    assert sorted(pt["line"] for pt in p["patches"]) == [6, 25]
    keys = {s["line"]: s["api_key"] for s in raw.values() if s["kind"] == "client"}
    assert keys[14]["per_tenant"] and keys[19]["per_tenant"] and not keys[6]["per_tenant"] and not keys[25]["per_tenant"]


def test_via_client_points_at_client_actually_used():
    """L3: a local `client` in another function is a different variable; factory results link to the factory's client (sync/async
    matched); with two assignments in one function the nearest preceding one wins."""
    o, _, _ = planned("via_client")
    assert o[("models.py", 18)]["via_client"] == "py:models.py:14:15:client"  # self.get_client(key) -> sync OpenAI()
    assert o[("models.py", 22)]["via_client"] == "py:models.py:13:19:client"  # async_=True in an async def -> AsyncOpenAI()
    assert o[("models.py", 26)]["via_client"] == "py:models.py:25:17:client"
    assert o[("models.py", 28)]["via_client"] == "py:models.py:27:17:client"
    assert all(s["via_client"] != "py:models.py:6:17:client" for s in o.values() if s["surface"] != "embeddings")


def test_stateful_responses_client_kept_with_patch_opts():
    """C1: any previous_response_id on a client keeps all of that client's /responses sites on the provider (the chain must stay on
    one backend) and marks its patch responses=False; its chat sites still migrate; other clients are untouched."""
    o, p, _ = planned("stateful")
    assert o[("app.py", 11)]["outcome"] == "kept" and o[("app.py", 11)]["reason"] == plan.STATEFUL_REASON
    assert o[("app.py", 15)]["outcome"] == "kept" and o[("app.py", 15)]["reason"] == plan.STATEFUL_REASON  # no prev id, same client
    assert o[("app.py", 19)]["outcome"] == "migrated"  # chat.completions on the same client
    assert o[("app.py", 23)]["outcome"] == "migrated"  # store=False on another client is not stateful
    opts = {pt["line"]: pt.get("opts") for pt in p["patches"]}
    assert opts == {5: {"responses": False}, 6: None, 7: None}


def test_stateful_signals_on_synthetic_sites():
    c = site(1, "client", "openai", "OpenAI", target="client")
    sites = [c, site(2, "call", "openai", "responses", client_id=c["id"], model={"value": "gpt-4o-mini", "static": True}),
             site(3, "call", "openai", "responses.retrieve", client_id=c["id"])]
    o, p = outcomes(sites)
    assert o[2] == ("kept", plan.STATEFUL_REASON) and o[3][0] == "kept"
    assert p["patches"][0]["opts"] == {"responses": False}
    for flag in ("has_conversation", "store_true", "has_background"):
        o, _ = outcomes([c, site(2, "call", "openai", "responses", client_id=c["id"], **{flag: True})])
        assert o[2] == ("kept", plan.STATEFUL_REASON), flag


def test_responses_with_provider_vector_store_is_kept():
    """H2: file_search over vector_store_ids (here via a module-level TOOLS constant) stays on the provider."""
    o, _, raw = planned("stateful")
    assert o[("app.py", 28)]["outcome"] == "kept" and o[("app.py", 28)]["reason"] == plan.FILES_REASON
    assert raw[o[("app.py", 28)]["id"]]["uses_provider_files"]


def test_scanner_records_literal_kwargs():
    """H5: literal kwargs per call site (non-literals skipped) feed the static-kwargs replay."""
    o, _, raw = planned("stateful")
    assert raw[o[("app.py", 23)]["id"]]["static_kwargs"] == {"store": False, "temperature": 0.2, "max_output_tokens": 50,
                                                              "text": {"format": {"type": "json_object"}}}
    assert raw[o[("app.py", 28)]["id"]]["static_kwargs"] == {"tools": [{"type": "file_search", "vector_store_ids": ["vs_123"]}]}
    assert raw[o[("app.py", 11)]["id"]]["static_kwargs"] == {}  # previous_response_id=prev_id is not a literal


def test_unresolved_call_when_all_family_clients_are_per_tenant_is_flagged():
    """M6, screenshot-to-code-style: a factory builds AsyncOpenAI(api_key=<request key>) and passes it into a session class whose
    self._client call cannot be linked; the call is flagged per-tenant, not 'handoff: constructed outside this repo'."""
    c = dict(site(1, "client", "openai", "AsyncOpenAI", target="client",
                  api_key={"value": "openai_api_key", "static": False, "per_tenant": True, "why": "function parameter 'openai_api_key'"}),
             file="factory.py")
    a = dict(site(2, "call", "openai", "responses", receiver="self._client", client_link="unresolved"), file="session.py")
    o, p = outcomes([c, a])
    assert o[2][0] == "flagged" and plan.TENANT_REASON in o[2][1]
    assert p["patches"] == []


def test_ts_per_tenant_key_and_runtime_endpoint():
    """chatbot-ui: `new OpenAI({apiKey: profile.openai_api_key})` is per-tenant; `baseURL: customModel.base_url` is not the provider."""
    from router_migrate import plan as planmod
    from router_migrate.catalog import Catalog
    def client(i, **kw):
        return dict({"id": "ts:r%d.ts:1:0:client" % i, "lang": "ts", "file": "r%d.ts" % i, "line": 1, "col": 0, "kind": "client", "sdk": "openai",
                     "surface": "OpenAI", "model": None, "base_url": None, "receiver": None, "prod": True}, **kw)
    def call(i):
        return {"id": "ts:r%d.ts:5:0:call" % i, "lang": "ts", "file": "r%d.ts" % i, "line": 5, "col": 0, "kind": "call", "sdk": "openai",
                "surface": "chat.completions", "model": {"value": "gpt-4o-mini", "static": True}, "base_url": None, "receiver": "c",
                "prod": True, "via_client": "ts:r%d.ts:1:0:client" % i}
    sites = [client(1, api_key={"value": "profile.openai_api_key", "static": False, "per_tenant": True, "why": "profile.openai_api_key"}), call(1),
             client(2, base_url={"value": "customModel.base_url", "static": False}), call(2)]
    assert planmod.client_target(sites[2]).startswith("other:endpoint chosen at runtime")
    p = planmod.build({"sites": sites, "root": None}, Catalog.load())
    out = {s["file"]: s["outcome"] for s in p["sites"] if s["kind"] == "call"}
    assert out == {"r1.ts": "flagged", "r2.ts": "kept"}, [(s["file"], s["reason"]) for s in p["sites"]]
