"""Plan: classify every LLM site into migrated / kept / flagged / handoff with an exact reason, and pick client patches.

Outcomes (per call site):
  migrated  traffic from this site goes to Router once RAMP_ROUTER_API_KEY is set (its client is patched, surface is served)
  kept      stays on its provider by design: Router has no such endpoint, or the client targets another vendor's endpoint
  flagged   could go to Router but the tool will not move it automatically (unmapped model, beta API, server-side state...)
  handoff   needs a code change an agent can make (other-vendor SDK whose model Router serves, raw HTTP, other gateways)
"""
import re
from urllib.parse import urlparse

ROUTER_SURFACES = {"chat.completions", "chat.completions.parse", "chat.completions.stream", "responses", "messages", "messages.stream",
                   "messages.count_tokens", "framework.chat"}
NO_ENDPOINT = {  # measured: these return 404/405 on Router
    "embeddings": "Router has no /v1/embeddings (measured 404)", "images": "Router has no images endpoints (measured 404)",
    "audio": "Router has no audio endpoints", "files": "Router has no /v1/files (measured 404)",
    "models": "Router serves GET /v1/models but model lists here describe the provider's account; kept",
    "completions.legacy": "Router has no legacy /v1/completions (measured 404)",
    "assistants": "Assistants/threads need provider-side state Router does not keep", "vector_stores": "Router has no vector stores",
    "responses.retrieve": "Router keeps no stored responses (GET /v1/responses/{id} measured 404)",
    "framework.embeddings": "Router has no embeddings endpoint (measured 404)", "other.embeddings": "Router has no embeddings endpoint",
}
PATCHABLE = {
    # sdk -> (python patch kind, ts handled by tool.mjs)
    "openai": "wrap_openai", "anthropic": "wrap_anthropic", "langchain-openai": "kwargs_langchain_openai",
    "langchain-anthropic": "kwargs_chat_anthropic", "llamaindex-openai": "kwargs_llamaindex",
    "vercel-ai-openai": "ts", "vercel-ai-anthropic": "ts",
}
FRAMEWORK_CLIENT_SURFACE = {"ChatOpenAI": "framework.chat", "ChatAnthropic": "framework.chat", "llama_index.OpenAI": "framework.chat",
                            "AzureChatOpenAI": "framework.chat", "OpenAIEmbeddings": "framework.embeddings",
                            "openai-default-provider": "framework.chat", "createOpenAI": "framework.chat", "createAnthropic": "framework.chat",
                            "gateway": "framework.chat"}
VERCEL_PROVIDERS = {"createOpenAI", "createAnthropic", "openai-default-provider", "anthropic-default-provider", "createOpenAICompatible",
                    "createTogetherAI", "createGroq", "createXai", "createMistral", "createGoogleGenerativeAI", "gateway", "createGateway"}
VENDOR_IN_PATH = re.compile(r"(?:^|/|_|-)(deepseek|groq|mistral|ollama|together|xai|grok|minimax|openrouter|perplexity|gemini|azure|lmstudio|"
                            r"vllm|zhipu|dashscope|aimlapi|flowise|mlx|llamacpp|bedrock|vertex|cohere|fireworks|qwen|moonshot)(?:[._/-]|$)")
OTHER_VENDOR_MODELS = re.compile(r"^(gemini|grok|deepseek|kimi|glm|qwen|minimax|gpt-oss|nemotron)", re.I)


def _host(u):
    try:
        return urlparse(u).hostname or ""
    except Exception:
        return ""


