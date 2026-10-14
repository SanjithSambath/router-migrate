"""router-migrate: move a codebase's production LLM calls onto Ramp's Router, without breaking it.

In Claude Code (any model): /router-migrate, or
  router-migrate start  [PATH]        set up (map every LLM call, baseline in the background); print the agent's instructions
  ...the session migrates with its own tools and the commands below, and writes .router-migrate/MIGRATION.json...
  router-migrate finish [PATH]        the outside check, improvement cards, REPORT.html and the dashboard (prints its URL)

Headless (CI, evals):
  router-migrate run PATH --agent claude [--model M]   start -> a headless agent session -> finish

Agent tools (run from the repository root; FILE:LINE accepts a comma-separated list):
  router-migrate check --all | FILE:LINE    does each call route to Router now, why not, and the fix
  router-migrate check --run "CMD"          run CMD against a local Router simulator; which calls sent valid requests
  router-migrate check --final [--tests]    dry run of the outside check
  router-migrate levers [FILE:LINE]         model, Flex tier, price, and what the rules suggest or reject per migrated call
  router-migrate wrap | unwrap FILE:LINE    exact codemods at a client construction
  router-migrate adapt FILE:LINE --LEVER --loses "..."   take a trade-off (shown with an undo); adapt --undo T<n>

Review and apply (or use the dashboard):
  router-migrate dashboard [PATH]           REPORT.html + the live dashboard
  router-migrate decide CARD approve|dismiss|postpone|shadow-first
  router-migrate apply PATH --decisions     apply approved cards (policy compile / codemod options / trade-off undo)
  router-migrate status [PATH]              effective policy per feature, hash check, undecided cards

Nothing changes at runtime until RAMP_ROUTER_API_KEY is set; ROUTER_MIGRATE_DISABLE=1 turns it off again.
Earlier pipelines (scan, plan, verify, report, up, recommend, probe, claim, card, improve, judge) still run; see archive/README.md.
"""
import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__
from .catalog import Catalog
from . import plan as planmod
from . import scan_py
from .runtime import render

HERE = os.path.dirname(os.path.abspath(__file__))
TS_TOOL = os.path.join(HERE, "ts", "tool.mjs")
STATE = ".router-migrate"


def state_path(root, name):
    d = os.path.join(root, STATE)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def save(root, name, obj):
    with open(state_path(root, name), "w") as f:
        json.dump(obj, f, indent=2, default=str)


def load(root, name):
    p = state_path(root, name)
    return json.load(open(p)) if os.path.exists(p) else None


def node_ok():
    return shutil.which("node") is not None and os.path.exists(TS_TOOL)


def run_ts(args, timeout=600):
    r = subprocess.run(["node", TS_TOOL] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ts tool failed: %s" % (r.stderr.strip()[-2000:] or r.stdout[-500:]))
    return json.loads(r.stdout)


# ------------------------------------------------------------------------------------------------ scan
def do_scan(root, quiet=False):
    t = time.time()
    res = scan_py.scan(root)
    ts = {"files_scanned": 0, "sites": [], "errors": []}
    if node_ok():
        try:
            ts = run_ts(["scan", root])
        except Exception as e:
            ts["errors"] = [{"file": "*", "error": "ts scan failed: %s" % e}]
    else:
        ts["errors"] = [{"file": "*", "error": "node or the TS tool is unavailable: TS/JS files were not scanned"}]
    out = {"root": os.path.abspath(root), "tool_version": __version__, "files_scanned": {"py": res["files_scanned"], "ts": ts.get("files_scanned", 0)},
           "sites": res["sites"] + ts.get("sites", []), "errors": res["errors"] + ts.get("errors", []), "seconds": round(time.time() - t, 2)}
    save(root, "scan.json", out)
    if not quiet:
        kinds = {}
        for s in out["sites"]:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        print("scanned %d Python + %d TS/JS files in %.1fs: %s" % (out["files_scanned"]["py"], out["files_scanned"]["ts"], out["seconds"],
                                                                  ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items()))))
        for e in out["errors"][:10]:
            print("  scan error: %s: %s" % (e["file"], e["error"]))
    return out


