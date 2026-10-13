"""v2 Improve phase: per-site signals and the MVP rule set -> recommendations.json (router_migrate/schemas/CONTRACTS.md section 2).

Signals per production site: latency_class (interactive | user_waiting | background), model_owner (app_literal |
operator_config | end_user | library_param | unknown), resolved_model (Router catalog id or None), feature (file:function,
the id the vendored runtime tags requests with and the policy matches on).

Rules (MVP):
  unlock.vendor_endpoint   kept site on another vendor's OpenAI-compatible endpoint whose model resolves exactly in the catalog
  unlock.stated_loss       flagged site whose only obstacle is an Anthropic beta flag that can be dropped (listed as lost)
  attr.metadata            spend tags for every Router-served Responses feature (policy defaults)
  cache.key                OpenAI-model feature with a long static system prompt (>= ~1,024 tokens)
  tier.flex_background     background latency class and a model with a measured flex tier (-50% measured on gpt-5.4-mini)
  fallback.candidates      capability-matched fallback models for a feature with a known model
  model.deprecated / model.newer_cheaper_same_family / model.right_size   cards only (never applied), app_literal and
                           operator_config owners only
Every lever not recommended for a scope is listed under considered_not_recommended with its reason.
"""
import ast
import hashlib
import json
import os
import re
import time

RULES_VERSION = "v2-mvp-1"
_MAPS = {}
MODEL_OWNERS_FOR_MODEL_CARDS = ("app_literal", "operator_config")

# Anthropic beta flags whose removal does not break a request that does not use the beta's tools/params (stated loss).
DROPPABLE_BETAS = {
    "prompt-caching-2024-07-31": "nothing functional: prompt caching is generally available without the beta header",
    "token-efficient-tools-2025-02-19": "the token-efficient tool-use encoding (more output tokens on tool calls)",
}
BG_PATH = re.compile(r"(^|[/_.-])(workers?|jobs?|tasks?|cron|batch(es)?|queues?|pipelines?|ingest(ion)?|etl|backfill|nightly|scheduled?|celery|consumers?|indexer|summari[sz]e_all)([/_.-]|$)", re.I)
WEB_PATH = re.compile(r"(^|/)(app/api/|pages/api/|api/routes?/|routes?/|server/|backend/)|route\.(t|j)sx?$|(^|/)(views|endpoints|handlers)/", re.I)
WEB_DEPS = ("next", "express", "fastify", "hono", "koa", "@remix-run", "fastapi", "flask", "django", "starlette", "gradio", "streamlit", "sanic", "aiohttp")


# ------------------------------------------------------------------------------------------------ repo + site signals
def repo_kind(root):
    """'app' (web/server framework dependency), 'cli' (console entry point / bin), else 'library'."""
    deps, has_bin = "", False
    for f in ("package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
        p = os.path.join(root, f)
        if os.path.exists(p):
            txt = open(p, encoding="utf-8", errors="replace").read()
            deps += txt.lower()
            if f == "package.json":
                try:
                    has_bin = has_bin or bool(json.loads(txt).get("bin"))
                except ValueError:
                    pass
            if f in ("pyproject.toml", "setup.py", "setup.cfg") and re.search(r"console_scripts|\[project\.scripts\]|\[tool\.poetry\.scripts\]|entry_points", txt):
                has_bin = True
    if any(re.search(r'["\'\s]%s["\'\s=<>~^@]' % re.escape(d), deps) for d in WEB_DEPS):
        return "app"
    return "cli" if has_bin else "library"


def line_maps(diff_text):
    """{file: f(old_line) -> new_line} from a unified diff (apply.diff): plan.json lines refer to the pre-apply source."""
    maps, cur, hunks = {}, None, []
    for l in (diff_text or "").splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
            hunks = maps.setdefault(cur, [])
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", l)
        if m and cur is not None:
            hunks.append((int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)))

    def make(hs):
        def f(n):
            delta = 0
            for a, al, c, cl in hs:
                if n >= a + al:
                    delta = (c + cl) - (a + al)
                elif n >= a:
                    return n + (c - a) + (cl - al if cl > al else 0)  # inside a changed hunk: best effort
            return n + delta
        return f
    return {k: make(v) for k, v in maps.items()}