def client_target(c):
    """'provider' (default OpenAI/Anthropic endpoint or env/config-driven), 'other:<host>' or 'azure' / 'cloud'."""
    if c["sdk"] in ("azure-openai",):
        return "azure"
    if c["sdk"] in ("bedrock",) or c["surface"] in ("AnthropicVertex", "AsyncAnthropicVertex"):
        return "cloud"
    bu = c.get("base_url")
    if not bu or not bu.get("value"):
        return "provider"
    if "router.com" in str(bu.get("value")) or "RAMP_ROUTER" in str(bu.get("value")):
        return "router"  # already pointed at Router (native migration, e.g. by hand or by an agent)
    if not bu.get("static"):
        # config-driven. If the expression itself names another vendor (a fallback URL literal or a vendor env var), that is
        # where the traffic goes by default, and the runtime will leave it there.
        text = str(bu.get("value"))
        if re.search(r"azure|/deployments", text, re.I):
            return "azure"
        hosts = [h for h in re.findall(r"https?://([A-Za-z0-9.\-]+)", text) if h not in ("api.openai.com", "api.anthropic.com")]
        if hosts:
            return "other:" + hosts[0]
        envs = re.findall(r"[\"']([A-Z][A-Z0-9_]*(?:BASE|URL|ENDPOINT|HOST)[A-Z0-9_]*)[\"']", text)
        vendor_envs = [e for e in envs if not e.startswith(("OPENAI", "ANTHROPIC", "RAMP_ROUTER"))]
        if vendor_envs:
            return "other:$" + vendor_envs[0]
        named = [m for m in re.findall(r"([a-z][a-z0-9]*)_(?:base_url|api_base|api_url|endpoint|host)", text)
                 if m not in ("openai", "anthropic", "base", "api", "default", "llm", "model", "client", "custom", "router")]
        if named:
            return "other:%s (config)" % named[0]
        return "provider-or-config"  # Router takes it when the config resolves to api.openai.com / api.anthropic.com
    h = _host(bu["value"])
    if h in ("api.openai.com", "api.anthropic.com"):
        return "provider"
    return "other:" + (h or bu["value"][:40])


def link_same_file(sites):
    """Calls with no resolved client: link to the only client of the same SDK family constructed in the same file."""
    by_file = {}
    for c in sites:
        if c["kind"] == "client":
            by_file.setdefault((c["file"], _family(c["sdk"])), []).append(c)
    for s in sites:
        if s["kind"] == "call" and not s.get("client_id") and not s.get("client_candidates"):
            fam = _family(s["sdk"])
            cands = by_file.get((s["file"], fam), []) + ([] if fam != "openai" else by_file.get((s["file"], "azure-openai"), []))
            if len(cands) == 1:
                s["client_id"] = cands[0]["id"]
                s["client_link"] = "same-file-unique"
                if cands[0]["sdk"] in ("azure-openai",):
                    s["sdk"] = "azure-openai"
            elif len(cands) > 1:
                s["client_candidates"] = [c["id"] for c in cands]
                s["client_link"] = "ambiguous"
    return sites


