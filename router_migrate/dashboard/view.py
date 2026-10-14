"""The dashboard's plain-language view of a run (stdlib only).

`build(bundle, root, floor_diff, post_diff)` answers, for a person who did not watch the run:
- calls: every production LLM call with one status from route truth (on Router and verified / wired but not verifiable /
  on Router with a trade-off / unlocked / reported moved but not routing / needs your decision / stays on its provider),
  the provider and model before and after, the agent's report on it, and the exact code line that changed;
- headline: the overview's numbers, counted from those same calls;
- improvements: every card as a plain sentence, who proposed it (the agent, with its rationale, or a rule default), the change it makes (code or router.policy.json), its effect on each
  request (a key-by-key diff of the request body), the agent assumptions it rests on, and its trade-offs;
- assumptions: every judgment and claim the agent made, in plain words, with the lines it read.
Decision state (approved, applied, ...) stays with the page, which already tracks it per card.
"""
import json
import os
import re

STATUS = {  # one status per production call, from route truth (what actually goes through Router), not from what the code looks like
    "moved": ("On Router", "Verified: the outside check saw this call go through Router."),
    "tradeoff": ("On Router, with a trade-off", "Moved at a stated cost. Undo puts it back as it was, on the next Apply."),
    "unverified": ("Wired, not verifiable", "Wired to Router, but no static check can see this call route (its client or model is set at run time)."),
    "unlocked": ("Unlocked in Improve", "Moved in Improve after you approved its unlock."),
    "not_routing": ("Reported moved, doesn't route", "The agent reported these as moved, but the outside check sees them stay on their provider."),
    "decide": ("Needs your decision", "The agent judged moving these a product decision. They stay where they are until you decide."),
    "left": ("Stays on its provider", "Router can't serve it as written, or it is not a production LLM call."),
}
STATUS_ORDER = ["moved", "tradeoff", "unverified", "unlocked", "not_routing", "decide", "left"]
ROUTED = ("moved", "tradeoff", "unverified", "unlocked")

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
    r = re.sub(r"^stays on its provider:\s*", "", str(reason or "").strip(), flags=re.I)
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


def _loc(v):
    """'a/b.py:12' -> ('a/b.py', 12); anything else -> (None, None)."""
    f, _, ln = str(v or "").rpartition(":")
    return (f, int(ln)) if f and ln.isdigit() else (None, None)


def _near_loc(f, line, locs, k=3):
    """The first FILE:LINE in locs on the same file within k lines of line, or None."""
    for x in locs or []:
        xf, xl = _loc(x.get("site") if isinstance(x, dict) else x)
        if xf == f and line is not None and abs(xl - int(line)) <= k:
            return x
    return None


def _value(v):
    """A judgment the agent reported: a bare value or {value, why}."""
    return v.get("value") if isinstance(v, dict) else v


LEVER_WORDS = {"drop_params": "drops %s", "drop_betas": "drops beta %s", "unlock_hosts": "%s served through Router",
               "operator_key_only": "operator's key only", "model_map": "%s"}


def levers_text(lv):
    """The levers a move took, in words: {"drop_params": ["seed"]} or ["drop_betas:x", "model_map:a=b", "wrap:f:1"] ->
    'drops beta x; a → b'. Wrapping the client is how every call moves, not a trade-off, so it is left out."""
    pairs = []
    if isinstance(lv, dict):
        for k, v in lv.items():
            if k == "model_map" and isinstance(v, dict):
                pairs += [(k, "%s → %s" % kv) for kv in sorted(v.items())]
            elif v:
                pairs.append((k, ", ".join(map(str, v)) if isinstance(v, list) else "" if v is True else str(v)))
    else:
        for x in lv or []:
            k, _, v = str(x).partition(":")
            pairs.append((k, " → ".join(v.split("=", 1)) if k == "model_map" else v))
    out = []
    for k, v in pairs:
        if k in ("wrap", "client"):
            continue
        w = LEVER_WORDS.get(k)
        out.append((w % v if "%s" in w else w) if w else ("%s %s" % (k, v)).strip())
    return "; ".join(out)


