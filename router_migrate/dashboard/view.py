"""The dashboard's plain-language view of a run (stdlib only).

`build(bundle, root, floor_diff, post_diff)` answers, for a person who did not watch the run:
- calls: every production LLM call, how it reached Router (lossless / by the agent / unlocked / left in place / needs you),
  the provider and model before and after, and the exact code line that changed;
- improvements: every card as a plain sentence, the change it makes (code or router.policy.json), its effect on each
  request (a key-by-key diff of the request body), the agent assumptions it rests on, and its trade-offs;
- assumptions: every judgment and claim the agent made, in plain words, with the lines it read.
Decision state (approved, applied, ...) stays with the page, which already tracks it per card.
"""
import json
import os
import re

STATUS = {
    "lossless": ("Moved, lossless", "Same model, same request: only the client changed."),
    "agent": ("Moved by the agent", "The agent moved it after reading the code; the tool confirmed the call now routes."),
    "unlocked": ("Unlocked", "Moved in Improve after you accepted a stated trade-off."),
    "decide": ("Needs your decision", "Moving it would change behavior; it stays where it is until you decide."),
    "left": ("Left in place", "Router can't serve it as written, or it is not a production LLM call."),
}
STATUS_ORDER = ["lossless", "agent", "unlocked", "decide", "left"]

HOSTS = [("api.x.ai", "xAI"), ("api.deepseek.com", "DeepSeek"), ("api.groq.com", "Groq"), ("openrouter.ai", "OpenRouter"),
         ("api.together", "Together"), ("api.mistral.ai", "Mistral"), ("generativelanguage.googleapis.com", "Google"),
         ("api.fireworks.ai", "Fireworks"), ("openai.azure.com", "Azure OpenAI"), ("localhost", "a local server"),
         ("127.0.0.1", "a local server"), ("api.anthropic.com", "Anthropic"), ("api.openai.com", "OpenAI")]
FRAMEWORK = [("llamaindex", "LlamaIndex"), ("llama_index", "LlamaIndex"), ("langchain", "LangChain"), ("litellm", "LiteLLM"),
             ("vercel", "Vercel AI SDK"), ("ai-sdk", "Vercel AI SDK")]
REASON_CODES = {
    "R_NO_ENDPOINT": "Router has no endpoint for this kind of call (embeddings, images, audio, files, moderation).",
    "R_OTHER_HOST": "In production it calls another host (for example a user-chosen provider), not this SDK's default.",
    "R_PER_TENANT": "Each user brings their own provider key; through Router they would all share one key.",
    "R_STATEFUL": "It relies on provider-side state (stored responses or files).",
    "R_BETA": "It uses an Anthropic beta feature Router doesn't accept as written.",
    "R_MODEL_NOT_IN_CATALOG": "Its model isn't in Router's catalog.",
    "R_DEAD_CODE": "The code is unused.",
    "R_NOT_PRODUCTION": "Not production traffic (a test, example, script or not an LLM call).",
    "R_RAW_REQUESTS": "Raw HTTP to the provider; moving it needs a code change the tool doesn't make.",
    "R_OTHER_GATEWAY": "It already goes through another LLM gateway.",
    "R_OTHER_VENDOR_SDK": "Another vendor's SDK; moving it needs a port.",
    "R_BYOK_REQUIRED": "Router serves this model only with your own provider key.",
    "R_UNDECIDED": "The code doesn't settle it; a person needs to check.",
    "R_GATE_FAILED": "The edit failed the tests and was rolled back.",
}

LEVER = {"tier.flex_background": "flex", "fallback.candidates": "fallback", "cache.key": "cache", "attr.metadata": "tags",
         "unlock.vendor_endpoint": "unlock", "unlock.stated_loss": "unlock", "model.deprecated": "model",
         "model.newer_cheaper_same_family": "model", "model.right_size": "model"}
