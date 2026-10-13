#!/usr/bin/env python3
"""Eval: run router-migrate on the 15 pinned repos and measure coverage and behavior preservation.

Per repo (fresh clone at the pinned commit in eval/work/runs/<name>):
  1. tests before (Router off)               4. tests after (Router off: the default state after migrating)
  2. router-migrate scan/plan/apply (timed)  5. the repo's tests again with Router ON against router-sim (real code paths, validated)
  3. typecheck before/after (TS repos)       6. replay of every unique migrated request shape (site's literal kwargs) at router-sim (+ --live)
Writes eval/scoreboard.json, eval/SCOREBOARD.md, eval/scoreboard.html and per-repo logs in eval/logs/.
Recipes (env setup + test commands, with measured baselines) live in eval/recipes/<name>.json.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from router_migrate import cli, verify as V  # noqa: E402
from router_migrate.catalog import Catalog  # noqa: E402
from router_migrate.sim.server import serve  # noqa: E402

EVAL = os.path.join(ROOT, "eval")
SETDIR = EVAL  # repos.json, recipes/, logs/ and the scoreboard of the repo set being measured (--set)
WORK = os.path.join(EVAL, "work")
LOGS = os.path.join(EVAL, "logs")
SETS = {"dev": (EVAL, os.path.join(EVAL, "work")),
        "heldout": (os.path.join(EVAL, "heldout"), os.path.join(EVAL, "work", "heldout"))}  # held-out: never used to tune the tool


def contain(e):
    """Keep evaluated repos' setup and tests from touching anything outside their work dirs: git discovery stops at WORK
    (so a repo's tests can never see router-migrate's own .git), and browser downloads are skipped."""
    e.setdefault("GIT_CEILING_DIRECTORIES", WORK)
    for k, v in (("PUPPETEER_SKIP_DOWNLOAD", "true"), ("PUPPETEER_SKIP_CHROMIUM_DOWNLOAD", "true"), ("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1"),
                 ("HUSKY", "0")):
        e.setdefault(k, v)
    return e


def sh(cmd, cwd=ROOT, env=None, timeout=1800):
    e = dict(os.environ)
    e.pop("RAMP_ROUTER_API_KEY", None)
    e.update(env or {})
    contain(e)
    r = subprocess.run(cmd, cwd=cwd, shell=True, env=e, capture_output=True, text=True, timeout=timeout, executable="/bin/bash")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def subst(s, rec, src):
    env_dir = os.path.join(WORK, "envs", rec["name"])
    return (s.replace("$PY", os.path.join(env_dir, "bin", "python")).replace("$ENV", env_dir).replace("$SRC", src).replace("$RM", ROOT))


def prepare(repo, rec):
    """Fresh clone of the pristine checkout + dependencies reused from the recipe's prepared env (eval/work/base, envs)."""
    name = repo["name"]
    src = os.path.join(WORK, "runs", name)
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.dirname(src), exist_ok=True)
    code, out = sh("git clone -q %s %s && git -C %s checkout -q %s" % (shlex.quote(os.path.join(WORK, "pristine", name)), shlex.quote(src),
                                                                       shlex.quote(src), repo["commit"]))
    if code:
        raise RuntimeError("clone failed: " + out[-500:])
    base = os.path.join(WORK, "base", name)
    if rec.get("lang") == "ts":
        # node_modules from the prepared base copy (APFS clone: instant, no extra disk)
        for d, dirs, _ in os.walk(base):
            if "node_modules" in dirs:
                rel = os.path.relpath(os.path.join(d, "node_modules"), base)
                dst = os.path.join(src, rel)
                if not os.path.exists(dst):
                    sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(os.path.join(base, rel)), shlex.quote(dst)) * 2))
                dirs.remove("node_modules")
            dirs[:] = [x for x in dirs if x not in (".git", ".next")]
        for extra in rec.get("copy_from_base", []):
            if os.path.exists(os.path.join(base, extra)):
                sh("cp -cR %s %s" % (shlex.quote(os.path.join(base, extra)), shlex.quote(os.path.join(src, extra))))
    else:
        # point editable installs at this run's copy so 'after' tests import the migrated code
        cmds = rec.get("reinstall") or [c + " --no-deps" for c in rec.get("setup", []) if " -e " in c and "$SRC" in c]
        for c in cmds:
            code, out = sh(subst(c, rec, src), timeout=900)
            if code:
                raise RuntimeError("editable reinstall failed: %s" % out[-400:])
    return src