STAY_LABELS = [  # short reasons for the headline ("2 stay on their provider: no Router endpoint, local model")
    (r"R_NO_ENDPOINT|embedding|image|audio|speech|transcri|moderation|files api|vector", "no Router endpoint"),
    (r"localhost|127\.0\.0\.1|ollama|local (model|server)|lm ?studio|vllm", "local model"),
    (r"R_PER_TENANT|R_BYOK|per-tenant|own (provider )?key|each user", "users' own keys"),
    (r"R_OTHER_GATEWAY|gateway|litellm|openrouter", "another gateway"),
    (r"R_NOT_PRODUCTION|not a (production )?site|not production|\btests?\b|example|script", "not production traffic"),
    (r"R_DEAD_CODE|unused|dead code", "unused code"),
    (r"R_BETA|beta", "beta feature"),
    (r"R_STATEFUL|stored responses|stateful", "provider-side state"),
    (r"R_MODEL_NOT_IN_CATALOG|not in (the )?router catalog", "model not on Router"),
    (r"R_OTHER_VENDOR|R_OTHER_HOST|another vendor|vendor's endpoint|targets ", "another vendor's endpoint"),
    (r"R_RAW_REQUESTS|raw http|requests\.post|fetch\(", "raw HTTP call"),
]


def stay_label(reason):
    r = str(reason or "")
    return next((lab for pat, lab in STAY_LABELS if re.search(pat, r, re.I)), "other" if r else "no reason given")


def _result(b):
    """The outside check's result (ledger.json `result`), with older v5 field names mapped onto the current contract."""
    res = dict(((b.get("ledger") or {}).get("result")) or {})
    if "moved_verified" not in res and res.get("moved_sites") is not None:
        res["moved_verified"] = res.get("moved_sites")
    if "moved_unverified" not in res and res.get("claimed_unverified") is not None:
        res["moved_unverified"] = res.get("claimed_unverified")
    return res


