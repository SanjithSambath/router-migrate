#!/usr/bin/env python3
"""Eval v2 (MVP) on the 15 dev repos -> eval/scoreboard_v2.json, eval/SCOREBOARD_V2.md, eval/reports/<repo>/REPORT.html.

Per repo, on a fresh clone at the pinned commit (same recipes and envs as v1):
  1. v1 lossless pass (scan/plan/apply). Its migrated count must equal eval/scoreboard.json (v1 numbers must not change).
  2. recommend -> cards.
  3. "with unlocks and same-model improvements accepted": approve every unlock + same-model card (never model cards), run
     `apply --decisions`, then the repo's own tests/typecheck again with Router OFF (the default state); compare with v1's
     baseline. Unlocked sites are replayed through the vendored runtime against router-sim and (with --live) the live Router.
  4. REPORT.html (the dashboard frozen with its data).
Live spend is appended to eval/live_spend_v2.jsonl and capped (--max-usd, default 0.50; stop on 402/429).
"""
import argparse
import json
import os
import shutil
import sys
import time

EVAL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(EVAL)
sys.path.insert(0, ROOT)
sys.path.insert(0, EVAL)
import run_eval as RE  # noqa: E402
from router_migrate import cli, improve  # noqa: E402
from router_migrate.catalog import Catalog  # noqa: E402
from router_migrate.dashboard import render as dash_render  # noqa: E402
from router_migrate.sim.server import serve  # noqa: E402

SPEND = os.path.join(EVAL, "live_spend_v2.jsonl")
OUT_ROWS = os.path.join(EVAL, "logs_v2")
MIN_EVIDENCE = {"attr.metadata": "L0", "cache.key": "L1", "tier.flex_background": "L1", "fallback.candidates": "L0",
                "unlock.vendor_endpoint": "L0", "unlock.stated_loss": "L1",
                "model.deprecated": "L2", "model.newer_cheaper_same_family": "L2", "model.right_size": "L2"}


def spent_total():
    tot = 0.0
    if os.path.exists(SPEND):
        for l in open(SPEND):
            try:
                tot += float(json.loads(l).get("cost_usd") or 0)
            except ValueError:
                pass
    return round(tot, 6)


def bar_met(c):
    return int(c["evidence"]["level"][1]) >= int(MIN_EVIDENCE.get(c["rule"], "L9")[1])


def worse(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict) or "not_run" in before:
        return False
    if before.get("typecheck"):
        return (after.get("ts_errors") or 0) > (before.get("ts_errors") or 0)
    if after.get("passed") is None:
        return True
    return after["passed"] < (before.get("passed") or 0) or (after.get("failed", 0) + after.get("errors", 0)) > (before.get("failed", 0) + before.get("errors", 0))


def replay_unlocks(src, rec, base, key, live, budget):
    """Send each unlocked site's request shape through the vendored runtime with the card's option, to router-sim or live."""
    import httpx
    import importlib.util
    from router_migrate.runtime import render
    p = os.path.join(src, ".router-migrate", "_replay_runtime.py")
    open(p, "w").write(render("router_runtime.py", Catalog.load()))
    spec = importlib.util.spec_from_file_location("rm_v2_replay", p)
    rt = importlib.util.module_from_spec(spec)
    old = {k: os.environ.get(k) for k in ("RAMP_ROUTER_API_KEY", "RAMP_ROUTER_BASE_URL", "ROUTER_MIGRATE_FALLBACK")}
    os.environ.update(RAMP_ROUTER_API_KEY=key, RAMP_ROUTER_BASE_URL=base, ROUTER_MIGRATE_FALLBACK="0")
    spec.loader.exec_module(rt)
    out = []
    try:
        for c in rec["cards"]:
            if c["lever_group"] != "unlock":
                continue
            u = rec["signals"][c["scope"]["sites"][0]].get("unlock") or {}
            if c["rule"] == "unlock.vendor_endpoint":
                url = "https://%s/v1/chat/completions" % u["host"]
                body = {"model": c["rule_inputs"]["model"], "messages": [{"role": "user", "content": "Reply with exactly: pong"}], "max_tokens": 16}
                hdr = {"authorization": "Bearer vendor-key-not-sent"}
                opts = {"unlock_hosts": [u["host"]]}
            else:
                url = "https://api.anthropic.com/v1/messages"
                body = {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with exactly: pong"}]}
                hdr = {"x-api-key": "provider-key-not-sent", "anthropic-version": "2023-06-01", "anthropic-beta": ",".join(u["drop_betas"])}
                opts = {"drop_betas": u["drop_betas"]}

            class NoProvider(httpx.BaseTransport):
                def handle_request(self, req):
                    if req.url.host in (u.get("host"), "api.openai.com", "api.anthropic.com"):
                        return httpx.Response(599, json={"error": "would stay on the vendor"})
                    return httpx.HTTPTransport().handle_request(req)
            with httpx.Client(transport=rt.RouterTransport(inner=NoProvider(), opts=opts), timeout=120) as cl:
                r = cl.post(url, json=body, headers=hdr)
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            cost = ((j.get("usage") or {}).get("cost")) if isinstance(j, dict) else None
            row = {"card": c["id"], "rule": c["rule"], "status": r.status_code, "ok": r.status_code == 200, "model": (j or {}).get("model")}
            if live:
                with open(SPEND, "a") as f:
                    f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": "eval_v2_unlock_replay", "check": c["id"], "model": body["model"],
                                        "cost_usd": cost if cost is not None else 0.00005, "estimated": cost is None, "status": r.status_code}) + "\n")
                if r.status_code in (402, 429):
                    raise SystemExit("live %d: stopping" % r.status_code)
            out.append(row)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.remove(p)
    return out


