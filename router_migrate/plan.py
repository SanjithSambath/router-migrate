"""Plan: classify every LLM site into migrated / kept / flagged / handoff with an exact reason, and pick client patches.

Outcomes (per call site):
  migrated  traffic from this site goes to Router once RAMP_ROUTER_API_KEY is set (its client is patched, surface is served)
  kept      stays on its provider by design: Router has no such endpoint, or the client targets another vendor's endpoint
  flagged   could go to Router but the tool will not move it automatically (unmapped model, beta API, server-side state...)
  handoff   needs a code change an agent can make (other-vendor SDK whose model Router serves, raw HTTP, other gateways)
"""
import os
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
    "responses.input_items": "Router keeps no stored responses (GET /v1/responses/{id}/input_items: no server state)",
    "responses.cancel": "Router keeps no stored/background responses to cancel",
    "responses.delete": "Router keeps no stored responses to delete",
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
# A file/dir named after another vendor (deepseek.ts, zhipuai.py, providers/groq/...). The vendor name may carry a suffix
# (zhipuai, togetherai, vertexai, azureopenai, grok3) but must start at a path/word boundary.
VENDOR_IN_PATH = re.compile(r"(?:^|[/_.-])(deepseek|groq|mistral|ollama|together|xai|grok|minimax|openrouter|perplexity|gemini|azure|"
                            r"lm_?studio|vllm|zhipu|dashscope|aimlapi|flowise|mlx|llama_?cpp|bedrock|vertex|cohere|fireworks|qwen|"
                            r"moonshot|kimi|siliconflow|baichuan|hunyuan|volcengine|doubao|ernie|qianfan|anyscale|cerebras|"
                            r"sambanova|nvidia_?nim|huggingface|hf_?inference)[a-z0-9]*(?:[._/-]|$)")
BETA_REASON = "Anthropic beta header: the runtime keeps these calls on Anthropic"
TENANT_REASON = "per-tenant provider credentials: all tenants would share one Router key (billing/isolation change)"
STATEFUL_REASON = "stateful Responses API use (previous_response_id/retrieve/conversation/store): Router keeps no server state"
FILES_REASON = "uses provider-side vector stores/files Router cannot see"
STATEFUL_SURFACES = {"responses.retrieve", "responses.input_items", "responses.cancel", "responses.delete"}
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
    if c.get("router_native"):
        return "router"  # measure-only: the base URL is assigned elsewhere in the file from a Router default / RAMP_ROUTER_* env
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
        m = re.search(r"\b(custom\w*|profile|user|session|req|request|body|params|tenant|workspace)\??\.[\w.?]*", text)
        if m:
            return "other:endpoint chosen at runtime (%s)" % m.group(0)  # user-defined endpoints are not the provider's API
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


ROUTER_TEXT = re.compile(r"router\.com|RAMP_ROUTER")