def _at(site, maps):
    """Site with its line mapped to the current (post-apply) file."""
    f = (maps or {}).get(site["file"])
    return dict(site, line=f(int(site["line"]))) if f else site


def enclosing_function(root, site):
    """Name of the innermost function containing the site's line ('<module>' at top level), as the runtime reports it."""
    path = os.path.join(root, site["file"])
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return "<module>"
    line = int(site["line"])
    if site["file"].endswith(".py"):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return "<module>"
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                if node.lineno <= line <= end and (best is None or node.lineno > best.lineno):
                    best = node
        return best.name if best else "<module>"
    lines = src.splitlines()[:line]
    pat = re.compile(r"(?:function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|^\s*(?:public |private |protected |static |async )*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{)")
    for l in reversed(lines):
        m = pat.search(l)
        if m:
            name = next(g for g in m.groups() if g)
            if name not in ("if", "for", "while", "switch", "catch", "return"):
                return name
    return "<module>"


def _const_lookup(root, files, name):
    """String value of a module-level constant NAME = "literal" (Python) / const NAME = "literal" (TS) in the given files."""
    pat = re.compile(r"""(?:^|\b(?:export\s+)?(?:const|let|var)\s+)%s\s*(?::\s*\w+\s*)?=\s*["'`]([^"'`]+)["'`]""" % re.escape(name), re.M)
    for f in files:
        try:
            m = pat.search(open(os.path.join(root, f), encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        if m:
            return m.group(1)
    return None


def model_signal(root, site, kind, all_files, catalog):
    """(model_owner, model_value or None) for a site."""
    m = site.get("model") or {}
    val = m.get("value")
    if m.get("static") and val:
        return "app_literal", val
    text = str(val or "")
    if not text:
        return ("library_param" if kind == "library" else "unknown"), None
    default = None
    d = re.search(r"""(?:getenv|environ\.get|process\.env\.\w+\s*\?\?|process\.env\[[^\]]+\]\s*\?\?|\|\|)\s*\(?\s*(?:["'][A-Z0-9_]+["']\s*,\s*)?["'`]([A-Za-z0-9._:/-]+)["'`]""", text)
    if d:
        default = d.group(1)
    if re.search(r"getenv|environ|process\.env|settings\.|SETTINGS|Settings\(", text):
        return "operator_config", default
    if re.search(r"\b(req|request|body|params|query|args|argv|flags|options|opts|cli|user|profile|session|chatSettings|payload|input)\b", text):
        return "end_user", default
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", text):  # module constant
        v = _const_lookup(root, [site["file"]] + [f for f in all_files if re.search(r"(const|config|setting|model)", f, re.I)], text)
        if v:
            return "app_literal", v
    if re.search(r"config|cfg|conf\b", text, re.I):
        return {"app": "operator_config", "cli": "end_user", "library": "library_param"}[kind], default
    if kind == "library":
        return "library_param", default
    if kind == "cli":
        return "end_user", default
    return "unknown", default


def latency_class(site, kind, func):
    """background | interactive | user_waiting. Conservative: only clear background markers get 'background'."""
    blob = "%s/%s" % (site["file"], func)
    if BG_PATH.search(blob):
        return "background"
    if site.get("stream"):
        return "interactive"
    if kind == "app" and (WEB_PATH.search(site["file"]) or func in ("POST", "GET", "PUT", "handler", "route")):
        return "interactive"
    return "interactive" if kind == "app" else "user_waiting"


def static_system_prompt_tokens(root, site, func):
    """~tokens of the longest string literal in the site's file assigned to a prompt/system/instruction name (a static signal)."""
    try:
        src = open(os.path.join(root, site["file"]), encoding="utf-8", errors="replace").read()
    except OSError:
        return 0
    best = 0
    for m in re.finditer(r"""(?i)([A-Za-z_]*(?:prompt|system|instruction)[A-Za-z_]*)\s*[:=]\s*(?:f|r)?("{3}[\s\S]*?"{3}|'{3}[\s\S]*?'{3}|`[\s\S]*?`)""", src):
        best = max(best, len(m.group(2)) // 4)
    return best


# ------------------------------------------------------------------------------------------------ cards
def _etag(root, site, row):
    """hash(site code span + the model's catalog row): an approval goes stale when either changes."""
    site = _at(site, _MAPS.get(root))
    span = ""
    try:
        lines = open(os.path.join(root, site["file"]), encoding="utf-8", errors="replace").read().splitlines()
        i = int(site["line"]) - 1
        span = "\n".join(lines[max(0, i - 3): i + 4])
    except (OSError, ValueError):
        pass
    return hashlib.sha256((span + json.dumps(row or {}, sort_keys=True)).encode()).hexdigest()[:16]


def _card(rule, group, title, sites, sig, change, loses, inputs, evidence, impact, risk, effort, applies=True, options=None, etag=""):
    cid = "rec_%s_%s" % (rule.replace(".", "_"), hashlib.sha1(("%s|%s" % (rule, ",".join(sorted(s["id"] for s in sites)))).encode()).hexdigest()[:8])
    return {"id": cid, "rule": rule, "lever_group": group, "title": title,
            "scope": {"sites": [s["id"] for s in sites], "features": sorted({sig[s["id"]]["feature"] for s in sites}),
                      "latency_class": sorted({sig[s["id"]]["latency_class"] for s in sites})[0] if sites else None},
            "model_owner": sorted({sig[s["id"]]["model_owner"] for s in sites})[0] if sites else None,
            "change": change, "loses": loses, "rule_inputs": inputs, "evidence": evidence, "impact": impact, "risk": risk,
            "effort": effort, "options": (options or [])[:3], "etag": etag, "state": "new", "applies": applies}


def _price(row):
    p = (row or {}).get("pricing") or {}
    try:
        return float(p.get("input") or 0) + float(p.get("output") or 0)
    except ValueError:
        return 0.0


def _family(mid):
    m = re.match(r"(gpt-\d+(?:\.\d+)?|o\d|claude-[a-z]+|gemini-\d(?:\.\d)?|grok|deepseek|glm|kimi|qwen|minimax)", mid or "")
    return m.group(1) if m else None


def _caps_ok(cand, orig):
    if not cand or cand.get("status") != "active" or "responses" not in (cand.get("surfaces") or []):
        return False
    for k in ("tools", "structured_outputs"):
        if orig.get(k) and not cand.get(k):
            return False
    return set(orig.get("input_modalities") or ["text"]) <= set(cand.get("input_modalities") or ["text"])


def fallback_candidates(catalog, orig_id):
    """Up to 2 capability-matched candidates: same owner first, closest list price; then one cross-vendor option."""
    orig = catalog.by_id.get(orig_id)
    if not orig:
        return []
    op = _price(orig)
    pool = [m for m in catalog.models if m["id"] != orig_id and m.get("owned_by") in ("openai", "anthropic") and _caps_ok(m, orig)
            and not m.get("requires_byok") and op and 0.5 <= (_price(m) / op) <= 2.0]
    same = sorted([m for m in pool if m["owned_by"] == orig.get("owned_by")], key=lambda m: abs(_price(m) - op))[:1]
    other = sorted([m for m in pool if m["owned_by"] != orig.get("owned_by")], key=lambda m: abs(_price(m) - op))[:1]
    return [m["id"] for m in same + other]


def _created(mid):
    try:
        return json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "model_created.json")))["created"].get(mid) or 0
    except (OSError, ValueError, KeyError):
        return 0