LEVERS = [("flex", "Flex", "Half price for work nobody waits on"),
          ("fallback", "Fallbacks", "Another model answers when one fails"),
          ("cache", "Caching", "Repeated prompt text billed at a fraction"),
          ("tags", "Spend tags", "Spend per feature and product"),
          ("unlock", "Unlocks", "More traffic onto Router"),
          ("model", "Model changes", "Cheaper or newer models, tested first")]

LATENCY = {"background": "Nobody waits on it: it runs as a background job",
           "interactive": "A person watches the reply as it streams",
           "user_waiting": "A caller waits for the full answer"}
TASK = {"classification": "It sorts input into categories", "extraction": "It pulls structured fields out of text",
        "routing": "It picks where a request should go", "summarization": "It summarizes text", "chat": "It writes chat replies",
        "generation": "It writes longer content", "agentic": "It runs a tool-using agent loop",
        "reasoning": "It does multi-step reasoning", "other": "It does something else"}
CLAIM = {"migrated": "Moved it to Router", "kept": "Left it in place", "flagged": "Asked you to decide",
         "handoff": "Handed it to you", "not_a_site": "Said it is not a production LLM call"}

_BOILER = re.compile(r"router-migrate|router_runtime|routerRuntime|router_policy|routerPolicy|^\s*(try:|except ImportError)"
                     r"|_router_\w+\s*=\s*lambda|\bas _router_\w+,?\s*$|^\s*$")
_TRIVIAL = re.compile(r"^\s*[()\[\]{},;]*\s*$")


def short(feature):
    """svc/workers/nightly_summaries.py:summarize_all -> (nightly_summaries.py, summarize_all)"""
    path, _, fn = (feature or "").rpartition(":")
    return os.path.basename(path) if path else (feature or ""), fn or ""


def provider(site):
    base = str(site.get("base_url") or site.get("host") or "")
    for host, name in HOSTS:
        if host in base:
            return name, host
    reason = str(site.get("reason") or "")
    m = re.search(r"targets ([\w.-]+\.[a-z]{2,})", reason)
    if m:
        for host, name in HOSTS:
            if host in m.group(1):
                return name, m.group(1)
        return m.group(1), m.group(1)
    sdk = str(site.get("sdk") or "").lower()
    name = "Anthropic" if "anthropic" in sdk else "OpenAI" if ("openai" in sdk or not sdk) else sdk.replace("-", " ").title()
    return name, None


def framework(site):
    sdk = str(site.get("sdk") or "").lower()
    return next((n for k, n in FRAMEWORK if k in sdk), None)


# ------------------------------------------------------------------------------------------------ diffs
def parse_diff(text):
    """Unified diff -> {file: [block]}; a block is {"line": new-file line, "before": [...], "after": [...]} with the
    router-migrate boilerplate (import shims, runtime and policy files) left out."""
    out, cur, blk, new_n = {}, None, None, 0
    lines = (text or "").split("\n")

    def flush():
        nonlocal blk
        # a block that is only brackets after the boilerplate is gone was part of an import shim
        if blk and any(not _TRIVIAL.match(x) for x in blk["before"] + blk["after"]):
            out.setdefault(cur, []).append(blk)
        blk = None

    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            flush()
            a, b = l[4:].split("\t")[0], lines[i + 1][4:].split("\t")[0]
            cur = re.sub(r"^[ab]/", "", a if b == "/dev/null" else b)
            i += 2
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", l)
        if m:
            flush()
            new_n = int(m.group(1))
            i += 1
            continue
        if cur is None or _skip_file(cur):
            i += 1
            continue
        if l.startswith("-") and not l.startswith("---"):
            if blk is None or blk["after"]:
                flush()
                blk = {"line": new_n, "before": [], "after": []}
            if not _BOILER.search(l[1:]):
                blk["before"].append(l[1:])
        elif l.startswith("+") and not l.startswith("+++"):
            if blk is None:
                blk = {"line": new_n, "before": [], "after": []}
            if not _BOILER.search(l[1:]):
                blk["after"].append(l[1:])
            new_n += 1
        else:
            flush()
            if l.startswith(" "):
                new_n += 1
        i += 1
    flush()
    return out