def tests(rec, src, label, env=None):
    cmd = subst(rec["test_cmd"], rec, src)
    e = {k: subst(str(v), rec, src) for k, v in (rec.get("test_env") or {}).items()}
    e.update(env or {})
    env_dir = os.path.join(WORK, "envs", rec["name"])
    for k, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "home/.config"), ("XDG_CACHE_HOME", "home/.cache"), ("XDG_DATA_HOME", "home/.local/share")):
        os.makedirs(os.path.join(env_dir, sub), exist_ok=True)
        e.setdefault(k, os.path.join(env_dir, sub))  # the recipe's own HOME (set inside test_cmd) still wins
    contain(e)
    res = V.run_tests(cmd, src, env=e, timeout=rec.get("timeout_s", 1800))
    open(os.path.join(LOGS, "%s.%s.log" % (rec["name"], label)), "w").write(res["tail"])
    return res


def typecheck(rec, src, label):
    if not rec.get("typecheck_cmd"):
        return None
    code, out = sh("cd %s && %s" % (shlex.quote(src), subst(rec["typecheck_cmd"], rec, src)), timeout=900)
    open(os.path.join(LOGS, "%s.%s.log" % (rec["name"], label)), "w").write(out[-20000:])
    return {"exit": code, "errors": out.count("error TS")}


def sim_tests(rec, src):
    srv, sim, base = serve()
    try:
        res = tests(rec, src, "sim", env={"RAMP_ROUTER_API_KEY": V.SIM_KEY, "RAMP_ROUTER_BASE_URL": base})
        recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
        res.update(requests=len(recs), valid=sum(1 for x in recs if x.get("valid")),
                   invalid=[{"path": x["path"], "error": x.get("error"), "model": x.get("model"), "feature": x.get("feature")} for x in recs if not x.get("valid")][:20])
        return res
    finally:
        srv.shutdown()


def count(t, kind="unit"):
    if t and kind == "typecheck":
        return {"typecheck": True, "exit": t.get("exit"), "ts_errors": t.get("ts_errors")}
    if not t or t.get("passed") is None:
        return None
    return {"passed": t["passed"], "failed": (t.get("failed") or 0), "errors": (t.get("errors") or 0), "skipped": t.get("skipped") or 0, "exit": t.get("exit")}