def newer_cheaper(catalog, orig_id):
    orig = catalog.by_id.get(orig_id)
    if not orig or not _family(orig_id):
        return None
    fam = _family(orig_id)
    cands = [m for m in catalog.models if m["id"] != orig_id and m.get("owned_by") == orig.get("owned_by") and _caps_ok(m, orig)
             and (_family(m["id"]) or "").split("-")[0] == fam.split("-")[0] and 0 < _price(m) < _price(orig)
             and _created(m["id"]) > _created(orig_id) > 0]
    return sorted(cands, key=lambda m: -_created(m["id"]))[0]["id"] if cands else None


def replacement_for_deprecated(catalog, orig_id):
    orig = catalog.by_id.get(orig_id) or {}
    cands = [m for m in catalog.models if m.get("owned_by") == orig.get("owned_by") and m.get("status") == "active"
             and _caps_ok(m, dict(orig, structured_outputs=False)) and _price(m)]
    return sorted(cands, key=lambda m: _price(m))[0]["id"] if cands else None


def policy_scope(feature):
    """Where a feature-specific policy entry goes. Python: the exact feature id. TS/JS: a glob that also matches the compiled
    path a bundler produces (e.g. app/api/chat/route.ts:POST -> **app/api/chat/route.*:POST matches .next/server/.../route.js:POST)."""
    f, _, fn = feature.rpartition(":")
    if f.endswith(".py"):
        return ("features", feature)
    stem = re.sub(r"\.(m|c)?(t|j)sx?$", "", f)
    return ("globs", "**%s.*:%s" % (stem, fn))