def _skip_file(path):
    b = os.path.basename(path)
    return b in ("router_runtime.py", "router_policy.py", "routerRuntime.ts", "routerRuntime.js", "routerPolicy.ts",
                 "routerPolicy.js", "router.policy.json") or path.startswith(".router-migrate/")


def _near(blocks, line, k=2):
    blocks = [b for b in blocks or [] if b["before"] or b["after"]]
    return sorted(blocks, key=lambda b: abs(int(b["line"]) - int(line or 0)))[:k]


def _trim(lines, n=4):
    lines = [l.rstrip() for l in lines]
    return lines[:n] + (["…"] if len(lines) > n else [])


# ------------------------------------------------------------------------------------------------ calls
def _model(site, sig):
    m = site.get("model") or {}
    reason = str(site.get("reason") or "")
    mm = re.search(r"model '([^']+)' -> '([^']+)'", reason)
    if mm:
        a, b = mm.group(1), mm.group(2)
        return {"before": a, "after": b, "match": "1:1" if a == b else "mapped",
                "note": "same model id on Router" if a == b else "mapped to Router's id for the same model"}
    if m.get("static") and m.get("value"):
        return {"before": m["value"], "after": None, "match": None, "note": None}
    rm = sig.get("resolved_model")
    src = (sig.get("sources") or {}).get("resolved_model")
    if rm:
        return {"before": rm, "after": rm if site.get("outcome") == "migrated" else None, "match": "runtime",
                "note": "picked at run time; the agent found the default in config" if src == "agent" else "picked at run time"}
    if "chosen at runtime" in reason or (m and not m.get("static")):
        return {"before": None, "after": None, "match": "runtime", "note": "picked at run time; routes when it is in Router's catalog"}
    return {"before": None, "after": None, "match": None, "note": None}


PLAIN_REASON = [
    (r"another vendor's endpoint", "Calls {host} directly. Moving it changes who serves and bills it, so it is offered as an unlock."),
    (r"beta header", "Uses an Anthropic beta header that Router doesn't accept as written."),
    (r"per-tenant", "Each user brings their own provider key; through Router they would all share one key."),
    (r"embeddings", "Embeddings: Router has no embeddings endpoint."),
    (r"image", "Image generation: Router doesn't serve images."),
    (r"audio|speech|transcri", "Audio: Router doesn't serve audio endpoints."),
    (r"not a (production )?site|test|example|script", "Not production traffic (a test, example or script)."),
]


def _plain(reason, host):
    r = str(reason or "")
    m = re.match(r"^agent:\s*(R_[A-Z_]+)\s*(.*)$", r, re.S | re.I)
    if m:  # the post-agent plan carries the agent's reason code and its note
        return REASON_CODES.get(m.group(1)) or m.group(2)
    for pat, txt in PLAIN_REASON:
        if re.search(pat, r, re.I):
            return txt.format(host=host or "the vendor")
    return r[:1].upper() + r[1:] if r else ""


def _claims_by_site(claims):
    by = {}
    for c in claims or []:
        by.setdefault(c.get("file"), []).append(c)
    return by


def _claim_for(by_file, site, feature=None):
    """The agent's claim on this call: same feature (claims keep the line from before the agent's own edits, which can move
    the call), else the nearest line within 3."""
    best = None
    for c in by_file.get(site.get("file"), []):
        d = abs(int(c.get("line") or 0) - int(site.get("line") or 0))
        same = bool(feature) and c.get("feature") == feature
        mine = c.get("origin") == "agent" or c.get("by") == "agent" and not c.get("tool_outcome")
        if (same or d <= 3) and (best is None or (not same, not mine, d) < best[0]):
            best = ((not same, not mine, d), c)  # the agent's own claim before the tool's re-check of the edited code
    return best[1] if best else None


