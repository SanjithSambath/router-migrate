"""v2 Improve phase plumbing: recommend -> decide -> apply --decisions -> status, the dashboard bundle and events.jsonl.

Files (CONTRACTS.md):
- in <repo>/.router-migrate/: recommendations.json, decisions.json, RECOMMENDATIONS.md (rendered one-way), events.jsonl,
  unlocks.json, REPORT.html;
- at the repo root: router.policy.json (committed);
- beside each vendored runtime: router_policy.py / routerPolicy.ts.

Nothing applies itself: only approved cards in decisions.json, with a current etag, are applied.
"""
import datetime
import json
import os
import re
import time

from . import apply_py, policy as pol, recommend as rec_mod

STATE = ".router-migrate"
ACTIONS = ("approve", "dismiss", "postpone", "shadow_first")


def sp(root, name):
    return os.path.join(root, STATE, name)


def load(root, name, default=None):
    p = sp(root, name)
    return json.load(open(p)) if os.path.exists(p) else default


def save(root, name, obj):
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    with open(sp(root, name), "w") as f:
        json.dump(obj, f, indent=1)


def event(root, stage, status, counts=None, msg="", cost_usd=0.0):
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    with open(sp(root, "events.jsonl"), "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "status": status, "counts": counts or {},
                            "cost_usd": cost_usd, "msg": msg}) + "\n")


# ------------------------------------------------------------------------------------------------ recommend
def recommend(root, catalog, repo=None):
    plan, scan = load(root, "plan.json"), load(root, "scan.json")
    if plan is None:
        raise SystemExit("no plan: run `router-migrate plan` (or `up`) first")
    r = rec_mod.build(root, plan, catalog, repo=repo, scan=scan)
    dec = load(root, "decisions.json", {"cards": {}})
    for c in r["cards"]:
        d = dec["cards"].get(c["id"])
        if d:
            c["state"] = {"approve": "approved", "dismiss": "dismissed", "postpone": "postponed", "shadow_first": "shadow_first"}[d["action"]]
            if d.get("etag") != c["etag"]:
                c["state"] = "stale"  # code or catalog row changed since the decision: re-confirm
    save(root, "recommendations.json", r)
    open(sp(root, "RECOMMENDATIONS.md"), "w").write(rec_mod.render_markdown(r, dec))
    event(root, "recommend", "done", {"cards": len(r["cards"]), "considered": len(r["considered_not_recommended"])})
    return r


def undecided(root):
    r, dec = load(root, "recommendations.json", {"cards": []}), load(root, "decisions.json", {"cards": {}})
    return [c for c in r["cards"] if c["id"] not in dec["cards"] or dec["cards"][c["id"]].get("etag") != c["etag"]]