def policy_patch(feature, entry):
    kind, name = policy_scope(feature)
    return {kind: {name: entry}}


def build(root, plan, catalog, repo=None, scan=None):
    """recommendations.json for a repo whose plan.json (v1) and scan.json are given (scan supplies client constructions)."""
    kind = repo_kind(root)
    diff_p = os.path.join(root, ".router-migrate", "apply.diff")
    maps = line_maps(open(diff_p).read()) if os.path.exists(diff_p) else {}
    _MAPS[root] = maps
    sites = [s for s in plan["sites"] if s.get("prod")]
    files = sorted({s["file"] for s in plan["sites"]} | set(_source_files(root)))
    sig = {}
    for s in sites:
        fn = enclosing_function(root, _at(s, maps))
        owner, mval = model_signal(root, s, kind, files, catalog)
        rid = catalog.resolve(mval)[0] if mval else None
        sig[s["id"]] = {"feature": "%s:%s" % (s["file"], fn), "function": fn, "latency_class": latency_class(s, kind, fn),
                        "model_owner": owner, "model_value": mval, "resolved_model": rid}
    cards, considered = [], []

    def consider(rule, ss, reason):
        considered.append({"rule": rule, "scope": {"sites": [x["id"] for x in ss], "features": sorted({sig[x["id"]]["feature"] for x in ss})}, "reason": reason})

    migrated = [s for s in sites if s["outcome"] == "migrated"]
    clients = {s["id"]: s for s in (scan or {}).get("sites", []) + plan["sites"] if s["kind"] == "client"}
    patched = {pt["site_id"] for pt in plan.get("patches") or []}

    def client_of(s):
        c = clients.get(s.get("via_client") or "")
        if c is None:  # same-file client of the same vendor family when the call was not linked
            same = [x for x in clients.values() if x["file"] == s["file"] and x["sdk"].split("-")[0] == s["sdk"].split("-")[0]]
            c = same[0] if len(same) == 1 else None
        return c

    def per_tenant(c):
        return bool(c and (c.get("api_key") or {}).get("per_tenant"))
    responses_surface = [s for s in migrated if not s["surface"].startswith("messages")]
    messages_surface = [s for s in migrated if s["surface"].startswith("messages")]

    # ---- unlock.vendor_endpoint
    for s in sites:
        if s["outcome"] != "kept" or "another vendor's endpoint" not in s.get("reason", "") or s["surface"] not in ("chat.completions", "framework.chat", "responses"):
            continue
        rid = sig[s["id"]]["resolved_model"] or (catalog.resolve(_default_model_in_file(root, s))[0] if _default_model_in_file(root, s) else None)
        host = _vendor_host(s.get("reason", ""))
        cl = client_of(s)
        if host and (cl is None or per_tenant(cl) or s["lang"] != "py"):
            consider("unlock.vendor_endpoint", [s], "client not resolvable in this file" if cl is None else "per-tenant credentials" if per_tenant(cl)
                     else "TS/JS vendor unlock codemod not built in the MVP")
            continue
        if rid and host and not host.startswith("$") and "(config)" not in host:
            row = catalog.by_id.get(rid)
            cards.append(_card("unlock.vendor_endpoint", "unlock", "Serve %s through Router instead of %s (same model)" % (rid, host), [s], sig,
                               {"kind": "code", "diff": "wrap the client with unlock_hosts=[%r]; requests whose model is a Router catalog id go to Router, every other request still goes to %s" % (host, host)},
                               ["%s's own serving (its rate limits, regions and any vendor-only features)" % host, "requests are billed by Router instead of %s" % host],
                               {"host": host, "model": rid, "model_source": "literal" if sig[s["id"]]["resolved_model"] else "the file's default model"},
                               {"level": "L0", "results": [{"level": "L0", "fact": "%s is a Router catalog id (exact match)" % rid, "source": "catalog"}]},
                               {"per_call_pct": None, "monthly_usd": None, "ignores": "price differences between %s and Router for this model (unmeasured)" % host},
                               2, "low", etag=_etag(root, s, row)))
            sig[s["id"]]["unlock"] = {"rule": "unlock.vendor_endpoint", "host": host, "client": cl["id"]}
        elif host:
            consider("unlock.vendor_endpoint", [s], "model is not a Router catalog id (%s)" % (sig[s["id"]]["model_value"] or _default_model_in_file(root, s) or "chosen at runtime, no default found"))

    # ---- unlock.stated_loss (Anthropic beta flags)
    for s in sites:
        if s["outcome"] != "flagged" or "beta" not in s.get("reason", "").lower():
            continue
        cl = client_of(s)
        if cl is None or cl["id"] not in patched or s["lang"] != "py":
            consider("unlock.stated_loss", [s], "the client is not one v1 patched (%s)" % ("unresolved" if cl is None else "TS/JS" if s["lang"] != "py" else (cl.get("api_key") or {}).get("why") and "per-tenant credentials" or "not patchable"))
            continue
        betas = _beta_literals(root, s)
        if betas is None:
            consider("unlock.stated_loss", [s], "a beta flag is chosen at runtime (not a literal): the loss cannot be stated")
            continue
        if betas and set(betas) <= set(DROPPABLE_BETAS):
            cards.append(_card("unlock.stated_loss", "unlock", "Move %s to Router by dropping the %s beta header" % (sig[s["id"]]["feature"], ", ".join(betas)), [s], sig,
                               {"kind": "code", "diff": "wrap the client with drop_betas=%r; a request that carries any other beta stays on Anthropic" % sorted(betas)},
                               [DROPPABLE_BETAS[b] for b in betas], {"betas": betas},
                               {"level": "L1", "results": [{"level": "L1", "fact": "only beta flags in the file: %s" % ", ".join(betas), "source": "static"}]},
                               {"per_call_pct": None, "monthly_usd": None, "ignores": "no price change (same model)"}, 1, "low",
                               etag=_etag(root, s, None)))
            sig[s["id"]]["unlock"] = {"rule": "unlock.stated_loss", "drop_betas": sorted(betas), "client": cl["id"]}
        else:
            consider("unlock.stated_loss", [s], "beta flags %s are required by the request's tools or params" % (", ".join(betas) if betas else "(not statically known)"))

    # ---- attr.metadata
    if responses_surface:
        name = (repo or os.path.basename(os.path.abspath(root))).split("/")[-1]
        cards.append(_card("attr.metadata", "same_model", "Tag Router spend for %d feature(s) with product=%s" % (len({sig[s['id']]['feature'] for s in responses_surface}), name),
                           responses_surface, sig, {"kind": "policy", "diff": json.dumps({"defaults": {"metadata": {"product": name}}})}, [],
                           {"product": name, "features": len(responses_surface)},
                           {"level": "L0", "results": [{"level": "L0", "fact": "Router stores metadata as request tags; metadata.product attributes spend in Ramp", "source": "docs"}]},
                           {"per_call_pct": 0, "monthly_usd": None, "ignores": "attribution only, no price change"}, 0, "none",
                           etag=hashlib.sha256(name.encode()).hexdigest()[:16]))
    if messages_surface:
        consider("attr.metadata", messages_surface, "Messages surface: Router-only policy keys are not applied on /v1/messages")

    by_feature = {}
    for s in responses_surface:
        by_feature.setdefault(sig[s["id"]]["feature"], []).append(s)
    for feat, ss in sorted(by_feature.items()):
        sg = sig[ss[0]["id"]]
        rid = sg["resolved_model"]
        row = catalog.by_id.get(rid) if rid else None
        owner_vendor = (row or {}).get("owned_by")
        # ---- cache.key
        toks = max(static_system_prompt_tokens(root, s, sg["function"]) for s in ss)  # whole-file signal: line mapping not needed
        if owner_vendor == "anthropic":
            consider("cache.key", ss, "Claude model: v1 already sends prompt_cache_key = the feature id")
        elif toks >= 1024:
            cards.append(_card("cache.key", "same_model", "Cache the ~%s-token static prompt in %s" % ("{:,}".format(toks), feat), ss, sig,
                               {"kind": "policy", "diff": json.dumps(policy_patch(feat, {"prompt_cache_key": "$feature"}))}, [],
                               {"static_prompt_tokens": toks, "model": rid},
                               {"level": "L1", "results": [{"level": "L1", "fact": "string literal of ~%d tokens assigned to a prompt/system name in %s" % (toks, ss[0]["file"]), "source": "static"}]},
                               {"per_call_pct": None, "monthly_usd": None, "ignores": "cache hit rate (unmeasured); cached input is billed at the catalog cache_read price"},
                               0, "none", etag=_etag(root, ss[0], row)))
        else:
            consider("cache.key", ss, "no static system prompt >= ~1,024 tokens found (longest ~%d tokens)" % toks)
        # ---- tier.flex_background
        flexable = bool(rid and _has_flex(rid))
        if sg["latency_class"] == "background" and flexable:
            cards.append(_card("tier.flex_background", "same_model", "Run %s on Flex (-50%% per call, background job)" % feat, ss, sig,
                               {"kind": "policy", "diff": json.dumps(policy_patch(feat, {"allow_flex_tier": True}))},
                               ["latency: Flex requests can take much longer (61 s measured on gpt-5-nano)"],
                               {"latency_class": "background", "model": rid},
                               {"level": "L1", "results": [{"level": "L0", "fact": "%s has a measured flex tier" % rid, "source": "measured"},
                                                           {"level": "L1", "fact": "background markers in %s" % feat, "source": "static"}]},
                               {"per_call_pct": -50, "monthly_usd": None, "ignores": "measured on gpt-5.4-mini only; unmeasured for %s" % rid if rid != "gpt-5.4-mini" else "monthly volume (no observe-mode data)"},
                               1, "none", etag=_etag(root, ss[0], row)))
        elif flexable:
            consider("tier.flex_background", ss, "latency class %s: Flex adds up to ~61 s (measured)" % sg["latency_class"])
        elif rid:
            consider("tier.flex_background", ss, "%s has no flex tier" % rid)
        else:
            consider("tier.flex_background", ss, "model chosen at runtime (%s); flex eligibility unknown" % (sg["model_value"] or "no default found"))
        # ---- fallback.candidates
        cands = fallback_candidates(catalog, rid) if rid else []
        if cands:
            cards.append(_card("fallback.candidates", "same_model", "Fail over %s from %s to %s on outages" % (feat, rid, " then ".join(cands)), ss, sig,
                               {"kind": "policy", "diff": json.dumps(policy_patch(feat, {"fallbacks": cands}))},
                               ["on a pre-stream failure (429/5xx/no permission) a different model answers that request"],
                               {"model": rid, "candidates": cands},
                               {"level": "L0", "results": [{"level": "L0", "fact": "candidates match %s's tools/structured-output/modality support and are within 0.5-2x its list price" % rid, "source": "catalog"},
                                                           {"level": "L0", "fact": "Router's models list advances on 403/unknown model (measured)", "source": "measured"}]},
                               {"per_call_pct": 0, "monthly_usd": None, "ignores": "price of the fallback model on the requests that fail over"},
                               2, "none", options=[{"fallbacks": cands[:1]}, {"fallbacks": cands}], etag=_etag(root, ss[0], row)))
        else:
            consider("fallback.candidates", ss, "model chosen at runtime; candidates need a known model" if not rid else "no capability-matched candidate within 0.5-2x price")

    # ---- model cards (never applied; app_literal / operator_config only)
    for feat, ss in sorted({**by_feature, **{sig[s["id"]]["feature"]: [s] for s in messages_surface}}.items()):
        sg = sig[ss[0]["id"]]
        rid = sg["resolved_model"]
        if sg["model_owner"] not in MODEL_OWNERS_FOR_MODEL_CARDS:
            consider("model.*", ss, "model owner is %s: model changes are the %s's choice" % (sg["model_owner"], {"end_user": "user", "library_param": "library caller"}.get(sg["model_owner"], "unknown owner")))
            continue
        if not rid:
            consider("model.*", ss, "model %r is not a Router catalog id" % sg["model_value"])
            continue
        row = catalog.by_id.get(rid) or {}
        common = dict(applies=False, etag=_etag(root, ss[0], row))
        where = "%s %s" % ("literal" if sg["model_owner"] == "app_literal" else "default config value", rid)
        if row.get("status") == "deprecated":
            rep = replacement_for_deprecated(catalog, rid)
            cards.append(_card("model.deprecated", "model", "%s is deprecated in Router's catalog: move %s to %s" % (rid, feat, rep), ss, sig,
                               {"kind": "code" if sg["model_owner"] == "app_literal" else "config", "diff": "change the %s to %s" % (where, rep)},
                               ["a different model answers every request: quality must be checked (L2 replay, not in the MVP)"],
                               {"model": rid, "status": "deprecated", "replacement": rep},
                               {"level": "L0", "results": [{"level": "L0", "fact": "catalog status of %s is deprecated" % rid, "source": "catalog"}]},
                               {"per_call_pct": _pct(catalog, rid, rep), "monthly_usd": None, "ignores": "list prices only; token counts differ by model"},
                               2, "low", **common))
        nc = newer_cheaper(catalog, rid)
        if nc:
            cards.append(_card("model.newer_cheaper_same_family", "model", "Try %s for %s (newer, %s%% list price)" % (nc, feat, _pct(catalog, rid, nc)), ss, sig,
                               {"kind": "code" if sg["model_owner"] == "app_literal" else "config", "diff": "change the %s to %s" % (where, nc)},
                               ["a different model answers every request: needs L2 evidence before applying"],
                               {"model": rid, "candidate": nc},
                               {"level": "L0", "results": [{"level": "L0", "fact": "%s: same vendor, same capabilities, lower list price" % nc, "source": "catalog"}]},
                               {"per_call_pct": _pct(catalog, rid, nc), "monthly_usd": None, "ignores": "list prices only; output length and quality differ by model"},
                               2, "low", **common))
        small = max(int(((s.get("static_kwargs") or {}).get("max_tokens") or (s.get("static_kwargs") or {}).get("max_output_tokens") or 0)) if str(((s.get("static_kwargs") or {}).get("max_tokens") or 0)).isdigit() else 0 for s in ss)
        if 0 < small <= 256 and _price(row) >= 2.0:
            cheaper = sorted([m for m in catalog.models if m.get("owned_by") == row.get("owned_by") and _caps_ok(m, row) and 0 < _price(m) <= _price(row) / 4], key=_price)
            if cheaper:
                cards.append(_card("model.right_size", "model", "Right-size %s: short outputs (max %d tokens) on %s" % (feat, small, rid), ss, sig,
                                   {"kind": "code" if sg["model_owner"] == "app_literal" else "config", "diff": "change the %s to %s" % (where, cheaper[-1]["id"])},
                                   ["a smaller model answers every request: L2/L3 evidence advised"],
                                   {"model": rid, "max_output_tokens": small, "candidate": cheaper[-1]["id"]},
                                   {"level": "L1", "results": [{"level": "L1", "fact": "max output tokens %d at the call site" % small, "source": "static"}]},
                                   {"per_call_pct": _pct(catalog, rid, cheaper[-1]["id"]), "monthly_usd": None, "ignores": "list prices only"},
                                   3, "low", **common))
    for c in cards:
        if c["lever_group"] == "model":
            assert c["model_owner"] in MODEL_OWNERS_FOR_MODEL_CARDS
    return {"catalog_version": getattr(catalog, "source", "snapshot"), "rules_version": RULES_VERSION, "repo": repo,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "repo_kind": kind, "signals": sig, "cards": cards,
            "considered_not_recommended": considered}