def calls_view(b, plan_sites, floor_blocks, post_blocks, unlocks, floor_sites=None):
    sig = (b.get("recommendations") or {}).get("signals") or {}
    claims = _claims_by_site((b.get("ledger") or {}).get("claims"))
    worklist = [w for w in (b.get("ledger") or {}).get("worklist") or [] if w.get("type") == "site"]
    unl_feat = {u.get("feature"): dict(u, site=sid) for sid, u in (unlocks or {}).items() if u.get("feature")}
    unl_ids = set((unlocks or {}).keys())
    cards = (b.get("recommendations") or {}).get("cards") or []
    pending_unlock = {f: c for c in cards if c["rule"].startswith("unlock.") for f in (c.get("scope") or {}).get("features") or []}
    floor_moved = floor_ids = None
    if floor_sites is not None:
        mv = [x for x in floor_sites if x.get("outcome") == "migrated"]
        floor_moved = {"%s:%s" % (x.get("file"), x.get("function") or "<module>") for x in mv}
        floor_ids = {x.get("id") for x in mv}
    out = []
    for s in plan_sites:
        sg = sig.get(s["id"]) or {}
        feature = sg.get("feature") or "%s:%s" % (s.get("file"), s.get("function") or "<module>")
        prov, host = provider(s)
        claim = _claim_for(claims, s, feature)
        unlocked = unl_feat.get(feature) or (s["id"] in unl_ids and {"card": unlocks[s["id"]].get("card")})
        if unlocked:
            status = "unlocked"
        elif s.get("outcome") == "migrated":
            # moved by the rules if the floor plan (made before any agent ran) migrated this feature; else by the agent
            by_rules = floor_moved is None or feature in floor_moved or s["id"] in floor_ids
            status = "lossless" if by_rules else "agent"
        elif s.get("outcome") in ("flagged", "handoff"):
            status = "decide"
        else:
            status = "left"
        client_file = s["file"]
        cid = s.get("client_id") or (s.get("via_client") if isinstance(s.get("via_client"), str) else None)
        if isinstance(cid, str) and cid.count(":") >= 3:
            client_file = cid.split(":")[1]
        fb = _near(floor_blocks.get(client_file), s.get("line"))
        pb = _near(post_blocks.get(client_file), s.get("line"))
        code = []
        if status in ("left", "decide"):
            fb, pb = [], []  # the client may be wrapped for its other calls, but this call doesn't route: no change to show
        for blk, by in [(x, "migrate") for x in fb] + [(x, "agent" if status == "agent" else "unlock" if status == "unlocked" else "after")
                                                       for x in pb]:
            code.append({"file": client_file, "line": blk["line"], "before": _trim(blk["before"]), "after": _trim(blk["after"]), "by": by})
        model = _model(s, sg)
        why = STATUS[status][1]
        if status == "agent":  # what the rules said before the agent moved it: the call a reviewer should check
            w = next((x for x in worklist if x.get("file") == s["file"] and claim and int(x.get("line") or 0) == int(claim.get("line") or -1)), None)
            said = re.sub(r"^\w+:\s*", "", (w or {}).get("why") or "")
            pl = _plain(said, host).rstrip(".")
            why = ("The rules flagged it: %s. The agent read the code and moved it anyway; the tool confirmed it now routes." % (pl[:1].lower() + pl[1:])
                   if said else STATUS["agent"][1])
        if status in ("left", "decide"):
            why = _plain(s.get("reason"), host) or why
        if status == "unlocked":
            c = next((x for x in cards if x["id"] == unlocked.get("card")), None) or pending_unlock.get(feature)
            why = "Moved in Improve, after you approved its unlock. %s" % (_sentence(c, feature, None, b)[1] if c else "")
        file_, fn = short(feature)
        out.append({
            "id": s["id"], "feature": feature, "file": s["file"], "line": s.get("line"), "fn": fn or "<module>", "fname": file_,
            "provider": prov, "host": host, "via": framework(s), "surface": s.get("surface"), "lang": s.get("lang"), "stream": bool(s.get("stream")),
            "status": status, "status_label": STATUS[status][0], "why": why, "reason": s.get("reason"),
            "model": model, "routes": status in ("lossless", "agent", "unlocked"),
            "agent": ({"outcome": claim.get("outcome"), "said": CLAIM.get(claim.get("outcome"), claim.get("outcome")),
                       "why": claim.get("why") or "", "evidence": claim.get("evidence") or [],
                       "confirmed": claim.get("confirmed"), "tool_outcome": claim.get("tool_outcome")} if claim else None),
            "unlock_card": (unlocked or {}).get("card") if unlocked else ((pending_unlock.get(feature) or {}).get("id")),
            "code": code})
    out.sort(key=lambda c: (STATUS_ORDER.index(c["status"]), c["file"], c["line"] or 0))
    return out