def run_repo(repo, v1row, args, cat):
    name = repo["name"]
    rp = os.path.join(EVAL, "recipes", name + ".json")
    rec = json.load(open(rp)) if os.path.exists(rp) else {"name": name}
    rec["name"] = name
    t0 = time.time()
    src = RE.prepare(repo, rec)
    cli.do_scan(src, quiet=True)
    plan = cli.do_plan(src, quiet=True)
    cli.do_apply(src, quiet=True)
    lossless = plan["summary"]["migrated"]
    assert lossless == v1row["migrated"], "%s: v1 migrated changed %s != %s" % (name, lossless, v1row["migrated"])
    improve.event(src, "scan", "done")
    improve.event(src, "plan", "done", plan["summary"])
    improve.event(src, "apply", "done")
    r = improve.recommend(src, cat, repo=repo["repo"])
    for c in r["cards"]:
        if c["lever_group"] in ("unlock", "same_model"):
            improve.decide(src, c["id"], "approve", by="cli")
    log = improve.apply_decisions(src, cat)
    unl = improve.load(src, "unlocks.json", {"sites": {}})["sites"]
    runnable = bool(rec.get("test_cmd")) and (rec.get("baseline") or {}).get("exit") is not None and not args.no_tests
    tests_after = None
    if runnable:
        res = RE.tests(rec, src, "v2_after")
        tests_after = RE.count(res, rec.get("test_kind", "unit"))
    tc_before, tc_after = (v1row.get("typecheck") or {}).get("before_errors"), None
    if rec.get("typecheck_cmd") and not args.no_tests:
        tc = RE.typecheck(rec, src, "v2_typecheck_after")
        tc_after = tc["errors"] if tc else None
    tests_before = v1row.get("tests_before")
    worse_unlocks = worse(tests_before, tests_after) if tests_after is not None else False
    if tc_before is not None and tc_after is not None and tc_after > tc_before:
        worse_unlocks = True
    status_rep, status_ok = improve.status(src)
    srv, sim, base = serve()
    try:
        sim_unlock = replay_unlocks(src, r, base, "rm-sim-key-not-a-real-key", False, None)
    finally:
        srv.shutdown()
    live_unlock = []
    if args.live and os.environ.get("RAMP_ROUTER_API_KEY") and spent_total() < args.max_usd:
        live_unlock = replay_unlocks(src, r, "https://api.router.com", os.environ["RAMP_ROUTER_API_KEY"], True, args.max_usd)
    cards = r["cards"]
    groups = {}
    for c in cards:
        groups[c["lever_group"]] = groups.get(c["lever_group"], 0) + 1
    by_rule = {}
    for c in cards:
        by_rule[c["rule"]] = by_rule.get(c["rule"], 0) + 1
    owners = {sid: s["model_owner"] for sid, s in r["signals"].items()}
    model_non_owned = sum(1 for c in cards if c["lever_group"] == "model" and c["model_owner"] not in ("app_literal", "operator_config"))
    tests = {"before": tests_before, "after": tests_after}
    rep_dir = os.path.join(EVAL, "reports", name)
    os.makedirs(rep_dir, exist_ok=True)
    b = improve.bundle(src, repo=repo["repo"], live_spend_usd=spent_total(), tests=tests)
    open(os.path.join(rep_dir, "REPORT.html"), "w").write(dash_render.render_report(b))
    os.makedirs(OUT_ROWS, exist_ok=True)
    for f in ("recommendations.json", "decisions.json", "RECOMMENDATIONS.md", "unlocks.json"):
        if os.path.exists(improve.sp(src, f)):
            shutil.copy(improve.sp(src, f), os.path.join(OUT_ROWS, "%s.%s" % (name, f)))
    if os.path.exists(os.path.join(src, "router.policy.json")):
        shutil.copy(os.path.join(src, "router.policy.json"), os.path.join(OUT_ROWS, "%s.router.policy.json" % name))
    return {"repo": repo["repo"], "commit": repo["commit"][:10], "lossless_migrated": lossless, "unlock_candidates": groups.get("unlock", 0),
            "migrated_with_unlocks": lossless + len(unl), "unlocked_sites": sorted(unl), "tests_before": tests_before,
            "tests_after_unlocks": tests_after if tests_after is not None else {"not_run": (tests_before or {}).get("not_run", "no runnable tests")},
            "typecheck_errors": {"before": tc_before, "after_unlocks": tc_after}, "tests_worse_unlocks": worse_unlocks,
            "cards_total": len(cards), "cards_by_group": groups, "cards_by_rule": by_rule, "cards_bar_met": sum(1 for c in cards if bar_met(c)),
            "considered_not_recommended": len(r["considered_not_recommended"]), "model_cards_on_non_owned_sites": model_non_owned,
            "applied": [x["rule"] for x in log["applied"]], "policy_hash": log.get("policy_hash"), "policy_status_ok": status_ok,
            "same_model_safety_ok": (not worse_unlocks) and status_ok, "unlock_replay_sim": sim_unlock, "unlock_replay_live": live_unlock,
            "owners": {k: sum(1 for v in owners.values() if v == k) for k in sorted(set(owners.values()))},
            "report_html": os.path.relpath(os.path.join(rep_dir, "REPORT.html"), ROOT), "runtime_s": round(time.time() - t0, 1)}