# ------------------------------------------------------------------------------------------------ plan
def get_catalog(live):
    key = os.environ.get("RAMP_ROUTER_API_KEY")
    return Catalog.load(live=bool(live and key), api_key=key)


def do_plan(root, live=False, quiet=False, include_nonprod=False):
    scan = load(root, "scan.json") or do_scan(root, quiet=True)
    cat = get_catalog(live)
    p = planmod.build(scan, cat, include_nonprod=include_nonprod)
    save(root, "plan.json", p)
    if not quiet:
        print_summary(p["summary"], "plan")
        print("catalog: %s" % cat.source)
    return p


def print_summary(sm, label):
    pct = lambda x: "-" if x is None else "%.0f%%" % (100 * x)
    print("%s: %d production LLM sites -> %d migrated, %d handoff, %d kept, %d flagged | call-site coverage %s, Router-eligible coverage %s" % (
        label, sm["sites_found"], sm["migrated"], sm["handoff"], sm["kept"], sm["flagged"], pct(sm["callsite_coverage"]), pct(sm["weighted_coverage"])))


# ------------------------------------------------------------------------------------------------ apply
def do_apply(root, live=False, dry_run=False, quiet=False):
    from . import apply_py
    p = load(root, "plan.json") or do_plan(root, live=live, quiet=True)
    cat = get_catalog(live)
    py_rt = render("router_runtime.py", cat)
    ts_rt = render("routerRuntime.ts", cat)
    work = root
    tmp = None
    if dry_run:
        tmp = tempfile.mkdtemp(prefix="router-migrate-dry-")
        work = os.path.join(tmp, "repo")
        shutil.copytree(root, work, ignore=shutil.ignore_patterns("node_modules", ".git", ".venv", "venv", STATE, "__pycache__", ".next"))
    log = {"edits": [], "runtime_files": [], "files_changed": [], "skipped": []}
    by_file = {}
    for pt in p["patches"]:
        if pt["lang"] == "py":
            by_file.setdefault(pt["file"], {})[(pt["line"], pt["col"])] = (pt["patch"], pt.get("opts"))
    installs = {}
    for ins in p["installs"]:
        installs.setdefault(ins["file"], set()).add(ins["kind"])
    originals = {}
    for f in sorted(set(by_file) | set(installs)):
        originals[f] = open(os.path.join(work, f), encoding="utf-8").read()
        try:
            changed, done, skipped, rt_rel = apply_py.patch_file(work, f, by_file.get(f, {}), installs.get(f, set()))
        except Exception as e:
            log["skipped"].append({"file": f, "reason": "libcst failed: %s" % e})
            continue
        for (line, col), kind in done.items():
            log["edits"].append({"lang": "py", "file": f, "line": line, "col": col, "action": kind, "ok": True})
        for (line, col), why in skipped.items():
            log["skipped"].append({"lang": "py", "file": f, "line": line, "col": col, "reason": why})
        if changed:
            log["files_changed"].append(f)
        if rt_rel:
            rtp = os.path.join(work, rt_rel)
            if not os.path.exists(rtp) or open(rtp).read() != py_rt:
                open(rtp, "w").write(py_rt)
            if rt_rel not in log["runtime_files"]:
                log["runtime_files"].append(rt_rel)
    ts_ids = [pt["site_id"] for pt in p["patches"] if pt["lang"] == "ts"]
    if ts_ids and node_ok():
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
            rf.write(ts_rt)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
            json.dump(ts_ids, sf)
        for f in {pt["file"] for pt in p["patches"] if pt["lang"] == "ts"}:
            fp = os.path.join(work, f)
            if os.path.exists(fp):
                originals[f] = open(fp, encoding="utf-8").read()
        try:
            r = run_ts(["apply", work, "--runtime", rf.name, "--sites", sf.name])
            for e in r.get("edits", []):
                (log["edits"] if e.get("ok") else log["skipped"]).append(dict(e, lang="ts", reason=e.get("reason")))
            log["files_changed"] += [f for f in r.get("files_changed", []) if f not in log["files_changed"]]
            log["runtime_files"] += r.get("runtime_files", [])
        except Exception as e:
            log["skipped"].append({"lang": "ts", "file": "*", "reason": str(e)})
    patched_ids = {e.get("site_id") for e in log["edits"] if e.get("site_id")} | {
        pt["site_id"] for pt in p["patches"] for e in log["edits"] if e["lang"] == "py" and e["file"] == pt["file"] and e["line"] == pt["line"] and e["col"] == pt["col"]}
    log["patched_site_ids"] = sorted(x for x in patched_ids if x)
    # a site whose client patch failed is not migrated: downgrade honestly
    why = {}
    for pt in p["patches"]:
        for sk in log["skipped"]:
            if sk.get("site_id") == pt["site_id"] or (sk.get("file") == pt["file"] and sk.get("line") == pt["line"] and sk.get("col") == pt["col"]):
                why[pt["site_id"]] = sk.get("reason")
    p = reconcile(p, set(log["patched_site_ids"]), {pt["site_id"] for pt in p["patches"]}, why)
    diff = []
    for f in sorted(originals):
        new = open(os.path.join(work, f), encoding="utf-8").read()
        diff += difflib.unified_diff(originals[f].splitlines(True), new.splitlines(True), "a/" + f, "b/" + f)
    log["diff_lines"] = sum(1 for l in diff if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---")))
    write_handoff(root, p)
    if dry_run:
        sys.stdout.write("".join(diff))
        shutil.rmtree(tmp, ignore_errors=True)
        return log
    open(state_path(root, "apply.diff"), "w").write("".join(diff))
    save(root, "apply.json", log)
    save(root, "plan.json", p)
    if not quiet:
        print("apply: %d edits in %d files, runtime at %s, %d skipped; diff: %d lines (%s)" % (
            len(log["edits"]), len(log["files_changed"]), ", ".join(log["runtime_files"]) or "-", len(log["skipped"]), log["diff_lines"],
            os.path.join(STATE, "apply.diff")))
        for s in log["skipped"][:10]:
            print("  skipped %s:%s %s" % (s.get("file"), s.get("line", ""), s.get("reason")))
        print_summary(p["summary"], "result")
    return log


def reconcile(p, patched_ids, attempted, why=None):
    failed = attempted - patched_ids
    if not failed:
        return p
    why = why or {}
    for s in p["sites"]:
        cid = s["id"] if s["kind"] == "client" and s["id"] in failed else s.get("via_client")
        if s["outcome"] == "migrated" and cid in failed:
            s["outcome"], s["reason"] = "flagged", "client construction left unchanged: %s" % why.get(cid, "patch could not be applied safely")
    p["summary"] = planmod.summarize(p["sites"])
    return p


def write_handoff(root, p):
    tasks = [s for s in p["sites"] if s["outcome"] in ("handoff", "flagged") and s["prod"]]
    spec = [{"id": s["id"], "file": s["file"], "line": s["line"], "sdk": s["sdk"], "surface": s["surface"], "outcome": s["outcome"],
             "reason": s["reason"], "snippet": s.get("snippet"), "acceptance": acceptance(s)} for s in tasks]
    save(root, "handoff.json", spec)
    lines = ["# HANDOFF: tasks for a coding agent", "",
             "router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,",
             "then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.", "",
             "Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog",
             "model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.", ""]
    if not spec:
        lines.append("Nothing to hand off.")
    for i, t in enumerate(spec, 1):
        lines += ["## %d. %s:%d (%s %s) - %s" % (i, t["file"], t["line"], t["sdk"], t["surface"], t["outcome"]), "",
                  "`%s`" % (t["snippet"] or "").replace("`", "'"), "", "- Why: %s" % t["reason"], "- Done when: %s" % t["acceptance"], ""]
    open(state_path(root, "HANDOFF.md"), "w").write("\n".join(lines))


def acceptance(s):
    if s["sdk"] in ("google-genai", "mistral", "cohere", "groq", "ollama", "other"):
        return "the call goes through an OpenAI SDK client wrapped with router_runtime (or routerFetch) using the Router catalog id, and the repo's tests still pass"
    if s["kind"] == "raw_http":
        return "the request is sent with the OpenAI/Anthropic SDK through the runtime, or to Router directly with a User-Agent header"
    if "not in the Router catalog" in s["reason"]:
        return "either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only"
    return "the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report"


# ------------------------------------------------------------------------------------------------ verify / report
def do_verify(root, args):
    from .verify import verify
    return verify(root, args)


def do_report(root, quiet=False):
    from .report import report
    return report(root, quiet=quiet)


LEGACY_HELP = {"scan": "find LLM clients, call sites, raw HTTP and env usage (Python + TS/JS)",
               "plan": "bucket every site with exact reasons; dry-run diff",
               "apply": "--decisions: apply approved cards (legacy: vendor the runtime and patch every client)",
               "verify": "tests before vs after, syntax/type checks, requests replayed against router-sim",
               "report": "REPORT.md + report.json", "run": "start -> agent -> finish (--agent claude [--model M])"}
HIDDEN = {"scan", "plan", "verify", "report", "up", "recommend", "probe", "claim", "card", "improve", "judge", "_baseline", "_fulltest"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="router-migrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="router-migrate " + __version__)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("scan", "plan", "apply", "verify", "report", "run"):
        sp = sub.add_parser(name, help=LEGACY_HELP[name])
        sp.add_argument("path", nargs="?", default=".")
        sp.add_argument("--live", action="store_true", help="use the live catalog / replay against the real Router (needs RAMP_ROUTER_API_KEY)")
        sp.add_argument("--json", action="store_true", help="print the JSON result")
        if name in ("apply", "plan", "run"):
            sp.add_argument("--dry-run", action="store_true", help="print the unified diff without changing files")
            sp.add_argument("--include-nonprod", action="store_true", help="also patch clients in tests/examples/evals")
        if name in ("verify", "run"):
            sp.add_argument("--test-cmd", help="test command to run before/after (default: auto-detect)")
            sp.add_argument("--baseline", help="path to an unmodified copy of the repo for the 'before' test run (default: git stash)")
            sp.add_argument("--budget", type=float, default=0.25, help="USD cap for --live replay (default 0.25)")
            sp.add_argument("--timeout", type=int, default=1800)
        if name == "apply":
            sp.add_argument("--decisions", action="store_true", help="apply approved cards from .router-migrate/decisions.json")
        if name == "run":
            sp.add_argument("--skip-verify", action="store_true", help="skip the baseline test runs (finish then has no test gate)")
            sp.add_argument("--typecheck-cmd", help="typecheck command; its error count is the baseline finish compares against")
            sp.add_argument("--force", action="store_true", help="re-apply the floor and reset the ledger's floor snapshot")
            sp.add_argument("--no-floor", action="store_true", help="v4 default (kept for older scripts): nothing is applied")
            sp.add_argument("--agent", choices=["claude"], help="run a headless Claude Code session as the migration agent (start -> agent -> finish)")
            sp.add_argument("--arm", choices=["min", "free"], help="eval ablation: min (default) = with the outside check; free = without it")
            sp.add_argument("--model", help="agent model (default: the agent CLI's default)")
            sp.add_argument("--agent-timeout", type=int, default=2700, help="seconds before the agent run is stopped")
            sp.add_argument("--skip-improve", action="store_true", help="stop after the migration gate (no Improve phase)")
            sp.add_argument("--no-dashboard", action="store_true", help="write REPORT.html but don't serve the live dashboard")
            sp.add_argument("--no-browser", action="store_true", help="serve the dashboard without opening a browser")
    s_up = sub.add_parser("up", help="scan -> plan -> apply (lossless) -> verify -> recommend -> report, then the local dashboard")
    s_up.add_argument("path", nargs="?", default=".")
    s_up.add_argument("--agent", action="store_true", help="no dashboard; print pending cards as JSON; exit 2 while any card is undecided")
    s_up.add_argument("--no-browser", action="store_true", help="serve the dashboard without opening a browser")
    s_up.add_argument("--skip-verify", action="store_true", help="skip running the repo's tests")
    s_up.add_argument("--test-cmd")
    s_up.add_argument("--live", action="store_true")
    s_up.add_argument("--max-usd", type=float, default=0.25)
    s_rec = sub.add_parser("recommend", help="write .router-migrate/recommendations.json and RECOMMENDATIONS.md")
    s_rec.add_argument("path", nargs="?", default=".")
    s_rec.add_argument("--json", action="store_true")
    s_dec = sub.add_parser("decide", help="record a decision on a card (approve | dismiss | postpone | shadow-first)")
    s_dec.add_argument("card_id")
    s_dec.add_argument("action", choices=["approve", "dismiss", "postpone", "shadow-first"])
    s_dec.add_argument("--reason")
    s_dec.add_argument("--until", help="YYYY-MM-DD (postpone)")
    s_dec.add_argument("--path", default=".")
    s_dec.add_argument("--by", default="cli", choices=["cli", "agent", "ui"])
    from . import agent as agentmod
    agentmod.add_parsers(sub)
    s_st = sub.add_parser("status", help="effective setup per feature, policy hash check, orphans; exit 1 if stale or orphaned")
    s_st.add_argument("path", nargs="?", default=".")
    s_st.add_argument("--json", action="store_true")
    # the v5 surface, in the order a user meets it; earlier pipelines stay runnable but unadvertised
    import re as _re
    order = ["start", "finish", "run", "check", "levers", "wrap", "unwrap", "adapt", "dashboard", "decide", "apply", "status"]
    acts = {x.dest: x for x in sub._choices_actions}
    for x in acts.values():
        x.help = _re.sub(r"^v\d( improve)?: ", "", x.help or "")
    sub._choices_actions = [acts[n] for n in order if n in acts]
    sub.metavar = "{%s}" % ",".join(order)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    if a.cmd in ("check", "wrap", "unwrap", "adapt", "probe", "claim", "card", "finish", "run", "judge", "improve", "start", "levers", "dashboard", "_baseline", "_fulltest"):
        from . import agent as agentmod
        if a.cmd != "run" or not getattr(a, "dry_run", False):
            return agentmod.dispatch(a)
    if a.cmd in ("up", "recommend", "decide", "status"):
        from . import v2cli
        return v2cli.run(a)
    root = os.path.abspath(a.path)
    if a.cmd == "apply" and getattr(a, "decisions", False):
        from . import v2cli
        return v2cli.run(a)
    if a.cmd == "scan":
        r = do_scan(root)
    elif a.cmd == "plan":
        if getattr(a, "dry_run", False):
            do_plan(root, live=a.live, quiet=True, include_nonprod=a.include_nonprod)
            r = do_apply(root, live=a.live, dry_run=True, quiet=True)
        else:
            r = do_plan(root, live=a.live, include_nonprod=a.include_nonprod)
    elif a.cmd == "apply":
        do_scan(root, quiet=True)
        do_plan(root, live=a.live, quiet=True, include_nonprod=a.include_nonprod)
        r = do_apply(root, live=a.live, dry_run=a.dry_run)
    elif a.cmd == "verify":
        r = do_verify(root, a)
    elif a.cmd == "report":
        r = do_report(root)
    else:
        do_scan(root)
        do_plan(root, live=a.live, include_nonprod=a.include_nonprod)
        do_apply(root, live=a.live, dry_run=a.dry_run)
        do_verify(root, a)
        r = do_report(root)
    if a.json and r is not None:
        print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