# ------------------------------------------------------------------------------------------------ improvements
def _q(v):
    return json.dumps(v)


def _pct(p):
    return ("−%g%%" % abs(p)) if p and p < 0 else ("+%g%%" % p if p else "")


def _impact(c):
    im = c.get("impact") or {}
    p = im.get("per_call_pct")
    if c["rule"] == "cache.key":
        m = re.search(r"-(\d+)%", im.get("detail") or "")
        return {"label": "−%s%% on cached input" % m.group(1) if m else "Cheaper repeats", "tone": "save"}
    if p:
        return {"label": "%s per call" % _pct(p) if c["lever_group"] != "model" else "%s list price" % _pct(p), "tone": "save" if p < 0 else "cost"}
    if c["rule"] == "attr.metadata":
        return {"label": "Visibility", "tone": "neutral"}
    if c["rule"] == "fallback.candidates":
        return {"label": "Reliability", "tone": "neutral"}
    if c["rule"].startswith("unlock."):
        return {"label": "More on Router", "tone": "neutral"}
    return {"label": "Same price", "tone": "neutral"}


def _feat_model(sig_by_feat, f, c):
    ri = c.get("rule_inputs") or {}
    return ri.get("model") or (sig_by_feat.get(f) or {}).get("resolved_model")


def _request(c, f, model, b):
    """The request body before and after, key by key (what Router receives for this feature)."""
    r, ri = c["rule"], c.get("rule_inputs") or {}
    m = _q(model) if model else "(set at run time)"
    try:
        patch = json.loads(c["change"]["diff"]) if c["change"].get("kind") == "policy" else {}
    except (ValueError, TypeError):
        patch = {}
    entry = ((patch.get("features") or {}).get(f) or {}) if f else {}
    if r == "tier.flex_background":
        return [{"op": "keep", "key": "model", "v": m}, {"op": "del", "key": "service_tier", "v": '"default"'},
                {"op": "add", "key": "allow_flex_tier", "v": "true"}]
    if r == "fallback.candidates":
        fbs = entry.get("fallbacks") or ri.get("candidates") or []
        return [{"op": "del", "key": "model", "v": m}, {"op": "add", "key": "models", "v": "[" + ", ".join([m] + [_q(x) for x in fbs]) + "]"}]
    if r == "cache.key":
        return [{"op": "keep", "key": "model", "v": m}, {"op": "add", "key": "prompt_cache_key", "v": _q(f or "<feature>")}]
    if r == "attr.metadata":
        prod = ((patch.get("defaults") or {}).get("metadata") or {}).get("product") or ri.get("product")
        return [{"op": "keep", "key": "metadata.feature", "v": _q("<file>:<function>")}, {"op": "add", "key": "metadata.product", "v": _q(prod)}]
    if r == "unlock.vendor_endpoint":
        host = ri.get("host") or "the vendor"
        return [{"op": "del", "key": "endpoint", "v": host}, {"op": "add", "key": "endpoint", "v": "api.router.com"},
                {"op": "keep", "key": "model", "v": _q(ri.get("model")) if ri.get("model") else m}]
    if r == "unlock.stated_loss":
        betas = ri.get("betas") or []
        return [{"op": "del", "key": "anthropic-beta", "v": ", ".join(betas) or "beta"}, {"op": "del", "key": "endpoint", "v": "api.anthropic.com"},
                {"op": "add", "key": "endpoint", "v": "api.router.com"}, {"op": "keep", "key": "model", "v": m}]
    if c["lever_group"] == "model":
        new = ri.get("candidate") or ri.get("replacement") or (re.findall(r"to ([\w.:-]+)$", c["change"].get("diff") or "") or [None])[0]
        return [{"op": "del", "key": "model", "v": m}, {"op": "add", "key": "model", "v": _q(new) if new else "…"}]
    return []