def build(scan, catalog, include_nonprod=False, measure_only=False):
    """measure_only: score the repo as it is (no router-migrate patches assumed); used to score other migrations."""
    sites = link_same_file([dict(x) for x in scan["sites"]])
    clients = {s["id"]: s for s in sites if s["kind"] == "client"}
    patches, plan_sites = [], []

    # 1) which client constructions to patch
    patched = {}
    for c in clients.values():
        kind = PATCHABLE.get(c["sdk"])
        if c["lang"] == "ts":
            kind = "ts" if c["sdk"] in ("openai", "anthropic", "vercel-ai-openai", "vercel-ai-anthropic", "langchain-openai") else None
            if c["surface"] in ("OpenAIEmbeddings", "AzureOpenAI", "AzureChatOpenAI", "gateway"):
                kind = None
        if c["surface"] == "OpenAIEmbeddings":
            kind = None
        tgt = client_target(c)
        if measure_only:
            continue
        if not c["prod"] and not include_nonprod:
            continue  # tests/examples/evals are reported but not edited unless asked (--include-nonprod)
        if kind and tgt in ("provider", "provider-or-config"):
            patches.append({"site_id": c["id"], "lang": c["lang"], "file": c["file"], "line": c["line"], "col": c["col"], "patch": kind,
                            "sdk": c["sdk"], "label": c["surface"]})
            patched[c["id"]] = tgt
    by_sdk = {}
    for c in clients.values():
        by_sdk.setdefault((c["lang"], _family(c["sdk"])), []).append(c)

    # 2) classify LLM sites: API calls, plus framework model constructions (LangChain/LlamaIndex models are the call sites).
    #    Vercel AI SDK: generateText/streamText calls are the sites; provider constructions only count when no call was found.
    vercel_calls = any(s["kind"] == "call" and s["sdk"] == "vercel-ai" for s in sites)
    vproviders = [c for c in clients.values() if c["lang"] == "ts" and c["surface"] in VERCEL_PROVIDERS and c["prod"]]
    for s in sites:
        is_framework_client = s["kind"] == "client" and s["surface"] in FRAMEWORK_CLIENT_SURFACE
        if is_framework_client and vercel_calls and s["surface"] in VERCEL_PROVIDERS:
            continue
        if s["kind"] == "call" and s["sdk"] in ("langchain-openai", "langchain-anthropic", "llamaindex-openai"):
            continue  # LangChain/LlamaIndex sites are their model constructions; .invoke()/.stream() would double count
        if s["kind"] not in ("call", "module_client", "raw_http") and not is_framework_client:
            continue
        if s["kind"] == "raw_http" and s["surface"] == "other":
            continue  # a URL mention that is not a generation endpoint (e.g. a docs link or models list)
        surface = FRAMEWORK_CLIENT_SURFACE.get(s["surface"], s["surface"]) if is_framework_client else s["surface"]
        if s["kind"] == "call" and s["sdk"] == "vercel-ai":
            outcome, reason, client_id = _classify_vercel(s, vproviders, patched, catalog)
        else:
            outcome, reason, client_id = _classify(s, surface, clients, patched, by_sdk, catalog)
        plan_sites.append(dict(s, surface=surface, outcome=outcome, reason=reason, via_client=client_id,
                               router_eligible=surface in ROUTER_SURFACES or surface == "other.chat" and outcome == "handoff"))

    installs = sorted({(s["file"], "install_litellm") for s in plan_sites if s["sdk"] == "litellm" and s["outcome"] == "migrated"} |
                      {(s["file"], "install_openai_module") for s in plan_sites if s["kind"] == "module_client" and s["sdk"] == "openai"
                       and s["outcome"] == "migrated"})
    return {"catalog_source": catalog.source, "patches": patches, "installs": [{"file": f, "kind": k} for f, k in installs],
            "sites": plan_sites, "summary": summarize(plan_sites)}


def _classify_vercel(s, providers, patched, catalog):
    if s["surface"] == "framework.embeddings":
        return "kept", "Router has no embeddings endpoint (measured 404)", None
    if not s["prod"]:
        return "kept", "non-production code (tests/examples/evals): reported, not edited (use --include-nonprod)", None
    on = [p for p in providers if p["id"] in patched]
    off = sorted({p["surface"] for p in providers if p["id"] not in patched})
    if on:
        ok, why = _model_check(s, catalog)
        if not ok:
            return "flagged", why, on[0]["id"]
        tail = ("; stays on its provider when the configured provider is %s" % "/".join(off)) if off else ""
        return "migrated", "Vercel AI SDK call; %s provider patched with routerFetch%s. %s" % (
            "/".join(sorted({p["surface"] for p in on})), tail, why), on[0]["id"]
    if any(p["surface"] in ("gateway", "createGateway") for p in providers):
        return "handoff", "Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision", None
    if providers:
        return "kept", "Vercel AI SDK call on %s provider(s); Router does not serve those endpoints" % "/".join(off), None
    return "handoff", "Vercel AI SDK call whose provider is constructed outside this repo; wrap it with routerOptions()", None


def _family(sdk):
    return {"langchain-openai": "openai", "llamaindex-openai": "openai", "vercel-ai-openai": "openai", "langchain-anthropic": "anthropic",
            "vercel-ai-anthropic": "anthropic"}.get(sdk, sdk)