def calls_view(b, plan_sites, floor_blocks, post_blocks, unlocks, floor_sites=None):
    """Every production LLM call with one status from route truth: plan_v3's outcome (migrated only when it actually routes,
    `verified` when a check saw it) and the outside check's result lists, with the agent's report alongside."""
    sig = (b.get("recommendations") or {}).get("signals") or {}
    claims = _claims_by_site((b.get("ledger") or {}).get("claims"))
    report = [c for c in (b.get("ledger") or {}).get("claims") or [] if c.get("origin") == "report"]
    worklist = [w for w in (b.get("ledger") or {}).get("worklist") or [] if w.get("type") == "site"]
    res = _result(b)
    trades = [t for t in b.get("tradeoffs") or [] if t.get("id")]
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
        rc = claim if claim and claim.get("origin") == "report" else _near_loc(s["file"], s.get("line"), [dict(c, site="%s:%s" % (c.get("file"), c.get("line"))) for c in report])
        unlocked = unl_feat.get(feature) or (s["id"] in unl_ids and {"card": unlocks[s["id"]].get("card")})
        mine = [t for t in trades if t.get("site") == "%s:%s" % (s["file"], s.get("line")) or
                (t.get("file") == s["file"] and abs(int(t.get("line") or 0) - int(s.get("line") or 0)) <= 3)]
        mine = mine or [t for t in trades if feature and t.get("feature") == feature]
        live_trades = [t for t in mine if not t.get("undone")]
        routes = bool(unlocked) or s.get("outcome") == "migrated"
        verified = s.get("verified")
        if verified is None and routes:  # no per-site flag (older plan): the result lists say, else the plan's own check
            verified = not _near_loc(s["file"], s.get("line"), res.get("moved_unverified"))
        stay = _near_loc(s["file"], s.get("line"), [x for x in res.get("stays") or [] if isinstance(x, dict)])
        claimed_moved = (rc or {}).get("outcome") == "migrated" or _near_loc(s["file"], s.get("line"), res.get("claimed_not_routing"))
        if unlocked:
            status = "unlocked"
        elif routes and live_trades:
            status = "tradeoff"
        elif routes:
            status = "moved" if verified else "unverified"
        elif claimed_moved:
            status = "not_routing"
        elif s.get("outcome") in ("flagged", "handoff") or (rc or {}).get("reason_code") == "needs_decision":
            status = "decide"
        else:
            status = "left"
        # who moved it: the rules (the floor plan, made before any agent ran, migrated it) or the agent
        by = None
        if routes:
            by = "improve" if unlocked else "rules" if (floor_moved is None or feature in floor_moved or s["id"] in floor_ids) else "agent"
        client_file = s["file"]
        cid = s.get("client_id") or (s.get("via_client") if isinstance(s.get("via_client"), str) else None)
        if isinstance(cid, str) and cid.count(":") >= 3:
            client_file = cid.split(":")[1]
        fb = _near(floor_blocks.get(client_file), s.get("line"))
        pb = _near(post_blocks.get(client_file), s.get("line"))
        code = []
        if not routes:
            fb, pb = [], []  # the client may be wrapped for its other calls, but this call doesn't route: no change to show
        for blk, who in [(x, "migrate") for x in fb] + [(x, "agent" if by == "agent" else "unlock" if status == "unlocked" else "after") for x in pb]:
            code.append({"file": client_file, "line": blk["line"], "before": _trim(blk["before"]), "after": _trim(blk["after"]), "by": who})
        model = _model(s, sg)
        why = STATUS[status][1]
        stay_reason = _plain((stay or {}).get("reason") or s.get("reason"), host)
        if status in ("moved", "tradeoff", "unverified") and by == "agent":
            w = next((x for x in worklist if x.get("file") == s["file"] and claim and int(x.get("line") or 0) == int(claim.get("line") or -1)), None)
            said = re.sub(r"^\w+:\s*", "", (w or {}).get("why") or "")
            if said:  # what the rules said before the agent moved it: the call a reviewer should check
                pl = _plain(said, host).rstrip(".")
                why = "The rules flagged it: %s. The agent read the code and moved it anyway. " % (pl[:1].lower() + pl[1:]) + why
        if status == "unverified" and s.get("reason") and re.search(r"run ?time|config|dynamic|env", str(s.get("reason")), re.I):
            why += " " + _plain(s.get("reason"), host).rstrip(".") + "."
        if status == "tradeoff":
            why = "Goes through Router at a stated cost: what it gives up is below%s." % (
                "" if verified else "; not verifiable statically, so the Router-on test run is the evidence")
        if status == "not_routing":
            why = "The agent reported this as moved, but it doesn't route: %s" % (
                (stay_reason.rstrip(".") + ".") if stay_reason else "the outside check sees it go to its provider.")
        if status == "decide":
            why = ((rc or claim or {}).get("why") or "").strip() or _plain(s.get("reason"), host) or why
        if status == "left":
            why = stay_reason or why
            if mine and all(t.get("undone") for t in mine):
                why = "Back on its provider: you undid its trade-off. " + why
        if status == "unlocked":
            c = next((x for x in cards if x["id"] == unlocked.get("card")), None) or pending_unlock.get(feature)
            why = "Moved in Improve, after you approved its unlock. %s" % (_sentence(c, feature, None, b)[1] if c else "")
        file_, fn = short(feature)
        ag = claim or rc
        out.append({
            "id": s["id"], "feature": feature, "file": s["file"], "line": s.get("line"), "fn": fn or "<module>", "fname": file_,
            "provider": prov, "host": host, "via": framework(s), "surface": s.get("surface"), "lang": s.get("lang"), "stream": bool(s.get("stream")),
            "status": status, "status_label": STATUS[status][0], "why": why, "reason": s.get("reason"), "by": by,
            "verified": bool(verified) if routes else None, "model": model, "routes": routes,
            "stay_label": stay_label((stay or {}).get("reason") or s.get("reason") or (ag or {}).get("reason_code")) if not routes else None,
            "agent": ({"outcome": ag.get("outcome"), "said": CLAIM.get(ag.get("outcome"), ag.get("outcome")), "reason_code": ag.get("reason_code"),
                       "why": ag.get("why") or "", "evidence": ag.get("evidence") or [], "confirmed": ag.get("confirmed"),
                       "tool_outcome": ag.get("tool_outcome"), "report": ag.get("origin") == "report",
                       "levers": levers_text(ag.get("levers")), "loses": ag.get("loses") if ag.get("levers") else None,
                       "latency": _value(ag.get("latency")), "task": _value(ag.get("task"))} if ag else None),
            "tradeoffs": [{"card": "tradeoff:" + t["id"], "levers": levers_text(t.get("levers")), "loses": t.get("loses") or "",
                           "why": t.get("why") or "", "undone": bool(t.get("undone")), "hand_edit": not levers_text(t.get("levers"))}
                          for t in mine],
            "unlock_card": (unlocked or {}).get("card") if unlocked else ((pending_unlock.get(feature) or {}).get("id")),
            "code": code})
    out += _unmatched(b, res, report, out)
    out.sort(key=lambda c: (STATUS_ORDER.index(c["status"]), c["file"], c["line"] or 0))
    return out