# ------------------------------------------------------------------------------------------------ decide
def decide(root, card_id, action, reason=None, until=None, by="cli"):
    action = action.replace("-", "_")
    if action not in ACTIONS:
        raise ValueError("action must be one of approve, dismiss, postpone, shadow-first")
    if action == "dismiss" and not reason:
        raise ValueError("dismiss needs --reason")
    if action == "postpone":
        try:
            datetime.date.fromisoformat(until or "")
        except ValueError:
            raise ValueError("postpone needs --until YYYY-MM-DD")
    r = load(root, "recommendations.json")
    if r is None:
        raise ValueError("no recommendations: run `router-migrate recommend` first")
    card = next((c for c in r["cards"] if c["id"] == card_id), None)
    if card is None:
        raise KeyError("unknown card %s" % card_id)
    dec = load(root, "decisions.json", {"cards": {}})
    entry = {"action": action, "etag": card["etag"], "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if reason:
        entry["reason"] = reason
    if until:
        entry["until"] = until
    dec["cards"][card_id] = entry
    save(root, "decisions.json", dec)
    card["state"] = {"approve": "approved", "dismiss": "dismissed", "postpone": "postponed", "shadow_first": "shadow_first"}[action]
    save(root, "recommendations.json", r)
    open(sp(root, "RECOMMENDATIONS.md"), "w").write(rec_mod.render_markdown(r, dec))
    event(root, "decide", "done", {"card": card_id, "action": action})
    return entry


# ------------------------------------------------------------------------------------------------ apply --decisions
def _ts_policy_import(root, runtime_rel):
    """The side-effect import line appended to a vendored TS/JS runtime so every importer installs the compiled policy."""
    ext = os.path.splitext(runtime_rel)[1]
    if ext == ".ts":
        spec = "./routerPolicy"
        try:
            pkg = json.load(open(os.path.join(root, "package.json")))
            ts = open(os.path.join(root, "tsconfig.json")).read().lower()
            if pkg.get("type") == "module" and re.search(r'"module(resolution)?"\s*:\s*"(node16|nodenext)"', ts):
                spec = "./routerPolicy.js"
        except (OSError, ValueError):
            pass
        return 'import "%s"; // router-migrate: compiled policy (router.policy.json)\n' % spec, "routerPolicy.ts"
    if ext == ".mjs":
        return 'import "./routerPolicy.mjs"; // router-migrate: compiled policy (router.policy.json)\n', "routerPolicy.mjs"
    return 'require("./routerPolicy.js"); // router-migrate: compiled policy (router.policy.json)\n', "routerPolicy.js"


def _compile_everywhere(root, p, runtime_files):
    written = []
    for rt in runtime_files:
        d = os.path.dirname(os.path.join(root, rt))
        if rt.endswith(".py"):
            open(os.path.join(d, pol.PY_NAME), "w").write(pol.compile_py(p))
            written.append(os.path.relpath(os.path.join(d, pol.PY_NAME), root))
            continue
        line, name = _ts_policy_import(root, rt)
        src = pol.compile_ts(p)
        if not name.endswith(".ts"):  # plain JS: no TypeScript cast
            src = src.replace("(globalThis as any)", "globalThis").replace("export const", "const" if name.endswith(".js") and not name.endswith(".mjs") else "export const")
        open(os.path.join(d, name), "w").write(src)
        rtp = os.path.join(root, rt)
        body = open(rtp).read()
        if line.strip() not in body:
            open(rtp, "a").write("\n" + line)
        written.append(os.path.relpath(os.path.join(d, name), root))
    return written


def apply_decisions(root, catalog, dry_run=False):
    """Apply approved, non-stale cards. Returns a log {applied:[...], skipped:[...], policy_hash, files:[...]}."""
    r = load(root, "recommendations.json")
    dec = load(root, "decisions.json", {"cards": {}})
    if r is None:
        raise SystemExit("no recommendations: run `router-migrate recommend` first")
    applied, skipped, files = [], [], []
    p, err = pol.load(root)
    if err:
        raise SystemExit("router.policy.json: %s (fix or delete it; nothing applied)" % err)
    unlocks = load(root, "unlocks.json", {"sites": {}})
    scan = load(root, "scan.json", {"sites": []})
    by_id = {s["id"]: s for s in scan["sites"]}
    for c in r["cards"]:
        d = dec["cards"].get(c["id"])
        if not d or d["action"] != "approve":
            continue
        if d.get("etag") != c["etag"]:
            skipped.append({"card": c["id"], "reason": "stale: the code or catalog changed since approval; re-confirm"})
            continue
        if not c.get("applies", True):
            skipped.append({"card": c["id"], "reason": "model change: recommendation only, the tool never applies it"})
            continue
        if c["change"]["kind"] == "policy":
            patch = json.loads(c["change"]["diff"])
            for kind in ("defaults", "features", "globs"):
                if kind == "defaults" and "defaults" in patch:
                    p = pol.merge_entry(p, "defaults", patch["defaults"])
                elif kind in patch:
                    for name, entry in patch[kind].items():
                        p = pol.merge_entry(p, (kind, name), entry)
            applied.append({"card": c["id"], "rule": c["rule"], "kind": "policy"})
        elif c["rule"] in ("unlock.vendor_endpoint", "unlock.stated_loss"):
            sig = r["signals"][c["scope"]["sites"][0]]
            u = sig.get("unlock") or {}
            cl = by_id.get(u.get("client"))
            if cl is None or cl["lang"] != "py":
                skipped.append({"card": c["id"], "reason": "client construction not found (or not Python)"})
                continue
            kind = "wrap_openai" if c["rule"] == "unlock.vendor_endpoint" else "wrap_anthropic"
            opts = {"unlock_hosts": [u["host"]]} if kind == "wrap_openai" else {"drop_betas": u["drop_betas"]}
            if dry_run:
                applied.append({"card": c["id"], "rule": c["rule"], "kind": "code", "file": cl["file"], "dry_run": True})
                continue
            maps = rec_mod.line_maps(open(sp(root, "apply.diff")).read()) if os.path.exists(sp(root, "apply.diff")) else {}
            line = maps[cl["file"]](int(cl["line"])) if cl["file"] in maps else int(cl["line"])
            target = {(line, None): (kind, opts)} if cl["file"] in maps else {(line, cl["col"]): (kind, opts)}
            changed, done, sk, rt_rel = apply_py.patch_file(root, cl["file"], target, set())
            if sk:
                skipped.append({"card": c["id"], "reason": "; ".join(sk.values())})
                continue
            if rt_rel and not os.path.exists(os.path.join(root, rt_rel)):
                from .runtime import render
                open(os.path.join(root, rt_rel), "w").write(render("router_runtime.py", catalog))
            files.append(cl["file"])
            for sid in c["scope"]["sites"]:
                unlocks["sites"][sid] = {"card": c["id"], "rule": c["rule"]}
            applied.append({"card": c["id"], "rule": c["rule"], "kind": "code", "file": cl["file"]})
    log = {"applied": applied, "skipped": skipped, "policy_hash": None, "files": files}
    if p and not dry_run:
        if not pol.policy_valid(p):
            raise SystemExit("approved cards produced an invalid policy (bug); nothing written")
        open(os.path.join(root, pol.POLICY_FILE), "w").write(json.dumps(p, indent=1, sort_keys=True) + "\n")
        runtime_files = runtime_paths(root)
        log["files"] += [pol.POLICY_FILE] + _compile_everywhere(root, p, runtime_files)
        log["policy_hash"] = pol.policy_hash(p)
    if not dry_run:
        save(root, "unlocks.json", unlocks)
        event(root, "apply_decisions", "done", {"applied": len(applied), "skipped": len(skipped)})
    return log


def runtime_paths(root):
    """Vendored runtime files in the repo (from apply.json, plus any unlock-time copies)."""
    a = load(root, "apply.json", {}) or {}
    out = list(a.get("runtime_files") or [])
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("node_modules", ".git", STATE, ".venv", "venv")]
        for f in fs:
            if f in ("router_runtime.py", "routerRuntime.ts", "routerRuntime.mjs", "routerRuntime.js", "routerRuntime.cjs"):
                rel = os.path.relpath(os.path.join(d, f), root)
                if rel not in out and "router-migrate" in open(os.path.join(d, f), errors="replace").read(400):
                    out.append(rel)
    return out