def _source_files(root, cap=4000):
    out = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("node_modules", ".git", ".next", "dist", "build", ".venv", "venv", "__pycache__", ".router-migrate")]
        for f in fs:
            if f.endswith((".py", ".ts", ".tsx", ".js", ".mjs")):
                out.append(os.path.relpath(os.path.join(d, f), root))
                if len(out) >= cap:
                    return out
    return out


def _pct(catalog, a, b):
    pa, pb = _price(catalog.by_id.get(a)), _price(catalog.by_id.get(b))
    return round(100.0 * (pb - pa) / pa) if pa and pb else None


def _has_flex(mid):
    try:
        return mid in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flex_models.json")))["flex_models"]
    except (OSError, ValueError, KeyError):
        return False


def _vendor_host(reason):
    m = re.search(r"client targets (\S+)", reason)
    return m.group(1) if m else None


def _default_model_in_file(root, site):
    """A literal default model in the site's file (e.g. `self.config.model = "grok-4.3"` / `model: str = "..."`)."""
    try:
        src = open(os.path.join(root, site["file"]), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"""\bmodel(?:_name)?\s*(?::\s*\w+\s*)?=\s*(?:[\w.]+\s+(?:or|\|\||\?\?)\s+)?["']([A-Za-z0-9._:/-]{3,})["']""", src)
    return m.group(1) if m else None