def _unmatched(b, res, report, calls):
    """Calls the outside check or the agent's report names that the plan doesn't list (a site it can't see): shown from the
    ledger alone, so the page never hides one the headline counts."""
    seen = [(c["file"], c["line"]) for c in calls]
    have = lambda loc: any(f == _loc(loc)[0] and abs(int(l or 0) - _loc(loc)[1]) <= 3 for f, l in seen)  # noqa: E731
    out = []

    def add(loc, status, why, ag=None, verified=None, label=None):
        f, ln = _loc(loc)
        if not f or have(loc):
            return
        seen.append((f, ln))
        out.append({"id": "ledger:%s:%d" % (f, ln), "feature": "%s:%d" % (f, ln), "file": f, "line": ln, "fn": "%s:%d" % (os.path.basename(f), ln),
                    "fname": os.path.basename(f), "provider": "Provider", "host": None, "via": None, "surface": None, "lang": None, "stream": False,
                    "status": status, "status_label": STATUS[status][0], "why": why, "reason": None, "by": "agent" if status in ROUTED else None,
                    "verified": verified, "model": {}, "routes": status in ROUTED, "stay_label": label, "synthetic": True,
                    "agent": ({"outcome": ag.get("outcome"), "said": CLAIM.get(ag.get("outcome"), ag.get("outcome")), "reason_code": ag.get("reason_code"),
                               "why": ag.get("why") or "", "evidence": ag.get("evidence") or [], "report": True,
                               "levers": levers_text(ag.get("levers")), "loses": ag.get("loses") if ag.get("levers") else None,
                               "latency": _value(ag.get("latency")), "task": _value(ag.get("task"))} if ag else None),
                    "tradeoffs": [], "unlock_card": None, "code": []})
    rep = lambda loc: _near_loc(_loc(loc)[0], _loc(loc)[1], [dict(c, site="%s:%s" % (c.get("file"), c.get("line"))) for c in report])  # noqa: E731
    for loc in res.get("moved_verified") or []:
        add(loc, "moved", STATUS["moved"][1], rep(loc), True)
    for loc in res.get("moved_unverified") or []:
        add(loc, "unverified", STATUS["unverified"][1], rep(loc), False)
    for loc in res.get("claimed_not_routing") or []:
        st = _near_loc(_loc(loc)[0], _loc(loc)[1], [x for x in res.get("stays") or [] if isinstance(x, dict)])
        add(loc, "not_routing", "The agent reported this as moved, but it doesn't route: %s" % (
            _plain((st or {}).get("reason"), None).rstrip(".") + "." if st else "the outside check sees it go to its provider."), rep(loc))
    for c in report:
        if c.get("reason_code") == "needs_decision":
            add("%s:%s" % (c.get("file"), c.get("line")), "decide", (c.get("why") or "").strip() or STATUS["decide"][1], c)
    for x in res.get("stays") or []:
        if isinstance(x, dict):
            add(x.get("site"), "left", _plain(x.get("reason"), None) or STATUS["left"][1], rep(x.get("site")), label=stay_label(x.get("reason")))
    return out


def headline(calls, b):
    """The overview's numbers, from the same calls the Migrate page lists (one source of truth)."""
    n = lambda st: sum(1 for c in calls if c["status"] == st)  # noqa: E731
    routed = [c for c in calls if c["routes"]]
    stays = [c for c in calls if c["status"] == "left"]
    reasons = {}
    for c in stays:
        reasons[c.get("stay_label") or "other"] = reasons.get(c.get("stay_label") or "other", 0) + 1
    return {"total": len(calls), "moved": len(routed), "verified": sum(1 for c in routed if c.get("verified")),
            "unverified": sum(1 for c in routed if not c.get("verified")), "with_tradeoffs": n("tradeoff"),
            "needs_decision": n("decide"), "not_routing": n("not_routing"), "stay": len(stays),
            "stay_reasons": [{"reason": k, "n": v} for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))],
            "source": "route_truth" if (b.get("ledger") or {}).get("result") else "plan"}


# ------------------------------------------------------------------------------------------------ improvements
def _q(v):
    return json.dumps(v)


def _pct(p):
    return ("−%g%%" % abs(p)) if p and p < 0 else ("+%g%%" % p if p else "")


def _impact(c):
    im = c.get("impact") or {}
    p = im.get("per_call_pct")
    if c["rule"].startswith("tradeoff."):
        return {"label": "Undone" if c.get("state") == "undone" else "", "tone": "neutral"}  # the card's badge says it is on Router
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
    if r.startswith("tradeoff."):
        return _tradeoff_request(ri.get("levers") or {}, m)
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