# ------------------------------------------------------------------------------------------------ status
def status(root):
    """(report dict, ok bool). ok is False when a compiled policy is missing or stale, or a policy entry matches no site."""
    p, err = pol.load(root)
    r = load(root, "recommendations.json", {"cards": [], "signals": {}})
    feats = sorted({v["feature"] for v in (r.get("signals") or {}).values()})
    out = {"policy_file": os.path.exists(os.path.join(root, pol.POLICY_FILE)), "policy_error": err,
           "policy_hash": pol.policy_hash(p) if p else None, "compiled": [], "orphans": [], "features": {}}
    ok = err is None
    if p:
        for rt in runtime_paths(root):
            d = os.path.dirname(os.path.join(root, rt))
            name = pol.PY_NAME if rt.endswith(".py") else _ts_policy_import(root, rt)[1]
            h = pol.compiled_hash(os.path.join(d, name))
            out["compiled"].append({"file": os.path.relpath(os.path.join(d, name), root), "hash": h, "current": h == out["policy_hash"]})
            ok = ok and h == out["policy_hash"]
        for name in (p.get("features") or {}):
            if name not in feats:
                out["orphans"].append({"features": name})
        for g in (p.get("globs") or {}):
            if not any(pol._rt._glob_re(g).match(f) for f in feats):
                out["orphans"].append({"globs": g})
        ok = ok and not out["orphans"]
        for f in feats:
            eff = pol.policy_for(f, p)
            if eff:
                out["features"][f] = eff
    dec = load(root, "decisions.json", {"cards": {}})
    out["cards"] = {"total": len(r["cards"]), "undecided": len(undecided(root)),
                    "approved": sum(1 for d in dec["cards"].values() if d["action"] == "approve")}
    return out, ok