def write_all(rows):
    v1 = json.load(open(os.path.join(EVAL, "scoreboard.json")))
    tot = {"repos": len(rows), "sites_found": v1["totals"]["sites_found"],
           "lossless_migrated": sum(r["lossless_migrated"] for r in rows), "migrated_with_unlocks": sum(r["migrated_with_unlocks"] for r in rows),
           "repos_tests_worse_lossless": v1["totals"]["repos_tests_worse"], "repos_tests_worse_unlocks": sum(1 for r in rows if r["tests_worse_unlocks"]),
           "cards_total": sum(r["cards_total"] for r in rows), "cards_bar_met": sum(r["cards_bar_met"] for r in rows),
           "precision_bar_met": None, "live_spend_usd": spent_total()}
    by_rule, by_group = {}, {}
    for r in rows:
        for k, v in r["cards_by_rule"].items():
            by_rule[k] = by_rule.get(k, 0) + v
        for k, v in r["cards_by_group"].items():
            by_group[k] = by_group.get(k, 0) + v
    tot["cards_by_rule"], tot["cards_by_group"] = by_rule, by_group
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M %Z"), "totals": tot, "repos": rows,
               "notes": {"precision_bar_met": "unmeasured: cards were not adjudicated against a blind rubric in the MVP",
                         "lossless_migrated": "v1 automatic pass, unchanged (equals eval/scoreboard.json)",
                         "migrated_with_unlocks": "lossless + sites moved by approved unlock cards (reported beside, never inside, the lossless number)",
                         "tests_after_unlocks": "the repo's own tests with Router OFF after approving every unlock + same-model card and running apply --decisions"}},
              open(os.path.join(EVAL, "scoreboard_v2.json"), "w"), indent=1, default=str)
    L = ["# router-migrate v2 scoreboard (MVP: migrate -> unlock -> improve)", "",
         "Generated %s by `eval/run_eval_v2.py` on the 15 pinned dev repos (the v1 set)." % time.strftime("%Y-%m-%d %H:%M"), "",
         "**Totals.**",
         "- **Lossless (v1, automatic):** %d of %d production sites." % (tot["lossless_migrated"], tot["sites_found"]),
         "- **With unlocks accepted:** %d. This is reported beside the lossless number, not inside it." % tot["migrated_with_unlocks"],
         "- **Tests or typecheck worse:** %d repos lossless, %d repos with every unlock and same-model card applied (Router off)." % (
             tot["repos_tests_worse_lossless"], tot["repos_tests_worse_unlocks"]),
         "- **Cards:** %d (%s); %d meet their lever's evidence bar. Card precision is unmeasured (no adjudication in the MVP)." % (
             tot["cards_total"], ", ".join("%s %d" % (k, v) for k, v in sorted(by_group.items())), tot["cards_bar_met"]),
         "- **Live spend for v2 (M0 + unlock replays):** $%.6f." % tot["live_spend_usd"], "",
         "Cards by lever: %s." % ", ".join("`%s` %d" % (k, v) for k, v in sorted(by_rule.items())), "",
         "| repo | lossless | unlock cards | with unlocks | tests before | tests after unlocks + policy | cards (unlock / same-model / model) | bar met | model cards on non-owned sites | policy status | report |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    tc = lambda t: "-" if not isinstance(t, dict) else ("n/a: %s" % t["not_run"] if "not_run" in t else ("tsc %s errors" % t.get("ts_errors") if t.get("typecheck") else "%s passed%s" % (t.get("passed"), (", %d failed" % (t.get("failed", 0) + t.get("errors", 0))) if (t.get("failed", 0) + t.get("errors", 0)) else "")))  # noqa: E731
    for r in rows:
        g = r["cards_by_group"]
        L.append("| %s | %d | %d | %d | %s | %s%s | %d / %d / %d | %d | %d | %s | [REPORT.html](reports/%s/REPORT.html) |" % (
            r["repo"], r["lossless_migrated"], r["unlock_candidates"], r["migrated_with_unlocks"], tc(r["tests_before"]), tc(r["tests_after_unlocks"]),
            (" (tsc %s -> %s)" % (r["typecheck_errors"]["before"], r["typecheck_errors"]["after_unlocks"])) if r["typecheck_errors"]["after_unlocks"] is not None else "",
            g.get("unlock", 0), g.get("same_model", 0), g.get("model", 0), r["cards_bar_met"], r["model_cards_on_non_owned_sites"],
            "ok" if r["policy_status_ok"] else "check", r["repo"].split("/")[1]))
    L += ["", "## Definitions", "",
          "- **lossless**: v1's automatic pass (unchanged from eval/SCOREBOARD.md).",
          "- **unlock cards**: `unlock.vendor_endpoint` (another vendor's endpoint, model is an exact Router catalog id) and `unlock.stated_loss` (only droppable Anthropic beta flags).",
          "- **with unlocks**: lossless + the sites those cards move once approved.",
          "- **tests after unlocks + policy**: every unlock and same-model card approved, `apply --decisions` run, tests and typecheck re-run with Router off.",
          "- **model cards** (`model.deprecated`, `model.newer_cheaper_same_family`, `model.right_size`): recommendations only, never applied; only for `app_literal` / `operator_config` model owners.",
          "- **bar met**: the card's evidence reaches its lever's minimum (SPEC_V2 levers table). Model cards need L2 replay, which the MVP does not build.",
          "- **policy status**: `router-migrate status` after apply (compiled hash current, no orphan entries).", ""]
    open(os.path.join(EVAL, "SCOREBOARD_V2.md"), "w").write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--max-usd", type=float, default=0.50)
    args = ap.parse_args()
    RE.SETDIR, RE.WORK = RE.SETS["dev"]
    RE.LOGS = os.path.join(EVAL, "logs_v2")
    os.makedirs(RE.LOGS, exist_ok=True)
    repos = json.load(open(os.path.join(EVAL, "repos.json")))
    v1 = {r["repo"]: r for r in json.load(open(os.path.join(EVAL, "scoreboard.json")))["repos"]}
    keep = set(args.only.split(",")) if args.only else None
    cat = Catalog.load()
    rows_p = os.path.join(EVAL, "logs_v2", "rows.json")
    rows = json.load(open(rows_p)) if os.path.exists(rows_p) else {}
    for repo in repos:
        if keep and repo["name"] not in keep:
            continue
        print("== %s" % repo["repo"], flush=True)
        row = run_repo(repo, v1[repo["repo"]], args, cat)
        rows = json.load(open(rows_p)) if os.path.exists(rows_p) else {}  # parallel lanes
        rows[repo["repo"]] = row
        json.dump(rows, open(rows_p, "w"), indent=1, default=str)
        print("   " + json.dumps({k: row[k] for k in ("lossless_migrated", "migrated_with_unlocks", "cards_total", "cards_by_group", "tests_after_unlocks", "tests_worse_unlocks", "policy_status_ok")}, default=str), flush=True)
    ordered = [rows[r["repo"]] for r in repos if r["repo"] in rows]
    write_all(ordered)


if __name__ == "__main__":
    main()