def _model_check(s, catalog):
    m = s.get("model")
    if m and m.get("static") and m.get("value"):
        rid, why = catalog.resolve(m["value"])
        if not rid:
            return False, "model '%s' is not in the Router catalog (%s); the runtime leaves it on the provider. Map it explicitly with " \
                          "ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute" % (m["value"], why)
        return True, "model '%s' -> '%s' (%s)" % (m["value"], rid, why)
    return True, "model chosen at runtime; the runtime routes it when it is in the Router catalog, else the provider keeps it"


def _classify(s, surface, clients, patched, by_sdk, catalog):
    sdk = s["sdk"]
    if not s["prod"]:
        via = s.get("client_id") if s.get("client_id") in patched else (s["id"] if s["id"] in patched else None)
        if via is None:
            return "kept", "non-production code (tests/examples/evals): reported, not edited (use --include-nonprod)", None
    if s["kind"] == "raw_http" and surface not in ("chat.completions", "messages", "responses"):
        return "kept", "raw HTTP %s request (not generation traffic); describes the provider account" % surface, None
    if s["kind"] == "raw_http":
        return "handoff", "raw HTTP to %s %s: rewrite onto the OpenAI/Anthropic SDK (then the runtime applies) or call Router directly" % (
            "api.openai.com" if sdk == "openai" else "api.anthropic.com", surface), None
    if surface in NO_ENDPOINT:
        return "kept", NO_ENDPOINT[surface], None
    if sdk == "openai-v0":
        return "handoff", "openai<1.0 module API (openai.ChatCompletion): upgrade to the 1.x client, then the runtime applies", None
    if sdk in ("vercel-ai-gateway",):
        return "handoff", "Vercel AI Gateway is a different gateway; switching providers to createOpenAI({fetch: routerFetch()}) is a product decision", None
    if sdk in ("azure-openai",):
        return "kept", "Azure OpenAI deployments are customer-specific endpoints; not moved", None
    if sdk in ("bedrock",):
        return "kept", "AWS Bedrock client; not moved", None
    if surface == "messages.beta":
        return "flagged", "anthropic beta API (anthropic-beta header) is unmeasured on Router; the runtime keeps it on Anthropic", None
    if surface == "other.chat" or sdk in ("google-genai", "mistral", "cohere", "ollama", "groq", "other"):
        m = (s.get("model") or {}).get("value") if (s.get("model") or {}).get("static") else None
        rid, why = catalog.resolve(m) if m else (None, "")
        if rid:
            return "handoff", "%s SDK call; Router serves '%s' as '%s' via /v1/responses: port this call to the OpenAI SDK + runtime" % (sdk, m, rid), None
        extra = " (model %s: %s)" % (m, why) if m else ""
        return "kept", "%s SDK / endpoint%s; Router does not serve this vendor's endpoint with the shared key" % (sdk, extra), None
    if s.get("has_prev_response"):
        return "flagged", "previous_response_id needs provider-side state (Router: 400 on OpenAI, ignored elsewhere [measured]); runtime keeps it on the provider", None
    # find the client
    cid = None
    if s["kind"] == "client":
        cid = s["id"] if s["id"] in patched else None
        if cid is None and client_target(s) == "router":
            ok, why = _model_check(s, catalog)
            return ("migrated" if ok else "flagged"), "framework client points at Router natively; " + why, s["id"]
        if cid is None:
            tgt = client_target(s)
            if tgt.startswith("other:"):
                return "kept", "client targets %s (another vendor's endpoint)" % tgt[6:], None
            if tgt in ("azure", "cloud"):
                return "kept", "Azure/cloud-hosted endpoint; not moved", None
            if s["surface"] == "gateway":
                return "handoff", "Vercel AI Gateway is a different gateway; moving it is a product decision", None
            return "flagged", "framework client could not be patched safely (see patch log)", None
    elif s["kind"] == "module_client":
        if sdk in ("openai", "litellm"):
            ok, why = _model_check(s, catalog)
            return ("migrated", "module-level %s client routed by install hook; %s" % (sdk, why), None) if ok else ("flagged", why, None)
        return "flagged", "module-level %s usage" % sdk, None
    else:
        link = s.get("client_link")
        if s.get("client_id"):
            c = clients.get(s["client_id"])
            if c and client_target(c) == "router":
                ok, why = _model_check(s, catalog)
                if surface in ("chat.completions", "chat.completions.parse", "chat.completions.stream", "completions.legacy"):
                    return "flagged", "client points at Router but uses %s, which Router does not serve (measured 404)" % surface, c["id"]
                return ("migrated" if ok else "flagged"), "client points at Router natively; " + why, c["id"]
            if c and c["id"] in patched:
                cid = c["id"]
            elif c:
                tgt = client_target(c)
                if tgt.startswith("other:"):
                    return "kept", "client targets %s (another vendor's endpoint)" % tgt[6:], c["id"]
                if tgt in ("azure", "cloud"):
                    return "kept", "client is Azure/cloud-hosted (%s)" % c["surface"], c["id"]
        elif s.get("client_candidates"):
            pc = [c for c in s["client_candidates"] if c in patched]
            if pc:
                cid = pc[0]
                others = ["%s%s" % (clients[c]["surface"], "" if client_target(clients[c]) in ("provider", "provider-or-config") else " -> " + client_target(clients[c]).replace("other:", ""))
                          for c in s["client_candidates"] if c not in patched and clients[c]["prod"]]
                if others:
                    ok, why = _model_check(s, catalog)
                    return ("migrated" if ok else "flagged"), "migrated when the configured client is %s; stays put when it is %s. %s" % (
                        clients[cid]["surface"], "/".join(sorted(set(others))), why), cid
        if cid is None:
            fam = by_sdk.get((s["lang"], _family(sdk)), [])
            fam_patch = [c for c in fam if c["id"] in patched]
            fam = [c for c in fam if c["prod"]] or fam
            vendor = VENDOR_IN_PATH.search(s["file"].lower())
            if vendor and fam_patch:
                return "kept", "call lives in a %s-specific module and its client is not resolved statically; that vendor's endpoint " \
                               "is not routed by the runtime" % vendor.group(1), None
            if fam and len(fam_patch) == len(fam):
                cid = fam_patch[0]["id"]
                link = "repo-wide"
            elif fam_patch:
                return "flagged", "client not resolved statically; %d of %d %s clients in the repo are patched" % (len(fam_patch), len(fam), sdk), None
            else:
                return "handoff", "client is constructed outside this repo or not resolvable (%s); pass it through router_runtime.wrap_%s()" % (
                    link or "unresolved", "anthropic" if _family(sdk) == "anthropic" else "openai"), None
    ok, why = _model_check(s, catalog)
    if not ok:
        return "flagged", why, cid
    tgt = patched.get(cid, "provider")
    vendor = VENDOR_IN_PATH.search(s["file"].lower())
    if tgt == "provider-or-config" and vendor:
        return "kept", "config-driven client in a %s-specific module: that vendor's endpoint is not routed by the runtime" % vendor.group(1), cid
    note = " (base URL is config-driven: routed when it resolves to the provider default)" if tgt == "provider-or-config" else ""
    return "migrated", "client patched; %s%s" % (why, note), cid


def summarize(plan_sites):
    prod = [s for s in plan_sites if s["prod"]]
    count = lambda o: sum(1 for s in prod if s["outcome"] == o)
    eligible = [s for s in prod if s["router_eligible"] or s["outcome"] in ("migrated", "handoff", "flagged")]
    migrated = count("migrated")
    return {"sites_found": len(prod), "migrated": migrated, "handoff": count("handoff"), "kept": count("kept"), "flagged": count("flagged"),
            "router_eligible": len(eligible), "nonprod_sites": len(plan_sites) - len(prod),
            "callsite_coverage": round(migrated / len(prod), 4) if prod else None,
            "weighted_coverage": round(migrated / len(eligible), 4) if eligible else None}