def run_repo(repo, args, cat):
    name = repo["name"]
    rp = os.path.join(SETDIR, "recipes", name + ".json")
    rec = json.load(open(rp)) if os.path.exists(rp) else {"name": name}
    rec["name"] = name
    row = {"repo": repo["repo"], "commit": repo["commit"][:10], "lang": rec.get("lang"), "test_cmd": rec.get("test_cmd"),
           "test_kind": rec.get("test_kind", "unit")}
    src = prepare(repo, rec)
    runnable = bool(rec.get("test_cmd")) and (rec.get("baseline") or {}).get("exit") is not None and not args.no_tests
    if runnable:
        row["_tests_before"] = tests(rec, src, "before")
    row["_typecheck_before"] = typecheck(rec, src, "typecheck_before") if not args.no_tests else None
    t = time.time()
    cli.do_scan(src, quiet=True)
    cli.do_plan(src, quiet=True)
    applied = cli.do_apply(src, quiet=True)
    row["runtime_s"] = round(time.time() - t, 1)
    plan = cli.load(src, "plan.json")
    if runnable:
        row["_tests_after"] = tests(rec, src, "after")
        row["_sim_tests"] = sim_tests(rec, src)
    row["_typecheck_after"] = typecheck(rec, src, "typecheck_after") if not args.no_tests else None
    srv, sim, base = serve()
    try:
        rep = V.replay(plan, base, V.SIM_KEY, cat, tag="sim-" + name)
    finally:
        srv.shutdown()
    row["_replay"] = rep
    if args.live:
        key = os.environ.get("RAMP_ROUTER_API_KEY")
        row["_live"] = V.replay(plan, "https://api.router.com", key, Catalog.load(live=True, api_key=key), budget=args.live_budget_per_repo,
                                tag="live-" + name) if key else {"skipped": "no key"}
    sm = plan["summary"]
    row.update(sites_found=sm["sites_found"], migrated=sm["migrated"], handoff=sm["handoff"], kept=sm["kept"], flagged=sm["flagged"],
               router_eligible=sm["router_eligible"], callsite_coverage=sm["callsite_coverage"], weighted_coverage=sm["weighted_coverage"],
               diff_lines=applied.get("diff_lines"), files_changed=len(applied.get("files_changed", [])), nonprod_sites=sm["nonprod_sites"])
    kind = rec.get("test_kind", "unit")
    row["tests_before"] = count(row.get("_tests_before"), kind) or {"not_run": (rec.get("baseline") or {}).get("reason") or "no runnable tests"}
    row["tests_after"] = count(row.get("_tests_after"), kind) or {"not_run": row["tests_before"].get("not_run", "-")}
    st = row.get("_sim_tests") or {}
    row["tests_router_on"] = count(st, kind) if st else {"not_run": row["tests_before"].get("not_run", "-")}
    # sim_valid_pct: unique request shapes (surface, stream, model, the site's literal kwargs) valid at router-sim / unique shapes
    # replayed. The Router-on test run is reported separately: most suites mock the SDK or HTTP, so it sends 0 requests (H5).
    uniq = [r for r in rep["results"] if not r.get("reused") and r.get("ok") is not None]
    row["replay_unique_requests"] = rep.get("unique_requests", len(uniq))
    row["replay_ok"] = rep.get("ok", sum(1 for r in uniq if r["ok"]))
    row["sim_valid_pct"] = round(100.0 * row["replay_ok"] / row["replay_unique_requests"], 1) if row["replay_unique_requests"] else None
    row["sim_requests_from_tests"] = st.get("requests", 0) if st else None
    row["sim_detail"] = {"repo_test_requests": st.get("requests", 0), "repo_test_valid": st.get("valid", 0),
                         "replayed_sites": sum(1 for r in rep["results"] if r.get("ok") is not None),
                         "replay_unique_requests": row["replay_unique_requests"], "replay_ok": row["replay_ok"],
                         "invalid": st.get("invalid", []) + [r for r in uniq if not r["ok"]][:10]}
    lv = row.get("_live")
    if lv and lv.get("results") is not None:
        lr = [r for r in lv["results"] if r.get("ok") is not None]
        row["live_success_pct"] = round(100.0 * sum(1 for r in lr if r["ok"]) / len(lr), 1) if lr else None
        row["live_spent_usd"] = lv.get("spent_usd")
    else:
        row["live_success_pct"] = None
    tb, ta = row.get("_typecheck_before"), row.get("_typecheck_after")
    if tb and ta:
        row["typecheck"] = {"before_errors": tb["errors"], "after_errors": ta["errors"], "before_exit": tb["exit"], "after_exit": ta["exit"]}
    row["outcomes_by_sdk"] = {}
    for s in plan["sites"]:
        if s["prod"]:
            k = "%s/%s" % (s["sdk"], s["surface"])
            row["outcomes_by_sdk"].setdefault(k, {}).setdefault(s["outcome"], 0)
            row["outcomes_by_sdk"][k][s["outcome"]] += 1
    json.dump({k: v for k, v in row.items()}, open(os.path.join(LOGS, name + ".row.json"), "w"), indent=1, default=str)
    shutil.copy(os.path.join(src, ".router-migrate", "apply.diff"), os.path.join(LOGS, name + ".diff"))
    shutil.copy(os.path.join(src, ".router-migrate", "plan.json"), os.path.join(LOGS, name + ".plan.json"))
    shutil.copy(os.path.join(src, ".router-migrate", "HANDOFF.md"), os.path.join(LOGS, name + ".HANDOFF.md"))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated repo names")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--live", action="store_true", help="also replay migrated sites against api.router.com (needs RAMP_ROUTER_API_KEY)")
    ap.add_argument("--live-budget-per-repo", type=float, default=0.05)
    ap.add_argument("--set", choices=sorted(SETS), default="dev", help="repo set: dev (eval/) or heldout (eval/heldout/)")
    args = ap.parse_args()
    global SETDIR, WORK, LOGS
    SETDIR, WORK = SETS[args.set]
    LOGS = os.path.join(SETDIR, "logs")
    os.makedirs(LOGS, exist_ok=True)
    repos = json.load(open(os.path.join(SETDIR, "repos.json")))
    if args.only:
        keep = set(args.only.split(","))
        repos = [r for r in repos if r["name"] in keep]
    cat = Catalog.load()
    prev = {}
    sp = os.path.join(SETDIR, "scoreboard.json")
    if os.path.exists(sp):
        for r in json.load(open(sp)).get("repos", []):
            prev[r["repo"]] = r
    for repo in repos:
        t = time.time()
        print("== %s" % repo["repo"], flush=True)
        try:
            row = run_repo(repo, args, cat)
        except Exception as e:
            row = {"repo": repo["repo"], "commit": repo["commit"][:10], "error": "%s: %s" % (type(e).__name__, e)}
        row["eval_seconds"] = round(time.time() - t, 1)
        if os.path.exists(sp):  # other eval processes (parallel --only lanes) may have written rows meanwhile
            for r in json.load(open(sp)).get("repos", []):
                if r["repo"] != repo["repo"]:
                    prev[r["repo"]] = r
        prev[repo["repo"]] = {k: v for k, v in row.items() if not k.startswith("_")}
        print("   %s" % json.dumps({k: prev[repo["repo"]].get(k) for k in ("sites_found", "migrated", "callsite_coverage", "weighted_coverage",
                                                                         "tests_before", "tests_after", "sim_valid_pct", "replay_unique_requests",
                                                                         "sim_requests_from_tests", "error")}), flush=True)
        write_scoreboard([prev[r["repo"]] for r in json.load(open(os.path.join(SETDIR, "repos.json"))) if r["repo"] in prev])


def write_scoreboard(rows):
    from scoreboard import write_all
    write_all(rows, SETDIR)


if __name__ == "__main__":
    sys.path.insert(0, EVAL)
    main()