# ------------------------------------------------------------------------------------------------ dashboard bundle
SETUP = [
    {"id": "setup.budget", "title": "Set a per-key spend cap", "why": "Router enforces caps per API key (401 when hit); set one before rollout.",
     "values": {"page": "API keys", "cap": "your monthly limit"}, "link": "https://app.router.com/", "done": False},
    {"id": "setup.flex_default", "title": "Decide the account Flex default", "why": "Flex is on by default (allow_flex_tier_default=true, measured) and served "
     "requests at up to ~61 s. router-migrate sends service_tier \"default\" on every routed call unless a policy opts a feature into Flex.",
     "values": {"allow_flex_tier_default": "keep on (tool overrides per call) or off"}, "link": "https://app.router.com/strategies", "done": False},
    {"id": "setup.jev", "title": "Jev auto routing (optional, unmeasured)", "why": "Jev picks among 2-6 models you describe per routing profile. Not measured: "
     "the docs say allow_flex_tier opts a request out of Jev; whether service_tier \"default\" does is unverified.",
     "values": {"routing profile": "2-6 described models", "status on this account": "off"}, "link": "https://app.router.com/strategies", "done": False},
    {"id": "setup.switchyard", "title": "Switchyard (experimental, coding agents only)", "why": "model:\"switchyard\" works from app code once a strategy is "
     "saved (measured: served gpt-5.6-luna at flex); it switches model family mid-conversation. The tool never writes it.",
     "values": {"efficient_model": "openai:gpt-5.6-luna", "capable_model": "anthropic:claude-sonnet-4-5"}, "link": "https://app.router.com/strategies", "done": False},
    {"id": "setup.shadow", "title": "Shadow a candidate model before any model change", "why": "Shadow models record cost and latency for 1-3 "
     "candidates on a sample of /v1/responses traffic (needs content recording; not on BYOK keys). Results are only on the Shadow Logs page.",
     "values": {"candidates": "1-3", "sample": "5-10%"}, "link": "https://app.router.com/strategies", "done": False},
]


def bundle(root, repo=None, live_spend_usd=0.0, tests=None):
    plan = load(root, "plan.json", {"sites": [], "summary": {}})
    r = load(root, "recommendations.json", {"cards": [], "considered_not_recommended": [], "signals": {}})
    dec = load(root, "decisions.json", {"cards": {}})
    unlocks = load(root, "unlocks.json", {"sites": {}})
    diff = open(sp(root, "apply.diff")).read() if os.path.exists(sp(root, "apply.diff")) else ""
    prod = [s for s in plan["sites"] if s.get("prod")]
    sm = plan.get("summary") or {}
    unsupported_surfaces = ("embeddings", "images", "audio", "files", "batches", "vector_stores", "assistants", "moderations", "completions.legacy", "models")
    unsup = [{"site": s["id"], "file": s["file"], "line": s["line"], "surface": s["surface"], "reason": s["reason"]}
             for s in prod if s["outcome"] == "kept" and any(s["surface"].endswith(u) or s["surface"].startswith(u) for u in unsupported_surfaces)]
    stages = []
    for line in open(sp(root, "events.jsonl")).read().splitlines() if os.path.exists(sp(root, "events.jsonl")) else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        stages = [x for x in stages if x["stage"] != e["stage"]] + [{"stage": e["stage"], "status": e["status"], "counts": e.get("counts") or {}, "msg": e.get("msg", "")}]
    sites = []
    for s in prod:
        x = {k: s.get(k) for k in ("id", "file", "line", "lang", "sdk", "surface", "outcome", "reason", "stream")}
        sg = (r.get("signals") or {}).get(s["id"]) or {}
        x.update(feature=sg.get("feature"), latency_class=sg.get("latency_class"), model_owner=sg.get("model_owner"), resolved_model=sg.get("resolved_model"),
                 unlocked=s["id"] in unlocks["sites"], chips=[])
        sites.append(x)
    status_report, ok = status(root)
    return {"repo": repo or plan.get("repo") or os.path.basename(os.path.abspath(root)), "root": os.path.abspath(root),
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool_version": "router-migrate v2-mvp",
            "stages": stages, "summary": sm,
            "funnel": {"all_gen_ai": len(prod), "router_servable": sm.get("router_eligible", 0), "moved": sm.get("migrated", 0),
                       "needs_review": sm.get("flagged", 0) + sm.get("handoff", 0), "kept": sm.get("kept", 0)},
            "kpis": {"lossless_migrated": sm.get("migrated", 0), "migrated_with_unlocks": sm.get("migrated", 0) + len(unlocks["sites"]),
                     "unlock_candidates": sum(1 for c in r["cards"] if c["lever_group"] == "unlock"),
                     "cards_total": len(r["cards"]), "live_spend_usd": live_spend_usd, "tests_before": (tests or {}).get("before"), "tests_after": (tests or {}).get("after")},
            "sites": sites, "diff": diff, "recommendations": r, "decisions": dec, "setup": SETUP, "unsupported": unsup,
            "policy": {"exists": status_report["policy_file"], "hash": status_report["policy_hash"],
                       "compiled_hash": (status_report["compiled"][0]["hash"] if status_report["compiled"] else None), "orphans": status_report["orphans"]}}