def _policy_change(c, f):
    """The router.policy.json lines a policy card adds."""
    try:
        patch = json.loads(c["change"]["diff"])
    except (ValueError, TypeError):
        return None
    lines = []
    if patch.get("defaults"):
        for k, v in patch["defaults"].items():
            lines.append('"%s": %s' % (k, json.dumps(v)))
        scope = "defaults (every feature)"
    else:
        for name, entry in (patch.get("features") or {}).items():
            for k, v in entry.items():
                lines.append('"%s": %s' % (k, json.dumps(v)))
        scope = f
    return {"file": "router.policy.json", "scope": scope, "before": [], "after": lines, "by": "policy"}


def _find_line(root, files, needle_re):
    for rel in files:
        p = os.path.join(root or "", rel)
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, l in enumerate(fh, 1):
                    if re.search(needle_re, l):
                        return rel, i, l.rstrip("\n")
        except OSError:
            continue
    return None


def _wrap_preview(line, fn, kw):
    """`client = OpenAI(...)` -> `client = _router_wrap_openai(OpenAI(...), unlock_hosts=[...])`; an existing wrap gains the kwarg."""
    m = re.match(r"^(\s*[\w.\[\]'\"]+\s*=\s*)(.+?)\s*$", line)
    if not m or "#" in m.group(2):
        return None
    lhs, rhs = m.groups()
    if rhs.startswith(fn + "(") and rhs.endswith(")"):
        return lhs + rhs[:-1] + ", " + kw + ")"
    return lhs + fn + "(" + rhs + ", " + kw + ")"


def _code_change(c, f, root, post_blocks, judgments, applied):
    r, ri = c["rule"], c.get("rule_inputs") or {}
    if c["change"].get("kind") == "policy":
        pc = _policy_change(c, f)
        return [pc] if pc else []
    path = (f or "").rpartition(":")[0]
    if r.startswith("unlock."):
        if applied and post_blocks.get(path):
            return [{"file": path, "line": b["line"], "before": _trim(b["before"]), "after": _trim(b["after"]), "by": "unlock"}
                    for b in post_blocks[path][:1]]
        if r == "unlock.vendor_endpoint":
            hit = _find_line(root, [path], re.escape(ri.get("host") or "\0"))
            after = hit and _wrap_preview(hit[2], "_router_wrap_openai", "unlock_hosts=[%r]" % (ri.get("host") or ""))
        else:
            hit = _find_line(root, [path], r"=\s*(_router_wrap_anthropic\()?(Async)?Anthropic\(")
            after = hit and _wrap_preview(hit[2], "_router_wrap_anthropic", "drop_betas=%r" % sorted(ri.get("betas") or []))
        if hit and after:
            return [{"file": hit[0], "line": hit[1], "before": [hit[2]], "after": [after], "by": "preview"}]
        return []
    if c["lever_group"] == "model":
        old = ri.get("model")
        new = ri.get("candidate") or ri.get("replacement")
        ev = [e.split(":")[0] for e in ((judgments.get(f) or {}).get("model") or {}).get("evidence") or []]
        if not (old and new):
            return []
        hit = _find_line(root, [path] + ev, r"[\"']%s[\"']" % re.escape(old))
        if hit:
            return [{"file": hit[0], "line": hit[1], "before": [hit[2]], "after": [hit[2].replace(old, new, 1)], "by": "you"}]
    return []


