"""The migrate agent's improvement proposals (MIGRATION.json `improvements`) -> recommendation cards.

The agent knows what each call is for; the code knows what Router and the catalog can do. A proposal that matches a rule's
card enriches it (origin "agent", the agent's rationale and losses); one the rules didn't make becomes its own card after
validation against the catalog (a Flex tier exists, fallbacks are active catalog models, a model swap names a catalog model).
A proposal that fails validation is listed under "considered, not recommended" with the reason. Nothing applies until a
human approves the card; model swaps and code ports are never applied by the tool."""
import hashlib
import json

from . import recommend as R

LEVER_RULES = {"flex": ("tier.flex_background",), "fallbacks": ("fallback.candidates",), "cache_key": ("cache.key",),
               "model": ("model.deprecated", "model.newer_cheaper_same_family", "model.right_size")}


def _as_list(x):
    if not x:
        return []
    return [x] if isinstance(x, str) else [str(v) for v in x]


def _sites_for(rec, feature, site):
    ids = [sid for sid, sg in rec["signals"].items() if sg["feature"] == feature]
    return ids or ["agent:" + site]


def _model_for(rec, feature):
    return next((sg.get("resolved_model") for sg in rec["signals"].values() if sg["feature"] == feature and sg.get("resolved_model")), None)


def _new_card(p, rule, group, title, change, loses, applies, rec):
    feat = p["feature"]
    etag = hashlib.sha1(json.dumps([p["site"], p.get("change"), p.get("lever")], sort_keys=True, default=str).encode()).hexdigest()[:12]
    return {"id": "rec_agent_%s_%s" % (rule.split(".")[-1], hashlib.sha1(("%s|%s" % (feat, json.dumps(change, sort_keys=True))).encode()).hexdigest()[:8]),
            "rule": rule, "lever_group": group, "title": title,
            "scope": {"sites": _sites_for(rec, feat, p["site"]), "features": [feat], "latency_class": None}, "model_owner": None,
            "change": change, "loses": loses, "rule_inputs": {"proposal": {k: p.get(k) for k in ("lever", "change")}},
            "evidence": {"level": "agent", "results": [{"level": "agent", "fact": p.get("why") or "", "source": p["site"]}]},
            "impact": {"per_call_pct": None, "monthly_usd": None, "ignores": p.get("gains") or "proposed by the migration agent; unmeasured"},
            "risk": 2, "effort": "low", "options": [], "etag": etag, "state": "new", "applies": applies,
            "origin": "agent", "rationale": p.get("why") or "", "gains": p.get("gains") or "", "validated": True}


def _validate(p, rec, catalog):
    """(card or None, reason-if-rejected)."""
    lever, ch, feat = p.get("lever"), p.get("change") if isinstance(p.get("change"), dict) else {}, p["feature"]
    loses = _as_list(p.get("loses"))
    mid = _model_for(rec, feat)
    if lever == "flex":
        if not mid:
            return None, "the model is chosen at runtime; Flex eligibility is unknown"
        if not R._has_flex(mid):
            return None, "%s has no Flex tier" % mid
        return _new_card(p, "agent.flex", "same_model", "Run %s on Flex (half price, slower)" % feat,
                         {"kind": "policy", "diff": json.dumps(R.policy_patch(feat, {"allow_flex_tier": True}))},
                         loses or ["Flex adds latency (up to ~61 s measured)"], True, rec), None
    if lever == "fallbacks":
        ids = _as_list(ch.get("fallbacks") or ch.get("models"))
        bad = [m for m in ids if (catalog.by_id.get(m) or {}).get("status", "active") != "active" or m not in catalog.by_id]
        if not ids or bad:
            return None, "fallbacks must be active Router catalog ids (%s)" % (", ".join(bad) if bad else "none given")
        return _new_card(p, "agent.fallbacks", "same_model", "Fail over %s to %s on outages" % (feat, " then ".join(ids)),
                         {"kind": "policy", "diff": json.dumps(R.policy_patch(feat, {"fallbacks": ids}))},
                         loses or ["the fallback model answers when the primary is down", "Flex is not used on requests that carry fallbacks"], True, rec), None
    if lever == "cache_key":
        return _new_card(p, "agent.cache_key", "same_model", "Cache key for %s" % feat,
                         {"kind": "policy", "diff": json.dumps(R.policy_patch(feat, {"prompt_cache_key": "$feature"}))}, loses, True, rec), None
    if lever == "model":
        to = ch.get("model") or ch.get("to")
        rid = catalog.resolve(to)[0] if to else None
        if not rid:
            return None, "%r is not a Router catalog model" % to
        return _new_card(p, "agent.model", "model", "Try %s for %s (shadow first)" % (rid, feat),
                         {"kind": "code", "diff": "change the model for %s from %s to %s" % (feat, mid or "the current model", rid)},
                         loses or ["a different model answers every request: shadow it first and compare outputs"], False, rec), None
    title = {"vendor_port": "Port %s to a Router-served vendor/SDK" % feat}.get(lever, "Proposal for %s" % feat)
    return _new_card(p, "agent." + (lever or "other"), "decision", title,
                     {"kind": "note", "diff": json.dumps(ch) if ch else (p.get("gains") or "see rationale")}, loses, False, rec), None


def _same_change(p, card, catalog):
    """A model proposal matches a model card only when both name the same model; other levers match by lever + feature."""
    if p.get("lever") != "model":
        return True
    ch = p.get("change") if isinstance(p.get("change"), dict) else {}
    to = ch.get("model") or ch.get("to")
    rid = catalog.resolve(to)[0] if to else None
    ins = card.get("rule_inputs") or {}
    return bool(rid) and rid in (ins.get("candidate"), ins.get("replacement"))


def merge(rec, proposals, catalog):
    """Cards in `rec` (recommend.build output) gain origin/rationale; agent proposals are matched to them or added."""
    for c in rec["cards"]:
        c.setdefault("origin", "rule")
        c.setdefault("validated", True)
    for p in proposals or []:
        if not p.get("feature") or not p.get("site"):
            continue
        rules = LEVER_RULES.get(p.get("lever"), ())
        hit = next((c for c in rec["cards"] if c["rule"] in rules and p["feature"] in c["scope"]["features"] and _same_change(p, c, catalog)), None)
        if hit:  # the rules got there too: the agent's rationale and losses go on the same card
            hit["origin"] = "agent+rule"
            hit["rationale"] = p.get("why") or ""
            hit["gains"] = p.get("gains") or ""
            hit["loses"] = list(hit.get("loses") or []) + [x for x in _as_list(p.get("loses")) if x not in (hit.get("loses") or [])]
            continue
        card, reason = _validate(p, rec, catalog)
        if card:
            if not any(c["id"] == card["id"] for c in rec["cards"]):
                rec["cards"].append(card)
        else:
            rec["considered_not_recommended"].append({"rule": "agent." + str(p.get("lever")), "scope": {"features": [p["feature"]], "sites": [p["site"]]},
                                                      "reason": "proposed by the migration agent; not valid: %s" % reason, "origin": "agent"})
    return rec