def _beta_literals(root, site):
    """Beta flags the site's file can send, or None when any flag is not a literal (e.g. `betas = [tool_group.beta_flag]`)."""
    try:
        src = open(os.path.join(root, site["file"]), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    consts = dict(re.findall(r"""^\s*([A-Z][A-Z0-9_]*)\s*=\s*["']([a-z][a-z0-9-]*-20\d\d-\d\d-\d\d)["']""", src, re.M))
    found = set(re.findall(r"""["']([a-z][a-z0-9-]*-20\d\d-\d\d-\d\d)["']""", src))
    exprs = re.findall(r"betas\.(?:append|extend)\(([^)]*)\)", src) + re.findall(r"betas\s*=\s*\[([^\]]*)\]", src) + re.findall(r"betas\s*=\s*([A-Za-z_][\w.]*)\s*(?:$|or|if)", src, re.M)
    for e in exprs:
        for item in [x.strip() for x in e.split(",") if x.strip()]:
            if re.fullmatch(r"""["'][a-z][a-z0-9-]*-20\d\d-\d\d-\d\d["']""", item) or item in consts or item in ("omit", "NOT_GIVEN", "None", "[]"):
                continue
            return None  # a beta chosen at runtime: cannot state the loss
    return sorted(found)


def render_markdown(rec, decisions):
    """RECOMMENDATIONS.md, rendered one-way from recommendations.json + decisions.json (edit decisions with `router-migrate decide`)."""
    dec = (decisions or {}).get("cards") or {}
    L = ["# Router recommendations", "",
         "Generated by `router-migrate recommend` (%s). One-way view: decide with `router-migrate decide <id> approve|dismiss|postpone|shadow-first` or in `router-migrate up`." % rec.get("rules_version"), ""]
    groups = [("unlock", "Unlock: move more traffic"), ("same_model", "Same model: tier, cache, fallbacks, tags (policy)"), ("model", "Model changes: recommendations only, never applied by the tool")]
    for g, title in groups:
        cs = [c for c in rec["cards"] if c["lever_group"] == g]
        if not cs:
            continue
        L += ["## %s" % title, ""]
        for c in cs:
            d = dec.get(c["id"], {})
            box = "x" if d.get("action") == "approve" else " "
            state = d.get("action", "undecided")
            imp = c["impact"].get("per_call_pct")
            L.append("- [%s] <!-- rec:%s --> **%s** · Risk %d · Evidence %s · %s · %s%s" % (
                box, c["id"], c["title"], c["risk"], c["evidence"]["level"], c["change"]["kind"], state, (" · %+d%% per call" % imp) if isinstance(imp, (int, float)) and imp else ""))
            L.append("  - rule `%s` %s; loses: %s" % (c["rule"], json.dumps(c["rule_inputs"]), "; ".join(c["loses"]) or "nothing"))
        L.append("")
    L += ["## Considered, not recommended", ""] + ["- `%s` %s: %s" % (x["rule"], ", ".join(x["scope"]["features"]) or "-", x["reason"]) for x in rec["considered_not_recommended"]] + [""]
    return "\n".join(L)