ASSUMES = {"tier.flex_background": ["latency"], "cache.key": ["cacheable"], "model.right_size": ["task"],
           "fallback.candidates": ["model"], "model.newer_cheaper_same_family": ["model"], "model.deprecated": ["model"]}


def _plain_judgment(key, val):
    if key == "latency":
        return LATENCY.get(val, val)
    if key == "task":
        return TASK.get(val, val)
    if key == "model":
        return "It runs on %s in production" % val
    if key == "cacheable":
        try:
            n = int(val)
        except (TypeError, ValueError):
            return str(val)
        return "About %s prompt tokens never change between calls" % "{:,}".format(n) if n else "Almost all of the prompt changes per call"
    return "%s: %s" % (key, val)


def _sentence(c, f, model, b):
    r, ri = c["rule"], c.get("rule_inputs") or {}
    _, fn = short(f)
    if r == "tier.flex_background":
        return "Run %s on Flex" % fn, "Half price per call for a job nobody waits on. Replies can take up to about a minute."
    if r == "fallback.candidates":
        fbs = ri.get("candidates") or []
        return "Add fallbacks to %s" % fn, "If %s fails before answering, %s answers instead." % (model or "the model", " then ".join(fbs) or "another model")
    if r == "cache.key":
        n = ri.get("static_prompt_tokens")
        return "Cache %s's fixed prompt" % fn, ("About %s prompt tokens repeat on every call; Router can bill repeats as cached input."
                                                % "{:,}".format(n) if n else "The prompt's fixed part repeats on every call.")
    if r == "attr.metadata":
        return "Tag spend as product=%s" % ri.get("product"), "Every request from this repo carries the product and its feature, so spend shows up per feature in Router."
    if r == "unlock.vendor_endpoint":
        return "Serve %s through Router" % fn, "Same model (%s), served and billed by Router instead of %s." % (ri.get("model") or model, ri.get("host"))
    if r == "unlock.stated_loss":
        return "Move %s to Router" % fn, "Drops the %s beta header; Router then serves the same model." % ", ".join(ri.get("betas") or ["Anthropic beta"])
    if r == "model.deprecated":
        return "Replace %s" % ri.get("model"), "%s is deprecated in Router's catalog; %s is the suggested replacement." % (ri.get("model"), ri.get("replacement"))
    if r == "model.newer_cheaper_same_family":
        return "Try %s for %s" % (ri.get("candidate"), fn), "A newer model in the same family, at a lower list price."
    if r == "model.right_size":
        return "Try a smaller model for %s" % fn, "The task is short and well specified; a smaller model may do it for less."
    return c["title"], ""


def improvements_view(b, root, post_blocks):
    r = b.get("recommendations") or {}
    sig_by_feat = {}
    for s in (r.get("signals") or {}).values():
        sig_by_feat.setdefault(s.get("feature"), s)
    judgments = ((b.get("run") or {}).get("judgments")) or {}
    dec = (b.get("decisions") or {}).get("cards") or {}
    out = []
    for c in r.get("cards") or []:
        feats = (c.get("scope") or {}).get("features") or []
        f = feats[0] if len(feats) == 1 else None
        model = _feat_model(sig_by_feat, f, c) if f else None
        title, summary = _sentence(c, f, model, b)
        applied = c.get("state") == "applied" or bool((dec.get(c["id"]) or {}).get("applied_at"))
        assumptions = []
        for key in ASSUMES.get(c["rule"], []):
            j = (judgments.get(f) or {}).get(key) if f else None
            if j:
                assumptions.append({"key": key, "text": _plain_judgment(key, j.get("value")), "why": j.get("why") or "",
                                    "evidence": j.get("evidence") or []})
        trade = []
        for l in c.get("loses") or []:
            if re.search(r"cancel|ignores allow_flex_tier", l, re.I):
                continue  # the either/or note says this, before anyone approves
            if re.search(r"different model answers every request", l, re.I):
                l = "a different model answers every request, so check quality on real traffic first (shadow it)"
            trade.append(l)
        out.append({
            "id": c["id"], "rule": c["rule"], "lever": LEVER.get(c["rule"], "other"), "title": title, "summary": summary,
            "tool_title": c.get("title"), "features": feats, "fn": short(f)[1] if f else ("%d features" % len(feats) if feats else ""),
            "kind": "model" if c.get("lever_group") == "model" or c.get("applies") is False else "unlock" if c["rule"].startswith("unlock.") else "same",
            "impact": _impact(c), "request": _request(c, f, model, b),
            "code": _code_change(c, f, root, post_blocks, judgments, applied), "assumptions": assumptions,
            "facts": c.get("tool_facts") or [], "tradeoffs": trade, "conflicts_with": c.get("conflicts_with") or [],
            "conflict_note": c.get("conflict_note"), "suggested": c["rule"] == "tier.flex_background" and bool(c.get("conflicts_with")),
            "options": c.get("options") or []})
    return out