def _tradeoff_request(lv, m):
    """What a trade-off changed in each request (the levers the agent took)."""
    out = []
    for h in lv.get("unlock_hosts") or []:
        out += [{"op": "del", "key": "endpoint", "v": h}, {"op": "add", "key": "endpoint", "v": "api.router.com"}]
    if lv.get("drop_betas"):
        out.append({"op": "del", "key": "anthropic-beta", "v": ", ".join(lv["drop_betas"])})
    for old, new in sorted((lv.get("model_map") or {}).items()):
        out += [{"op": "del", "key": "model", "v": _q(old)}, {"op": "add", "key": "model", "v": _q(new)}]
    for k in lv.get("drop_params") or []:
        out.append({"op": "del", "key": k, "v": "(dropped)"})
    if lv.get("operator_key_only"):
        out.append({"op": "keep", "key": "api key", "v": "only the operator's key goes to Router; other keys stay on the provider"})
    return out


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
    if r.startswith("tradeoff."):
        ch = c["change"]
        out = [x for x in [_policy_change(c, f) if ch.get("kind") == "policy" else None] if x]
        file_, _, line = (((c.get("scope") or {}).get("sites") or [""])[0]).rpartition(":")
        file_, line = ch.get("file") or file_, ch.get("line") or (int(line) if line.isdigit() else 0)
        if ch.get("kind") == "code" or ch.get("wrap"):
            near = [b for b in _near(post_blocks.get(file_), line, 1) if abs(int(b["line"]) - int(line)) <= 20]
            out += [{"file": file_, "line": b["line"], "before": _trim(b["before"]), "after": _trim(b["after"]), "by": "agent"} for b in near]
            if not near:
                out.append({"file": file_, "line": line, "before": [], "after": ["# " + ch.get("diff", "")], "by": "agent"})
        return out
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
    if r.startswith("tradeoff."):
        site = ((c.get("scope") or {}).get("sites") or [f])[0]
        if c.get("state") == "undone":
            return c["title"], "Undone: %s is back on its provider as it was." % site
        if r == "tradeoff.edit":
            return c["title"], "%s goes through Router after a hand edit by the agent; revert that hunk to undo it." % site
        return c["title"], "%s goes through Router. The agent took the move and recorded what it costs; Undo reverses it." % site
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


def provenance(c):
    """Who proposed a card and whether the tool kept it: the migration agent (its rationale shown with the card) or a rule
    default. `validated` is True, or the reason the tool rejected the agent's proposal (shown, never approvable)."""
    ev = [r for r in ((c.get("evidence") or {}).get("results") or []) if r.get("level") == "agent"]
    origin = c.get("origin") or ("agent" if c["rule"].startswith("agent.") else "rule")
    rationale = c.get("rationale") or (ev[0].get("fact") if origin == "agent" and ev else None) or ""
    v = c.get("validated", True)
    im = c.get("impact") or {}
    return {"origin": origin, "rationale": rationale, "validated": v is True or v is None, "rejected": v if isinstance(v, str) and v else None,
            "monthly_usd": im.get("monthly_usd"), "price_note": im.get("ignores") if origin == "agent" else None}


def improvements_view(b, root, post_blocks):
    r = b.get("recommendations") or {}
    sig_by_feat = {}
    for s in (r.get("signals") or {}).values():
        sig_by_feat.setdefault(s.get("feature"), s)
    judgments = ((b.get("run") or {}).get("judgments")) or {}
    dec = (b.get("decisions") or {}).get("cards") or {}
    trades = {t.get("id"): t for t in b.get("tradeoffs") or []}
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
        out.append(dict({
            "id": c["id"], "rule": c["rule"], "lever": "tradeoff" if c["rule"].startswith("tradeoff.") else LEVER.get(c["rule"], "other"), "title": title, "summary": summary,
            "tool_title": c.get("title"), "features": feats, "fn": short(f)[1] if f else ("%d features" % len(feats) if feats else ""),
            "kind": "tradeoff" if c["rule"].startswith("tradeoff.")
            else "model" if c.get("lever_group") == "model" or c.get("applies") is False
            else "unlock" if c["rule"].startswith("unlock.") else "same",
            "impact": _impact(c), "request": _request(c, f, model, b),
            "code": _code_change(c, f, root, post_blocks, judgments, applied), "assumptions": assumptions,
            "facts": c.get("tool_facts") or [], "tradeoffs": trade, "conflicts_with": c.get("conflicts_with") or [],
            "conflict_note": c.get("conflict_note"), "suggested": c["rule"] == "tier.flex_background" and bool(c.get("conflicts_with")),
            "options": c.get("options") or []}, **provenance(c)))
        if c["rule"].startswith("tradeoff."):  # what moved, why the agent took it, and whether Undo can reverse it
            t = trades.get((c.get("rule_inputs") or {}).get("tradeoff")) or {}
            out[-1].update(why=t.get("why") or "", evidence=t.get("evidence") or [], site=t.get("site"), undone=c.get("state") == "undone",
                           hand_edit=c["rule"] == "tradeoff.edit")
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
        "counts": dict(counts, total=len(calls), routed=sum(counts[k] for k in ROUTED)), "headline": headline(calls, b),
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