def _router_by_assignment(root, c):
    """Measure-only (scoring migrations someone else wrote): is a non-literal base URL like `self.base_url` or `base_url`
    assigned in the same file from a Router default or a RAMP_ROUTER_* env var (following one constant)?"""
    bu = c.get("base_url") or {}
    v = str(bu.get("value") or "")
    if not v or bu.get("static") or ROUTER_TEXT.search(v) or not re.fullmatch(r"[A-Za-z_][\w.]*", v):
        return False
    try:
        text = open(os.path.join(root, c["file"]), encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    name = re.escape(v.split(".")[-1])
    for m in re.finditer(r"(?m)\b%s\s*(?::[^=\n]+)?=\s*([^\n]+)" % name, text):
        rhs = m.group(1)
        if ROUTER_TEXT.search(rhs):
            return True
        for const in re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", rhs):
            if re.search(r"(?m)^\s*%s\s*(?::[^=\n]+)?=\s*[^\n]*(router\.com|RAMP_ROUTER)" % re.escape(const), text):
                return True
    return False


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
                named = [c for c in cands if s.get("receiver") and c.get("target") == s["receiver"]]
                if len(named) == 1:  # TS: the call names the instance it uses (custom('gpt-4o') -> const custom = createOpenAI(...))
                    s["client_id"], s["client_link"] = named[0]["id"], "same-file-name"
                    continue
                s["client_candidates"] = [c["id"] for c in cands]
                s["client_link"] = "ambiguous"
    return sites


def build(scan, catalog, include_nonprod=False, measure_only=False):
    """measure_only: score the repo as it is (no router-migrate patches assumed); used to score other migrations."""
    sites = link_same_file([dict(x) for x in scan["sites"]])
    clients = {s["id"]: s for s in sites if s["kind"] == "client"}
    if measure_only and scan.get("root"):
        for c in clients.values():
            if _router_by_assignment(scan["root"], c):
                c["router_native"] = True
    patches, plan_sites = [], []
    # per-tenant keys are a heuristic judgment, not a fact: when measuring code as it stands, a client someone deliberately
    # wrapped (the floor never wraps these) routes, so the heuristic only applies to unwrapped clients
    tenant = {cid for cid, c in clients.items() if (c.get("api_key") or {}).get("per_tenant") and not (measure_only and c.get("wrapped"))}
    stateful = _stateful_clients(sites)
    lits = _model_literals(scan, catalog)

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
            if c.get("wrapped") and tgt in ("provider", "provider-or-config"):
                patched[c["id"]] = tgt  # already goes through the vendored runtime in the code as it stands
            continue
        if not c["prod"] and not include_nonprod:
            continue  # tests/examples/evals are reported but not edited unless asked (--include-nonprod)
        if c["id"] in tenant:
            continue  # per-tenant keys: moving them onto one Router key is a billing/isolation decision, not a codemod (M6)
        if kind and tgt in ("provider", "provider-or-config"):
            pt = {"site_id": c["id"], "lang": c["lang"], "file": c["file"], "line": c["line"], "col": c["col"], "patch": kind,
                  "sdk": c["sdk"], "label": c["surface"]}
            if c["id"] in stateful and _family(c["sdk"]) == "openai":
                pt["opts"] = {"responses": False}  # C1: keep this client's whole /responses chain on the provider
            patches.append(pt)
            patched[c["id"]] = tgt
    by_sdk = {}
    for c in clients.values():
        by_sdk.setdefault((c["lang"], _family(c["sdk"])), []).append(c)

    # 2) classify LLM sites: API calls, plus framework model constructions (LangChain/LlamaIndex models are the call sites).
    #    Vercel AI SDK: generateText/streamText calls are the sites; provider constructions only count when no call was found.
    vercel_calls = any(s["kind"] == "call" and s["sdk"].startswith("vercel-ai") for s in sites)  # calls are labelled vercel-ai-<provider> when the provider resolves
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
            outcome, reason, client_id = _classify_vercel(s, vproviders, patched, catalog, lits)
        else:
            outcome, reason, client_id = _classify(s, surface, clients, patched, by_sdk, catalog, lits)
        outcome, reason, client_id = _post_checks(s, surface, outcome, reason, client_id, clients, tenant, stateful)
        if measure_only and outcome == "migrated" and s["kind"] == "module_client" and not s.get("installed"):
            outcome, reason = "flagged", "module-level %s usage with no router-migrate install hook in this file" % s["sdk"]
        plan_sites.append(dict(s, surface=surface, outcome=outcome, reason=reason, via_client=client_id,
                               router_eligible=surface in ROUTER_SURFACES or surface == "other.chat" and outcome == "handoff"))

    installs = sorted({(s["file"], "install_litellm") for s in plan_sites if s["sdk"] == "litellm" and s["outcome"] == "migrated"} |
                      {(s["file"], "install_openai_module") for s in plan_sites if s["kind"] == "module_client" and s["sdk"] == "openai"
                       and s["outcome"] == "migrated"})
    return {"catalog_source": catalog.source, "patches": patches, "installs": [{"file": f, "kind": k} for f, k in installs],
            "sites": plan_sites, "summary": summarize(plan_sites)}


def _post_checks(s, surface, outcome, reason, cid, clients, tenant, stateful):
    """Facts that override a 'migrated' (or 'flagged') verdict regardless of how the client was linked (H1, H2, C1, M6)."""
    if outcome not in ("migrated", "flagged") or not s["prod"] or s["kind"] not in ("call", "client", "module_client"):
        return outcome, reason, cid
    link = cid or s.get("client_id")
    if link and link in clients and client_target(clients[link]) == "router":
        return outcome, reason, cid  # already pointed at Router by hand
    vendor = VENDOR_IN_PATH.search(s["file"].lower())
    if vendor:
        return "kept", "call lives in a %s-specific module: that vendor's endpoint is not routed by the runtime" % vendor.group(1), cid
    if surface == "responses" and (link in stateful or _site_stateful(s)):
        return "kept", STATEFUL_REASON, cid
    if surface == "responses" and s.get("uses_provider_files"):
        return "kept", FILES_REASON, cid
    if outcome != "migrated":
        return outcome, reason, cid
    if surface.startswith("messages") and (s.get("has_beta") or surface == "messages.beta"):
        return "flagged", BETA_REASON, cid
    if surface.startswith("messages") and s.get("beta_split"):
        return "flagged", BETA_REASON + " (a sibling branch sends the same request through client.beta.messages when betas are set; " \
                                       "for a first-party Anthropic client that is the path taken, so this branch is not a routed path)", cid
    if link in tenant or (s["kind"] == "client" and s["id"] in tenant):
        return "flagged", _tenant_reason(clients.get(link) or s), cid
    return outcome, reason, cid


def _tenant_key(c):
    return bool((c.get("api_key") or {}).get("per_tenant"))


def _tenant_reason(c):
    why = (c.get("api_key") or {}).get("why")
    return TENANT_REASON + (" [api_key from %s]" % why if why else "")


def _site_stateful(s):
    return bool(s.get("has_prev_response") or s.get("has_conversation") or s.get("store_true") or s.get("has_background")
                or s["surface"] in STATEFUL_SURFACES)


def _stateful_clients(sites):
    """Clients with any stateful Responses use: their whole /responses traffic must stay on one backend (C1)."""
    out = set()
    for s in sites:
        if s["kind"] == "call" and _site_stateful(s):
            if s.get("client_id"):
                out.add(s["client_id"])
            elif len(s.get("client_candidates") or []) == 1:
                out.add(s["client_candidates"][0])
    return out


def _model_literals(scan, catalog):
    """{family: (names, resolvable names)} for model-name literals in the repo's production files (scan['model_literals'] when the
    scan carries them, else read from scan['root'] on disk). None when unavailable (synthetic scans)."""
    lits = scan.get("model_literals")
    if lits is None:
        root = scan.get("root")
        if not root or not os.path.isdir(root):
            return None
        from .scan_py import collect_model_literals
        lits = collect_model_literals(root)
    return {fam: (sorted(names), [n for n in sorted(names) if catalog.resolve(n)[0]]) for fam, names in lits.items()}


def _classify_vercel(s, providers, patched, catalog, lits=None):
    if s["surface"] == "framework.embeddings":
        return "kept", "Router has no embeddings endpoint (measured 404)", None
    if not s["prod"]:
        return "kept", "non-production code (tests/examples/evals): reported, not edited (use --include-nonprod)", None
    on = [p for p in providers if p["id"] in patched]
    off = sorted({p["surface"] for p in providers if p["id"] not in patched})
    if on:
        fams = {"anthropic" if "Anthropic" in p["surface"] else "openai" for p in on}
        ok, why = _model_check(s, catalog, lits, fams.pop() if len(fams) == 1 else None)
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


def _site_family(s):
    sdk = s.get("sdk")
    if sdk in ("anthropic", "langchain-anthropic", "vercel-ai-anthropic") or str(s.get("surface") or "").startswith("messages") \
            or s.get("surface") == "ChatAnthropic":
        return "anthropic"
    if sdk in ("openai", "langchain-openai", "llamaindex-openai", "vercel-ai-openai", "azure-openai"):
        return "openai"
    return None


def _model_check(s, catalog, lits=None, family=None):
    m = s.get("model")
    if m and m.get("static") and m.get("value"):
        rid, why = catalog.resolve(m["value"])
        if not rid:
            return False, "model '%s' is not in the Router catalog (%s); the runtime leaves it on the provider. Map it explicitly with " \
                          "ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute" % (m["value"], why)
        return True, "model '%s' -> '%s' (%s)" % (m["value"], rid, why)
    fam = family or _site_family(s)
    if lits and fam in lits:
        names, ok_names = lits[fam]
        if names and not ok_names:
            return False, "model chosen at runtime and none of the repo's %s model names are in Router's catalog: %s" % (
                fam, ", ".join(names[:3]))
        if ok_names:
            return True, "model chosen at runtime; the runtime routes it when it is in the Router catalog, else the provider keeps it " \
                         "(repo names %d %s model(s), %d in the catalog, e.g. %s)" % (len(names), fam, len(ok_names), ok_names[0])
    return True, "model chosen at runtime; the runtime routes it when it is in the Router catalog, else the provider keeps it"


def _classify(s, surface, clients, patched, by_sdk, catalog, lits=None):
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
        return "flagged", BETA_REASON, s.get("client_id")
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
            ok, why = _model_check(s, catalog, lits)
            return ("migrated" if ok else "flagged"), "framework client points at Router natively; " + why, s["id"]
        if cid is None and _tenant_key(s) and client_target(s) in ("provider", "provider-or-config"):
            return "flagged", _tenant_reason(s), None
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
            ok, why = _model_check(s, catalog, lits)
            return ("migrated", "module-level %s client routed by install hook; %s" % (sdk, why), None) if ok else ("flagged", why, None)
        return "flagged", "module-level %s usage" % sdk, None
    else:
        link = s.get("client_link")
        if s.get("client_id"):
            c = clients.get(s["client_id"])
            if c and client_target(c) == "router":
                ok, why = _model_check(s, catalog, lits)
                if surface in ("chat.completions", "chat.completions.parse", "chat.completions.stream", "completions.legacy"):
                    return "flagged", "client points at Router but uses %s, which Router does not serve (measured 404)" % surface, c["id"]
                return ("migrated" if ok else "flagged"), "client points at Router natively; " + why, c["id"]
            if c and c["id"] in patched:
                cid = c["id"]
            elif c and _tenant_key(c) and client_target(c) in ("provider", "provider-or-config"):
                return "flagged", _tenant_reason(c), c["id"]
            elif c:
                tgt = client_target(c)
                if tgt.startswith("other:"):
                    return "kept", "client targets %s (another vendor's endpoint)" % tgt[6:], c["id"]
                if tgt in ("azure", "cloud"):
                    return "kept", "client is Azure/cloud-hosted (%s)" % c["surface"], c["id"]
        elif s.get("client_candidates"):
            pc = [c for c in s["client_candidates"] if c in patched]
            tc = [c for c in s["client_candidates"] if c in clients and _tenant_key(clients[c])]
            if tc and not pc:
                return "flagged", _tenant_reason(clients[tc[0]]), tc[0]
            if pc:
                cid = pc[0]
                others = ["%s%s" % (clients[c]["surface"], "" if client_target(clients[c]) in ("provider", "provider-or-config") else " -> " + client_target(clients[c]).replace("other:", ""))
                          for c in s["client_candidates"] if c not in patched and clients[c]["prod"]]
                if others:
                    ok, why = _model_check(s, catalog, lits)
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
            elif any(_tenant_key(c) for c in fam):
                # every client of this family in the repo is built from per-request keys: the call uses one of those
                return "flagged", _tenant_reason(next(c for c in fam if _tenant_key(c))), None
            else:
                return "handoff", "client is constructed outside this repo or not resolvable (%s); pass it through router_runtime.wrap_%s()" % (
                    link or "unresolved", "anthropic" if _family(sdk) == "anthropic" else "openai"), None
    ok, why = _model_check(s, catalog, lits)
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