def assumptions_view(b, imps):
    judgments = ((b.get("run") or {}).get("judgments")) or {}
    drives = {}
    for i in imps:
        for a in i["assumptions"]:
            for f in i["features"]:
                drives.setdefault((f, a["key"]), []).append(i["id"])
    out = []
    labels = {"latency": "Who waits", "task": "What it does", "model": "Production model", "cacheable": "Fixed prompt"}
    for f, js in sorted(judgments.items()):
        file_, fn = short(f)
        for key in ("latency", "task", "model", "cacheable"):
            j = js.get(key)
            if not j:
                continue
            out.append({"feature": f, "fn": fn, "fname": file_, "key": key, "label": labels[key], "value": j.get("value"),
                        "text": _plain_judgment(key, j.get("value")), "why": j.get("why") or "", "evidence": j.get("evidence") or [],
                        "drives": drives.get((f, key), [])})
    return out


def build(b, root=None, floor_diff=None, post_diff=None, plan_sites=None, unlocks=None, floor_sites=None):
    floor_blocks = parse_diff(floor_diff if floor_diff is not None else b.get("diff") or "")
    post_blocks = parse_diff(post_diff or "")
    calls = calls_view(b, plan_sites if plan_sites is not None else b.get("sites") or [], floor_blocks, post_blocks, unlocks or {},
                       floor_sites)
    imps = improvements_view(b, root, post_blocks)
    counts = {k: sum(1 for c in calls if c["status"] == k) for k in STATUS_ORDER}
    run = b.get("run") or {}
    return {
        "calls": calls, "improvements": imps, "assumptions": assumptions_view(b, imps),
        "counts": dict(counts, total=len(calls), routed=counts["lossless"] + counts["agent"] + counts["unlocked"]),
        "statuses": [{"k": k, "label": STATUS[k][0], "hint": STATUS[k][1]} for k in STATUS_ORDER],
        "levers": [{"k": k, "name": n, "hint": h} for k, n, h in LEVERS],
        "considered": [{"lever": LEVER.get(x["rule"], "other"), "fn": short(((x.get("scope") or {}).get("features") or [""])[0])[1],
                        "reason": x.get("reason")} for x in (b.get("recommendations") or {}).get("considered_not_recommended") or []],
        "steps": {"migrate": {"usd": (run.get("migrate") or {}).get("usd"), "minutes": (run.get("migrate") or {}).get("minutes"),
                              "turns": (run.get("migrate") or {}).get("turns"), "model": (run.get("migrate") or {}).get("model"),
                              "floor": run.get("floor_migrated"), "agent_edit_files": run.get("agent_edit_files")},
                  "improve": {"usd": (run.get("improve") or {}).get("usd"), "minutes": (run.get("improve") or {}).get("minutes"),
                              "turns": (run.get("improve") or {}).get("turns"), "judged": (run.get("improve") or {}).get("judged")}},
    }
