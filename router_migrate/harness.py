"""v6 harness: a light layer around an agent that does the work.

    start   clean tree, branch router-migrate/migration, baseline in the background, live catalog when a migration key
            exists, the scanner's map; prints the migrate + improve instructions (Claude Code: the session is the agent)
    finish  the outside check once, on the migration branch and every improvement branch; validation.json; the dashboard.
            Never reverts anything: a failure blocks Apply and the facts go back to the agent
    apply   the human's requests (apply / undo) handed to an agent session, which decides how to carry them out;
            `apply --done` validates its APPLY.json, re-checks, and logs the session
    run     headless: start -> a `claude -p` session -> finish (one fix pass with the facts if finish fails)

Contracts: router_migrate/schemas/V6_CONTRACTS.md. The agent's tools: check, levers, wrap, unwrap, adapt."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import agent as AG
from . import report_v6 as R

AGENT_TOOLS = "Read,Grep,Glob,Edit,MultiEdit,Write,TodoWrite,Task,Bash,WebFetch,WebSearch"
AGENT_COMMANDS = ("check", "levers", "wrap", "unwrap", "adapt", "request")
DUMMY_KEYS = {"OPENAI_API_KEY": "sk-dummy-not-a-real-key", "ANTHROPIC_API_KEY": "sk-ant-dummy-not-a-real-key"}
BACKGROUND_TESTS_SECS = 90  # a suite slower than this is checked in the background after finish; Apply waits for it
MIGRATION_BRANCH = R.MIGRATION_BRANCH
DEP_FILES = ("package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
             "go.mod") + AG.LOCKFILES
DEP_DIRS = ("node_modules", ".venv", "venv", "env")  # linked into branch worktrees so their tests can run
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$")
PROMPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


# ------------------------------------------------------------------------------------------------ git
def _g(root, *args):
    r = AG._git(root, *args)
    return r.stdout.strip() if r.returncode == 0 else None


def _branch(root):
    return _g(root, "rev-parse", "--abbrev-ref", "HEAD")


def _dirty(root):
    """Tracked files with staged or unstaged changes (the state dir excluded). Untracked files don't count."""
    r = AG._git(root, "status", "--porcelain", "--untracked-files=no")
    return [l[3:] for l in r.stdout.splitlines() if l.strip() and not l[3:].startswith(R.STATE)]


def _exclude(root):
    """Keep the state dir (and dependency dirs linked into branch worktrees) out of git without touching .gitignore."""
    common = _g(root, "rev-parse", "--git-common-dir")
    if not common:
        return
    p = os.path.join(root if not os.path.isabs(common) else "", common, "info", "exclude")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    have = open(p).read().splitlines() if os.path.exists(p) else []
    want = ["/%s/" % R.STATE] + ["/%s" % d for d in DEP_DIRS]
    add = [w for w in want if w not in have]
    if add:
        with open(p, "a") as f:
            f.write("\n# router-migrate\n" + "\n".join(add) + "\n")


# ------------------------------------------------------------------------------------------------ the truth tool
def check_sites(root, locs=None):
    """[{file, line, sdk, surface, routes, reason, ...}] for the current tree (route_check: the real runtime's decision)."""
    from .route_check import check_sites as cs
    rows = cs(root)
    if locs:
        rows = [r for r in rows if any(r["file"] == f and abs(int(r["line"]) - int(l)) <= 3 for f, l in locs)]
    return rows


def _fmt(rows, limit=200):
    lines = []
    for r in rows[:limit]:
        why = (r.get("reason") or "").replace("client is constructed outside this repo or not resolvable",
                                                "call not linked to a client the scanner found (it may use a shared or imported client)")
        lines.append("%s:%s  %s %s  %s" % (r["file"], r["line"], r.get("sdk") or "", r.get("surface") or "",
                                           "ROUTES" if r.get("routes") else "stays: %s" % why[:180]))
    if len(rows) > limit:
        lines.append("... %d more (run `router-migrate check --all`)" % (len(rows) - limit))
    return "\n".join(lines)


def _new_ledger(root, base, base_tree, baseline):
    return {"version": 6, "root": root, "base": base, "base_tree": base_tree, "branch": MIGRATION_BRANCH,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": bool(R.migration_key()), "baseline": baseline,
            "probes": {}, "live_probes": [], "confirmations": {}, "claims": {}, "cost": {}, "sessions": [],
            # fields the optional tools (wrap, unwrap, adapt) keep their records in
            "floor": {"tree": base_tree, "migrated": 0, "applied": False}, "edits": [], "tradeoffs": [], "cards": [],
            "worklist": [], "proposals": []}


def _ledger(root):
    led = AG.load_ledger(root)
    return led if led and led.get("version") == 6 else None


def cmd_check(a):
    """check --all | FILE:LINE[,...] | --run CMD [--live] | --final [--tests]."""
    if getattr(a, "final", False):
        return cmd_verify(a)
    root = AG.ledger_root(a.path)
    if getattr(a, "run", None):
        if getattr(a, "live", False):
            return probe_live(root, a)
        a.probe_cmd, a.timeout = a.run, getattr(a, "timeout", AG.PROBE_TIMEOUT)
        if AG.load_ledger(root) is None:
            AG.save_ledger(root, _new_ledger(root, None, None, {}))
        return AG.cmd_probe(a)
    locs = AG._locs(a.loc) if a.loc else None
    if a.loc and locs is None:
        return AG.err(a.json, "expected FILE:LINE[,FILE:LINE...], got %r" % a.loc, "e.g. `router-migrate check app/llm.py:42`")
    rows = check_sites(root, locs)
    n = sum(1 for r in rows if r.get("routes"))
    return AG.out({"ok": True, "sites": rows, "routes": n}, a.json,
                  "%d of %d LLM call(s) the scanner sees route to Router now (the scanner misses dynamically built clients)\n%s"
                  % (n, len(rows), _fmt(rows)))


def probe_live(root, a):
    """Run a command against the real Router with the migration key (through a recording pass-through). The key is only
    in the command's environment; output is scrubbed of it."""
    from . import live_proxy
    key = R.migration_key()
    if not key:
        return AG.err(a.json, "no migration key", "put one in ~/.config/router-migrate/key or ROUTER_MIGRATE_KEY; without "
                                                  "it use `check --run` (the simulator) and record live checks as not-run")
    led = AG.load_ledger(root) or {}
    srv, base, recs = live_proxy.serve()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAMP_ROUTER")}
    env.update({"RAMP_ROUTER_API_KEY": key, "RAMP_ROUTER_BASE_URL": base, "ROUTER_MIGRATE_FEATURE_TAG": "on",
                "ROUTER_MIGRATE_DISABLE": "", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    py = (led.get("baseline") or {}).get("python")
    if py and os.path.exists(py):
        env["PATH"] = os.path.dirname(py) + os.pathsep + env.get("PATH", "")
    t = time.time()
    try:
        r = subprocess.run(AG.test_env_prefix(led) + a.run, shell=True, cwd=root, env=env, capture_output=True, text=True,
                           timeout=getattr(a, "timeout", None) or 600)
        o, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        o, code = "TIMEOUT", 124
    finally:
        srv.shutdown()
    o = o.replace(key, "[migration key]")
    ok = [x for x in recs if (x.get("status") or 0) < 400]
    rec = {"cmd": a.run, "exit": code, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t, 1),
           "requests": recs[:50]}
    led = AG.load_ledger(root)
    if led is not None:
        led.setdefault("live_probes", []).append(rec)
        led.setdefault("cost", {})["live_requests"] = (led["cost"].get("live_requests") or 0) + len(recs)
        AG.save_ledger(root, led)
    lines = ["live: exit %s, %d request(s) to Router (%d ok, %d error)" % (code, len(recs), len(ok), len(recs) - len(ok))]
    for x in recs[:15]:
        lines.append("  %s %s model=%s feature=%s provider=%s%s" % (x.get("status"), x.get("path"), x.get("model"), x.get("feature"),
                                                                   x.get("provider"), (" error=%s" % x["error"][:200]) if x.get("error") else ""))
    if not recs:
        lines.append("  no request reached Router: the command didn't run a routed call (or the runtime kept it on the provider)")
    lines.append("output tail:\n" + o[-1500:])
    return AG.out({"ok": True, "exit": code, "requests": recs}, a.json, "\n".join(lines))


# ------------------------------------------------------------------------------------------------ catalog + levers
def catalog(root=None):
    """The live catalog saved at start when a key exists, else the bundled snapshot."""
    from .catalog import Catalog
    p = os.path.join(root or "", R.STATE, "catalog_live.json")
    if root and os.path.exists(p):
        d = json.load(open(p))
        return Catalog(d["models"], d.get("source") or "live")
    return Catalog.load()


def _family(mid):
    """'claude-haiku-4-5' -> 'claude-haiku', 'gpt-5.4-mini' -> 'gpt-mini': same vendor line, versions removed."""
    m = re.sub(r"-\d{8}$", "", mid or "")
    return re.sub(r"-+", "-", re.sub(r"(?<![a-z])\d+(\.\d+)*[a-z]?(?=-|$)", "", m)).strip("-")


LEVER_FACTS = """Router levers (where each applies; measured unless marked; see router_migrate/data/ROUTER.md):
  model choice          any surface; the code names the model. A different model answers every request.
  Flex (service_tier)   Responses surface only; on by default for the account; cheaper, slower (up to ~1 min measured).
  fallbacks (models[])  Responses surface only; another model answers on a pre-answer failure; a request with a models
                        list is served without Flex (measured).
  prompt_cache_key      Responses surface; cache reads are billed at a fraction of input (see each model's cache price).
  metadata / spend tags Responses surface; the feature label header goes on every Router request on any surface.
  provider_timeout      Responses surface (Router-only field).
  model_map / drop_params  router.policy.json, applied by the vendored runtime on any surface.
  /v1/messages          Anthropic-shaped calls: model choice, feature labels, model_map apply; the Router-only fields don't."""


def cmd_levers(a):
    """Facts for improvement work: catalog rows and same-family alternatives for models, the lever table, and (for a
    FILE:LINE) the old rules' suggestions as hints. The agent decides; nothing here is a recommendation it must follow."""
    from .recommend import _has_flex
    root = AG.ledger_root(getattr(a, "path", "."))
    cat = catalog(root)
    args = [x for x in re.split(r"[,\s]+", getattr(a, "loc", None) or "") if x]
    out = {"catalog": cat.source, "models": [], "hints": []}
    lines = ["catalog: %s" % cat.source, LEVER_FACTS, ""]

    def row(m):
        p = m.get("pricing") or {}
        return {"id": m["id"], "owner": m.get("owned_by"), "status": m.get("status", "active"), "surfaces": m.get("surfaces"),
                "input": p.get("input"), "output": p.get("output"), "cache_read": p.get("cache_read_input"), "flex": _has_flex(m["id"])}

    def show(r):
        return "  %-28s %-10s in $%-6s out $%-6s cache-read $%-6s flex %-3s %s" % (
            r["id"], r["owner"] or "", r["input"], r["output"], r["cache_read"], "yes" if r["flex"] else "no",
            ",".join(r["surfaces"] or []))

    models = [x for x in args if not AG.parse_loc(x)[0]]
    locs = [x for x in args if AG.parse_loc(x)[0]]
    for mid in models:
        rid = cat.resolve(mid)[0]
        if not rid:
            lines.append("%s: not in the Router catalog" % mid)
            continue
        me = row(cat.by_id[rid])
        fam = [row(m) for m in cat.models if m["id"] != rid and m.get("owned_by") == me["owner"] and _family(m["id"]) == _family(rid)
               and m.get("status", "active") == "active"]
        out["models"].append({"model": me, "same_family": fam})
        lines += ["%s:" % rid, show(me), "  same family:"] + [show(r) for r in fam[:8]] + [""]
    if locs:
        try:
            from . import recommend as rec
            from .agent import current_plan
            plan = current_plan(root)
            r = rec.build(root, plan, cat, current_lines=True)
            for loc in locs:
                f, ln = AG.parse_loc(loc)
                feats = {s["feature"] for sid, s in r["signals"].items() if sid.split(":")[1] == f and abs(int(sid.split(":")[2]) - ln) <= 3} \
                    if r.get("signals") else set()
                for c in r["cards"]:
                    if set(c["scope"]["features"]) & feats:
                        out["hints"].append({"site": loc, "rule": c["rule"], "title": c["title"], "loses": c.get("loses")})
                        lines.append("hint (heuristic; may be wrong) %s: %s" % (loc, c["title"]))
        except Exception as e:
            lines.append("hints unavailable: %s" % str(e)[:160])
    if not args:
        by = {}
        for m in cat.models:
            if m.get("status", "active") == "active" and "messages" in (m.get("surfaces") or []) + ["responses"]:
                by.setdefault(m.get("owned_by") or "?", []).append(row(m))
        for owner in sorted(by):
            lines.append("%s:" % owner)
            lines += [show(r) for r in sorted(by[owner], key=lambda r: float(r["input"] or 0))[:12]]
        lines.append("\n`router-migrate levers MODEL[,MODEL]` for one model and its same-family alternatives; FILE:LINE for "
                     "the old rules' hints at a call site")
    return AG.out(dict(out, ok=True), getattr(a, "json", False), "\n".join(lines))


# ------------------------------------------------------------------------------------------------ the outside check
def _runtime_edited(root, path):
    """True when a vendored runtime file differs from what the tool renders, ignoring the tool's own policy hook line."""
    from .catalog import Catalog
    from .runtime import render
    name = os.path.basename(path)
    if name not in ("router_runtime.py", "routerRuntime.ts"):
        return False
    tool_lines = lambda t: "\n".join(x for x in t.splitlines() if "router-migrate: compiled policy" not in x).rstrip()
    try:
        return tool_lines(open(os.path.join(root, path), encoding="utf-8").read()) != tool_lines(render(name, Catalog.load()))
    except Exception:
        return False


def run_tests(root, base):
    """(failures, info) for the suite: ONE run with Router on (the runtime pointed at router-sim). Without the key the runtime
    is inert, so a Router-on run no worse than the baseline also covers Router-off; only when it is worse does a Router-off
    run follow, to say whether the change or Router is the cause."""
    from .verify import sim_test_run
    cmd, before, fails = base.get("test_cmd"), base.get("before"), []
    if not cmd:
        return fails, {"status": "skipped", "why": "no test command"}
    on = sim_test_run(root, cmd, 3600)
    info = {"status": "passed", "counts": {k: on.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")},
            "baseline": before, "requests": on.get("requests"), "valid": on.get("valid"), "invalid": on.get("invalid") or []}
    if on.get("invalid"):
        fails.append({"gate": "router", "detail": "router-sim rejected %d request(s): %s" % (
            len(on["invalid"]), "; ".join("%s %s" % (x.get("feature"), x.get("error")) for x in on["invalid"][:3]))})
    elif AG._worse(before, on):
        off = AG.run_cmd(root, cmd)
        cause = "the change itself (Router off is worse too)" if AG._worse(before, off) else "Router-specific (Router off matches the baseline)"
        fails.append({"gate": "tests", "detail": "tests with Router on are %s vs baseline %s; cause: %s: %s" % (
            AG._tc(on), AG._tc(before), cause, AG._last_error(on.get("tail")))})
    if fails:
        info["status"] = "failed"
    return fails, info


def check_tree(root, led, tests=True, report=True):
    """The outside check on the tree at `root` (the migration checkout or a branch worktree), against the base commit.
    Returns the validation.json entry. Never changes the tree."""
    from .verify import syntax_check
    base = led.get("baseline") or {}
    res = {"ok": True, "failures": [], "flags": [], "tests": {}, "typecheck": {}, "router": {}, "routes": {}}
    fails, flags = res["failures"], res["flags"]
    changes = AG.changed_since(root, led["base_tree"])
    changed = [p for st, p in changes if st != "D"]
    for x in syntax_check(root, changed)["failed"]:
        fails.append({"gate": "parse", "detail": "%s: %s" % (x["file"], x["error"][-300:])})
    deleted, deps, rt = [], [], []
    for st, path in changes:
        if TEST_RE.search(path) and st in ("M", "D"):
            fails.append({"gate": "tests-edited", "detail": "existing test file %s was %s (add new tests instead)" % (
                path, "deleted" if st == "D" else "edited")})
            continue
        if st == "D":
            deleted.append(path)
        if os.path.basename(path) in DEP_FILES:
            deps.append(path)
        if st != "D" and _runtime_edited(root, path):
            rt.append(path)
    if deleted:
        flags.append({"kind": "deleted-file", "detail": "%d file(s) deleted" % len(deleted), "files": deleted})
    if deps:
        flags.append({"kind": "dependencies", "detail": "dependency files changed: %s" % ", ".join(deps), "files": deps})
    if rt:
        flags.append({"kind": "runtime-edit", "detail": "the vendored runtime was edited by hand", "files": rt})
    try:
        gone = AG.provider_key_removals(root, led["base_tree"])
    except Exception:
        gone = []
    if gone:
        flags.append({"kind": "provider-key", "detail": "provider key reference(s) removed: %s" % ", ".join(gone), "files": []})
    if base.get("typecheck_cmd"):
        tc = AG.run_cmd(root, base["typecheck_cmd"])
        tb = base.get("typecheck_before") or {}
        res["typecheck"] = {"errors": tc["ts_errors"], "exit": tc["exit"], "baseline": tb.get("errors")}
        if tc["ts_errors"] > (tb.get("errors") or 0) or (tc["exit"] and not tb.get("exit")):
            fails.append({"gate": "typecheck", "detail": "%d type error(s) (baseline %s)\n%s" % (tc["ts_errors"], tb.get("errors"), tc["tail"][-600:])})
    if tests == "background":
        res["tests"] = {"status": "running", "baseline": base.get("before")}
    elif tests:
        tf, res["tests"] = run_tests(root, base)
        fails += tf
        res["router"] = {"requests": res["tests"].get("requests") or 0, "valid": res["tests"].get("valid") or 0,
                         "invalid": res["tests"].pop("invalid", [])}
    else:
        res["tests"] = {"status": "skipped", "why": "dry run (pass --tests to run the suite)", "baseline": base.get("before")}
    try:
        rows = check_sites(root)
        res["routes"] = {"rows": [{"site": "%s:%s" % (r["file"], r["line"]), "routes": bool(r.get("routes")), "reason": r.get("reason")}
                                  for r in rows]}
    except Exception as e:
        res["routes"] = {"rows": [], "error": str(e)[:300]}
    if report:
        doc, errs = R.read_report(root)
        if errs:
            fails.append({"gate": "report", "detail": "MIGRATION.json: " + "; ".join(errs[:12])})
        elif doc:
            for it in doc.get("items") or []:
                b = it.get("branch")
                if it.get("kind") == "improvement" and b and _g(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + b) is None:
                    fails.append({"gate": "report", "detail": "items[%s].branch: branch %s does not exist" % (it["id"], b)})
    res["ok"] = not fails
    return res


def route_truth(root, led, doc, mig):
    """verified (the route check or a valid request from the call's feature, simulated or live), unverified, not routing, stays."""
    rows = (mig.get("routes") or {}).get("rows") or []
    routing = [r["site"] for r in rows if r["routes"]]
    near = lambda loc, pool: any(v.rsplit(":", 1)[0] == loc.rsplit(":", 1)[0] and abs(int(v.rsplit(":", 1)[1]) - int(loc.rsplit(":", 1)[1])) <= 3
                                 for v in pool if ":" in v)
    probed = {r.get("feature") for p in (led.get("probes") or {}).values() for r in p.get("requests") or [] if r.get("valid")}
    probed |= {r.get("feature") for p in led.get("live_probes") or [] for r in p.get("requests") or [] if (r.get("status") or 999) < 400}
    probed.discard(None)
    calls = (doc or {}).get("calls") or []
    verified, unverified, not_routing = list(routing), [], []
    for c in calls:
        site = c.get("site") or ""
        if c.get("now") != "router" or not AG.parse_loc(site)[0] or near(site, verified):
            continue
        f, ln = AG.parse_loc(site)
        if c.get("feature") in probed or AG._feature(root, f, ln) in probed:
            verified.append(site)
        elif near(site, [r["site"] for r in rows]):
            not_routing.append(site)
        else:
            unverified.append(site)
    stays = [{"site": r["site"], "reason": r["reason"]} for r in rows if not r["routes"]]
    return {"verified": sorted(set(verified)), "unverified": unverified, "not_routing": not_routing, "stays": stays}


def _merge_baseline(root, led, b=None):
    if b is None:
        p = baseline_path(root)
        b = json.load(open(p)) if os.path.exists(p) else None
    if not b or b.get("status") != "done":
        return False
    led["baseline"].update({k: b.get(k) for k in ("before", "typecheck_before") if b.get(k) is not None})
    return True


def cmd_verify(a):
    """check --final: the outside check as a dry run (tests only with --tests)."""
    root = AG.ledger_root(a.path)
    led = _ledger(root)
    if led is None:
        return AG.err(a.json, "no v6 migration here", "start with `router-migrate start .`")
    if not _merge_baseline(root, led):
        print("(the baseline is still running in the background; typecheck and tests are compared once it is done)")
        led["baseline"].pop("typecheck_cmd", None)
        a.tests = False
    res = check_tree(root, led, tests=bool(getattr(a, "tests", False)))
    lines = ["check --final: %s; %d call(s) route now; tests: %s" % ("OK" if res["ok"] else "would FAIL",
             sum(1 for r in (res["routes"].get("rows") or []) if r["routes"]), (res.get("tests") or {}).get("status"))]
    lines += ["  [%s] %s" % (f["gate"], f["detail"][:500]) for f in res["failures"]]
    lines += ["  flag (for the human): %s" % f["detail"][:300] for f in res["flags"]]
    if _dirty(root):
        lines.append("  note: uncommitted changes in %s (finish needs everything committed)" % ", ".join(_dirty(root)[:5]))
    return AG.out({"ok": res["ok"], "failures": res["failures"], "flags": res["flags"]}, a.json, "\n".join(lines))


# ------------------------------------------------------------------------------------------------ baseline
def baseline_path(root):
    return AG.sp(root, "baseline.json")


def run_baseline(root, test_cmd, typecheck_cmd):
    """The "before" picture (tests + typecheck on the untouched code), cached by git tree so re-runs never repeat it."""
    tree = AG.snapshot_tree(root)
    p = baseline_path(root)
    if os.path.exists(p):
        b = json.load(open(p))
        if b.get("tree") == tree and b.get("test_cmd") == test_cmd and b.get("status") == "done":
            return b
    b = {"tree": tree, "test_cmd": test_cmd, "typecheck_cmd": typecheck_cmd, "status": "running", "started": time.time()}
    json.dump(b, open(p, "w"))
    if test_cmd:
        r = AG.run_cmd(root, test_cmd)
        b["before"] = {k: r.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")}
    if typecheck_cmd:
        r = AG.run_cmd(root, typecheck_cmd)
        b["typecheck_before"] = {"exit": r["exit"], "errors": r["ts_errors"]}
    b["status"] = "done"
    json.dump(b, open(p, "w"))
    return b


def _detach(root, args, log):
    """Start `router-migrate <args>` as a detached process (survives the caller; output to .router-migrate/<log>)."""
    here = os.path.dirname(os.path.abspath(__file__))
    rm = os.path.join(os.path.dirname(here), "bin", "router-migrate")
    out = open(AG.sp(root, log), "w")
    return subprocess.Popen([rm] + args, cwd=root, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def _wait_baseline(root, timeout=3600):
    t = time.time()
    while time.time() - t < timeout:
        p = baseline_path(root)
        if os.path.exists(p):
            b = json.load(open(p))
            if b.get("status") == "done":
                return b
        time.sleep(2)
    return None


def cmd_baseline(a):
    run_baseline(os.path.abspath(a.path), getattr(a, "test_cmd", None), getattr(a, "typecheck_cmd", None))
    return 0


# ------------------------------------------------------------------------------------------------ start
FALLBACK_PROMPTS = {  # used only when the prompt files are missing from an install
    "migrate_v6.md": "Move this repository's production LLM traffic onto Ramp's Router (see router_migrate/data/ROUTER.md), keep "
                     "its behaviour, review improvements, commit each decision on router-migrate/migration, write "
                     ".router-migrate/MIGRATION.json (router_migrate/schemas/V6_CONTRACTS.md), then run `router-migrate finish .`.\n"
                     "{BUDGET}\n\n{CONTEXT}\n",
    "apply.md": "Carry out the human's requests below however is best, commit each result on router-migrate/migration, write "
                ".router-migrate/APPLY.json (router_migrate/schemas/V6_CONTRACTS.md), then run `router-migrate apply --done`.\n\n"
                "{REQUESTS}\n\n{CONTEXT}\n"}


def _prompt(name, **fill):
    p = os.path.join(PROMPTS, name)
    text = open(p).read() if os.path.exists(p) else FALLBACK_PROMPTS[name]
    for k, v in fill.items():
        text = text.replace("{%s}" % k, v)
    return text


def setup(root, a):
    """Clean tree, branch, ledger, background baseline, live catalog, the map. Returns (ledger, prompt) or raises SystemExit
    with the fact the user needs."""
    from .verify import detect_test_cmd
    if AG._git(root, "rev-parse", "--is-inside-work-tree").returncode:
        raise SystemExit("%s is not a git repository: run `git init && git add -A && git commit -m base` first" % root)
    _exclude(root)
    dirty = _dirty(root)
    if dirty:
        raise SystemExit("uncommitted changes in %s: commit or stash them first (router-migrate works on its own branch and "
                         "never touches work in progress)" % ", ".join(dirty[:6]))
    old = _ledger(root)
    cur = _branch(root)
    if cur == MIGRATION_BRANCH and old:
        base, base_tree = old["base"], old["base_tree"]
    else:
        if _g(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + MIGRATION_BRANCH):
            raise SystemExit("branch %s already exists (an earlier migration): `git checkout %s` to continue it, or delete the "
                             "branch to start over" % (MIGRATION_BRANCH, MIGRATION_BRANCH))
        base = _g(root, "rev-parse", "HEAD")
        r = AG._git(root, "checkout", "-q", "-b", MIGRATION_BRANCH)
        if r.returncode:
            raise SystemExit("could not create branch %s: %s" % (MIGRATION_BRANCH, r.stderr.strip()[-300:]))
        base_tree = _g(root, "rev-parse", base + "^{tree}")
    test_cmd = getattr(a, "test_cmd", None) or (old or {}).get("baseline", {}).get("test_cmd") or detect_test_cmd(root)
    tc_cmd = getattr(a, "typecheck_cmd", None) or (old or {}).get("baseline", {}).get("typecheck_cmd")
    py = re.search(r"(\S+/bin/python[\d.]*)\b", test_cmd or "")
    baseline = {"test_cmd": test_cmd, "typecheck_cmd": tc_cmd, "python": py.group(1) if py else None}
    led = old if (old and old.get("base") == base) else _new_ledger(root, base, base_tree, baseline)
    led["baseline"].update({k: v for k, v in baseline.items() if v})
    led["live"] = bool(R.migration_key())
    AG.save_ledger(root, led)
    b = json.load(open(baseline_path(root))) if os.path.exists(baseline_path(root)) else {}
    if b.get("status") != "done" or b.get("tree") != base_tree or b.get("test_cmd") != test_cmd:
        _detach(root, ["_baseline", root] + (["--test-cmd", test_cmd] if test_cmd else []) +
                (["--typecheck-cmd", tc_cmd] if tc_cmd else []), "baseline.log")
    key = R.migration_key()
    if key:
        from .catalog import Catalog
        c = Catalog.load(live=True, api_key=key)
        if c.source.startswith("live"):
            R.save(root, "catalog_live.json", {"source": c.source + " at " + time.strftime("%Y-%m-%d %H:%M"), "models": c.models})
    try:
        rows = check_sites(root)
    except Exception as e:
        rows, led["check_error"] = [], str(e)[:300]
    secs = (b.get("before") or {}).get("seconds") if b.get("status") == "done" else None
    ctx = ["## This repository",
           "Base commit %s; you are on branch %s (commit your work here; improvements go on branches %s<name>)."
           % ((base or "")[:9], MIGRATION_BRANCH, R.IMPROVE_PREFIX),
           ("Test command: %s%s. The final check runs the full suite with Router on; how you test along the way is up to you."
            % (test_cmd, " (about %ds per run)" % secs if secs else "")) if test_cmd else
           "No test command found (pass `--test-cmd` to `start` if there is one); the final check still parses, typechecks and route-checks.",
           "Typecheck command: %s (the final check compares its errors with the baseline)." % tc_cmd if tc_cmd else "",
           "Python with the repo's dependencies: %s" % baseline["python"] if baseline["python"] else "",
           ("A migration key is available: `router-migrate check --run \"CMD\" --live` runs code against the real Router "
            "(spend-capped; the key is injected into that command only; never print it). The catalog is live (`router-migrate levers`)."
            if key else "No migration key: verify with the simulator (`check --run`) and record live checks as not-run in your evidence."),
           "LLM calls the scanner sees (%d; it misses dynamically built and imported clients), as the code stands:" % len(rows), _fmt(rows)]
    budget = getattr(a, "budget", None) or "Aim to finish within about 45 minutes of work; live checks are capped by the key's spend limit."
    prompt = _prompt("migrate_v6.md", CONTEXT="\n".join(x for x in ctx if x), BUDGET=budget)
    AG._event(root, "setup", "done", msg="%d calls seen; live=%s" % (len(rows), bool(key)))
    return led, prompt


def cmd_start(a):
    """Claude Code (any model): set up, then print the instructions for the session that is already running."""
    root = os.path.abspath(a.path)
    try:
        _, prompt = setup(root, a)
    except SystemExit as e:
        return AG.err(False, str(e), "")
    print(prompt)
    return 0


# ------------------------------------------------------------------------------------------------ finish
def check_branch(root, led, branch, tests=True):
    """The outside check on an improvement branch, in a temporary worktree (the user's checkout is untouched)."""
    commit = _g(root, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch)
    if not commit:
        return {"ref": branch, "commit": None, "ok": False, "failures": [{"gate": "report", "detail": "branch %s not found" % branch}],
                "flags": [], "tests": {}, "typecheck": {}, "router": {}}
    wt = tempfile.mkdtemp(prefix="rm-branch-")
    shutil.rmtree(wt)
    r = AG._git(root, "worktree", "add", "-q", "--detach", wt, commit)
    if r.returncode:
        return {"ref": branch, "commit": commit, "ok": False, "failures": [{"gate": "report", "detail": r.stderr.strip()[-300:]}],
                "flags": [], "tests": {}, "typecheck": {}, "router": {}}
    try:
        for d in DEP_DIRS:
            if os.path.exists(os.path.join(root, d)) and not os.path.exists(os.path.join(wt, d)):
                os.symlink(os.path.realpath(os.path.join(root, d)), os.path.join(wt, d))
        res = check_tree(wt, led, tests=tests, report=False)
        res.pop("routes", None)
    finally:
        AG._git(root, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)
    res.update(ref=branch, commit=commit)
    return res


def validate(root, led, tests=True):
    """validation.json for the migration branch (the checkout) and every improvement branch in the report."""
    doc, _ = R.read_report(root)
    mig = check_tree(root, led, tests=tests, report=True)
    mig.update(ref=MIGRATION_BRANCH, commit=_g(root, "rev-parse", "HEAD"))
    mig["routes"] = route_truth(root, led, doc, mig)
    branches = {}
    for it in (doc or {}).get("items") or []:
        b = it.get("branch") if isinstance(it, dict) else None
        if it.get("kind") == "improvement" and b and b not in branches:
            branches[b] = check_branch(root, led, b, tests=tests)
    v = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "migration": mig, "branches": branches}
    R.save(root, "validation.json", v)
    return v


def cmd_validate(a):
    """Background worker: the full check with tests (slow suites), then the report is refreshed."""
    root = os.path.abspath(a.path)
    led = _ledger(root)
    validate(root, led, tests=True)
    _report_only(root, quiet=True)
    return 0


def _summary(v, doc):
    m = v["migration"]
    rt = m.get("routes") or {}
    br = v.get("branches") or {}
    items = (doc or {}).get("items") or []
    imps = [i for i in items if i.get("kind") == "improvement"]
    lines = ["finish: %s; %d call(s) on Router (verified), %d moved but not verifiable statically, %d reported moved but not "
             "routing, %d stay; tests: %s" % (
                 "check passed" if m["ok"] else "check FAILED (%s)" % ", ".join(sorted({f["gate"] for f in m["failures"]})),
                 len(rt.get("verified") or []), len(rt.get("unverified") or []), len(rt.get("not_routing") or []),
                 len(rt.get("stays") or []), (m.get("tests") or {}).get("status")),
             "items: %d (%s); improvements: %d branch(es), %d passed their check" % (
                 len(items), ", ".join("%d %s" % (sum(1 for i in items if i.get("kind") == k), k) for k in R.KINDS
                                       if any(i.get("kind") == k for i in items)), len(imps),
                 sum(1 for b in br.values() if b.get("ok")))]
    lines += ["  [%s] %s" % (f["gate"], f["detail"][:600]) for f in m["failures"]]
    for name, b in br.items():
        for f in b.get("failures") or []:
            lines.append("  [%s] %s: %s" % (f["gate"], name, f["detail"][:400]))
    lines += ["  flag (for the human): %s" % f["detail"][:300] for f in m.get("flags") or []]
    return lines


def finish(root, a, runs=None):
    """The outside check once (migration + improvement branches), validation.json, then the dashboard. Returns 0 when the
    migration's check passed."""
    led = _ledger(root)
    if led is None:
        print("finish: no v6 migration here; start with `router-migrate start .`")
        return 2
    if _branch(root) != MIGRATION_BRANCH:
        print("finish: check out %s first (it is where the migration lives)" % MIGRATION_BRANCH)
        return 1
    dirty = _dirty(root)
    if dirty:
        print("finish: commit your work first (uncommitted: %s). One commit per decision a human might want to reverse."
              % ", ".join(dirty[:8]))
        return 1
    if _merge_baseline(root, led, _wait_baseline(root)):
        AG.save_ledger(root, led)
    slow = ((led["baseline"].get("before") or {}).get("seconds") or 0) > BACKGROUND_TESTS_SECS
    v = validate(root, led, tests="background" if slow else True)
    doc, _ = R.read_report(root)
    led = _ledger(root)
    if runs:
        c = led.setdefault("cost", {})
        c["agent_usd"] = round((c.get("agent_usd") or 0) + sum(r.get("usd") or 0 for r in runs), 4)
        c["agent_minutes"] = round((c.get("agent_minutes") or 0) + sum(r.get("minutes") or 0 for r in runs), 1)
    led.setdefault("sessions", []).append({"kind": "finish", "at": v["at"], "ok": v["migration"]["ok"]})
    AG.save_ledger(root, led)
    if slow:
        _detach(root, ["_validate", root], "validate.log")
    AG._event(root, "check", "done" if v["migration"]["ok"] else "error",
              {"verified": len(v["migration"]["routes"]["verified"]), "branches": len(v["branches"])})
    print("\n".join(_summary(v, doc)))
    if slow:
        print("tests: the suite takes ~%ds; it is running in the background (Apply waits for it)" % led["baseline"]["before"]["seconds"])
    if getattr(a, "no_dashboard", False):
        _report_only(root)
    else:
        dashboard(root, a)
    return 0 if v["migration"]["ok"] else 1


def cmd_finish(a):
    return finish(AG.ledger_root(a.path), a)


# ------------------------------------------------------------------------------------------------ dashboard
def _report_only(root, quiet=False):
    try:
        from .dashboard import items as DI
        html = DI.render_report(root)
    except Exception as e:  # the dashboard is presentation: a failure here never fails the check
        if not quiet:
            print("report: not written (%s)" % str(e)[:200])
        return 0
    open(AG.sp(root, "REPORT.html"), "w").write(html)
    if not quiet:
        print("report: %s" % AG.sp(root, "REPORT.html"))
    return 0


def dashboard(root, a):
    """REPORT.html, then the live dashboard in a detached process (Claude Code / a terminal isn't blocked); prints its URL."""
    _report_only(root)
    no_browser = getattr(a, "no_browser", False) or os.environ.get("ROUTER_MIGRATE_NO_BROWSER")
    p = _detach(root, ["dashboard", root, "--serve"] + (["--no-browser"] if no_browser else []), "dashboard.log")
    t = time.time()
    while time.time() - t < 15:
        txt = open(AG.sp(root, "dashboard.log")).read() if os.path.exists(AG.sp(root, "dashboard.log")) else ""
        m = re.search(r"(http://127\.0\.0\.1:\d+\S*)", txt)
        if m:
            print("dashboard: %s  (pid %d; stop with `kill %d`)" % (m.group(1), p.pid, p.pid))
            return 0
        time.sleep(0.3)
    print("dashboard: starting (see %s)" % AG.sp(root, "dashboard.log"))
    return 0


def cmd_dashboard(a):
    root = AG.ledger_root(a.path)
    if not getattr(a, "serve", False):
        return dashboard(root, a)
    from .dashboard import items as DI
    no_browser = getattr(a, "no_browser", False) or os.environ.get("ROUTER_MIGRATE_NO_BROWSER")
    srv, url = DI.serve(root, open_browser=not no_browser)
    print("dashboard: %s" % url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


# ------------------------------------------------------------------------------------------------ requests + apply
def cmd_request(a):
    root = AG.ledger_root(a.path)
    try:
        r = R.record_request(root, a.item, a.action, reason=a.reason, until=a.until, by="cli")
    except ValueError as e:
        return AG.err(a.json, str(e), "items and what each allows: see the dashboard")
    _report_only(root, quiet=True)
    return AG.out({"ok": True, "request": r}, a.json, "recorded: %s %s%s" % (a.action, a.item, (" (%s)" % a.reason) if a.reason else ""))


def _requests_text(root, pending):
    doc, _ = R.read_report(root)
    items = {i.get("id"): i for i in (doc or {}).get("items") or []}
    out = []
    for iid, rq in pending.items():
        it = items.get(iid) or {}
        out.append("- %s: %s %s \"%s\"%s\n  %s\n  %s%s%s%s" % (
            iid, rq["action"].upper(), it.get("kind"), it.get("title"), (" (the human's reason: %s)" % rq["reason"]) if rq.get("reason") else "",
            it.get("summary") or "", ("branch %s" % it["branch"]) if it.get("branch") else ("commit %s" % it.get("commit") if it.get("commit") else "no commit recorded"),
            ("; depends on %s" % ", ".join(it["depends_on"])) if it.get("depends_on") else "",
            ("; excludes %s" % ", ".join(it["excludes"])) if it.get("excludes") else "",
            ("\n  why: %s" % it["why"][:600]) if it.get("why") else ""))
    return "\n".join(out)


def _apply_context(root, led):
    log = R.load(root, "apply_log.json", {"sessions": []})
    return "\n".join(x for x in [
        "Base commit %s; migration branch %s (you are on it)." % ((led.get("base") or "")[:9], MIGRATION_BRANCH),
        "Test command: %s." % (led["baseline"].get("test_cmd") or "none"),
        "Typecheck command: %s." % led["baseline"]["typecheck_cmd"] if led["baseline"].get("typecheck_cmd") else "",
        ("A migration key is available: `router-migrate check --run \"CMD\" --live` reaches the real Router." if R.migration_key()
         else "No migration key: verify with the simulator."),
        "Earlier apply sessions: %d." % len(log.get("sessions") or [])] if x)


def cmd_apply(a):
    """`apply`: hand the human's pending requests to an agent (printed for Claude Code; `--agent claude` runs it headless).
    `apply --done`: validate .router-migrate/APPLY.json, re-check, log the session."""
    root = AG.ledger_root(a.path)
    led = _ledger(root)
    if led is None:
        return AG.err(False, "no v6 migration here", "start with `router-migrate start .`")
    if getattr(a, "done", False):
        return apply_done(root, a)
    pending = R.pending_requests(root)
    if not pending:
        print("apply: nothing to do (approve or mark items for undo on the dashboard, or `router-migrate request ITEM ACTION`)")
        return 0
    blocked = R.apply_blocked(R.load(root, "validation.json", {}))
    if blocked and any(r["action"] == "apply" for r in pending.values()):
        undos = {i: r for i, r in pending.items() if r["action"] == "undo"}
        if not undos:
            print("apply: blocked: %s" % blocked)
            return 1
        print("apply: only the undo requests run now (%s)" % blocked)
        pending = undos
    if _branch(root) != MIGRATION_BRANCH or _dirty(root):
        print("apply: needs %s checked out with no uncommitted changes" % MIGRATION_BRANCH)
        return 1
    log = R.load(root, "apply_log.json", {"sessions": []})
    sid = "A%d" % (len(log.get("sessions") or []) + 1)
    led["apply"] = {"id": sid, "requests": sorted(pending), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "head": _g(root, "rev-parse", "HEAD")}
    AG.save_ledger(root, led)
    if os.path.exists(AG.sp(root, "APPLY.json")):
        os.remove(AG.sp(root, "APPLY.json"))
    prompt = _prompt("apply.md", REQUESTS=_requests_text(root, pending), CONTEXT=_apply_context(root, led))
    if getattr(a, "agent", None):
        info = _launch(root, prompt, getattr(a, "model", None), getattr(a, "agent_timeout", None) or 2700)
        if info.get("api_error") and (info.get("turns") or 0) <= 1:
            print("apply: the agent session did not start: %s" % (info.get("final") or "see agent_transcript.jsonl"))
            return 2
        rc = apply_done(root, a, runs=[info])
        if rc == 1 and info.get("session"):
            facts = open(AG.sp(root, "apply_done.txt")).read() if os.path.exists(AG.sp(root, "apply_done.txt")) else ""
            info2 = _launch(root, "`router-migrate apply --done` reported:\n%s\nFix exactly this, then stop." % facts,
                            getattr(a, "model", None), 900, resume=info["session"])
            rc = apply_done(root, a, runs=[info2])
        return rc
    print(prompt)
    return 0


def apply_done(root, a, runs=None):
    led = _ledger(root)
    pend = (led or {}).get("apply")
    msg = []

    def fail(lines):
        open(AG.sp(root, "apply_done.txt"), "w").write("\n".join(lines))
        print("\n".join(lines))
        return 1
    if not pend:
        return fail(["apply --done: no apply session in progress (run `router-migrate apply` first)"])
    p = AG.sp(root, "APPLY.json")
    if not os.path.exists(p):
        return fail(["apply --done: write .router-migrate/APPLY.json first (a summary and one result per requested item)"])
    try:
        doc = json.load(open(p))
    except ValueError as e:
        return fail(["apply --done: APPLY.json is not valid JSON: %s" % e])
    errs = R.validate_apply(doc, pend["requests"])
    if errs:
        return fail(["apply --done: APPLY.json: " + "; ".join(errs)])
    if _branch(root) != MIGRATION_BRANCH or _dirty(root):
        return fail(["apply --done: commit your work on %s first (uncommitted: %s)" % (MIGRATION_BRANCH, ", ".join(_dirty(root)[:6]))])
    _merge_baseline(root, led)
    slow = ((led["baseline"].get("before") or {}).get("seconds") or 0) > BACKGROUND_TESTS_SECS
    v = validate(root, led, tests="background" if slow else True)
    m = v["migration"]
    log = R.load(root, "apply_log.json", {"sessions": []})
    sess = {"id": pend["id"], "started": pend["started"], "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "requests": pend["requests"],
            "summary": doc.get("summary"), "results": doc.get("results"), "head_before": pend.get("head"),
            "head_after": _g(root, "rev-parse", "HEAD"),
            "check": {"ok": m["ok"], "failures": m["failures"], "tests": m.get("tests"), "flags": m.get("flags")}}
    if runs:
        sess["cost"] = {"usd": round(sum(r.get("usd") or 0 for r in runs), 4), "minutes": round(sum(r.get("minutes") or 0 for r in runs), 1)}
    log.setdefault("sessions", []).append(sess)
    R.save(root, "apply_log.json", log)
    rq = R.load(root, "requests.json", {"requests": {}})
    for r in doc.get("results") or []:
        if r.get("item") in rq["requests"] and r.get("item") in pend["requests"]:
            rq["requests"][r["item"]]["session"] = pend["id"]
    R.save(root, "requests.json", rq)
    led = _ledger(root)
    led.pop("apply", None)
    led.setdefault("sessions", []).append({"kind": "apply", "id": sess["id"], "at": sess["finished"], "ok": m["ok"]})
    AG.save_ledger(root, led)
    os.replace(p, AG.sp(root, "APPLY-%s.json" % sess["id"]))
    if slow:
        _detach(root, ["_validate", root], "validate.log")
    _report_only(root, quiet=True)
    for r in doc.get("results") or []:
        msg.append("  %s %s: %s%s" % (r.get("action"), r.get("item"), r.get("status"), (" (%s)" % r["note"][:200]) if r.get("note") else ""))
    print("apply %s: %d result(s); check %s%s\n%s" % (sess["id"], len(doc.get("results") or []), "passed" if m["ok"] else "FAILED",
                                                    "" if m["ok"] else " (%s)" % ", ".join(f["gate"] for f in m["failures"]), "\n".join(msg)))
    for f in m["failures"]:
        print("  [%s] %s" % (f["gate"], f["detail"][:500]))
    return 0 if m["ok"] else 1


def _claude_logged_in():
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=20)
        return '"loggedIn": true' in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def launch_apply(root):
    """The dashboard's Apply: a headless apply session when the agent CLI is logged in, else the command for Claude Code."""
    if not R.pending_requests(root):
        return {"mode": "none", "message": "nothing to apply"}
    if _claude_logged_in():
        log = R.load(root, "apply_log.json", {"sessions": []})
        sid = "A%d" % (len(log.get("sessions") or []) + 1)
        _detach(root, ["apply", root, "--agent", "claude"], "apply_session.log")
        return {"mode": "headless", "session": sid, "log": AG.sp(root, "apply_session.log")}
    return {"mode": "command", "command": "/router-migrate apply"}


# ------------------------------------------------------------------------------------------------ headless agent
def _shim(real):
    """A bin dir whose `router-migrate` exposes only the agent's tools (finish/apply are run by the harness)."""
    d = tempfile.mkdtemp(prefix="router-migrate-bin-")
    p = os.path.join(d, "router-migrate")
    with open(p, "w") as f:
        f.write('#!/bin/sh\ncase "$1" in\n  %s) exec "%s" "$@" ;;\n  *) echo "router-migrate: available here: %s. '
                'The harness runs finish/apply after you stop." >&2; exit 2 ;;\nesac\n'
                % ("|".join(AGENT_COMMANDS), real, ", ".join(AGENT_COMMANDS)))
    os.chmod(p, 0o755)
    return d


def _launch(root, prompt, model, timeout, resume=None):
    """Headless Claude Code session: full shell and web access, dummy OpenAI key, no Router key in its environment
    (live checks go through `check --run --live`, which injects the migration key into that command only)."""
    here = os.path.dirname(os.path.abspath(__file__))
    rm_bin = os.path.join(os.path.dirname(here), "bin")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--allowedTools", AGENT_TOOLS, "--max-turns", "400"]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["--model", model]
    # ANTHROPIC_API_KEY is left alone: it may be how the agent CLI itself authenticates (a dummy one fails it with a 401)
    env = dict(os.environ, **{k: v for k, v in DUMMY_KEYS.items() if k != "ANTHROPIC_API_KEY"})
    for k in ("RAMP_ROUTER_API_KEY", "ROUTER_MIGRATE_KEY"):
        env.pop(k, None)
    env["PATH"] = _shim(os.path.join(rm_bin, "router-migrate")) + os.pathsep + env.get("PATH", "")
    log = AG.sp(root, "agent_transcript.jsonl")
    t = time.time()
    with open(log, "a") as fh:
        try:
            code = subprocess.run(cmd, cwd=root, env=env, stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    res, session = {}, resume
    for line in open(log, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        session = ev.get("session_id") or session
        if ev.get("type") == "result":
            res = ev
    return {"exit": code, "usd": res.get("total_cost_usd"), "turns": res.get("num_turns"), "minutes": round((time.time() - t) / 60, 1),
            "final": (res.get("result") or "")[:2000], "session": session,
            "api_error": bool(res.get("is_error")) or str(res.get("result") or "").startswith(("API Error", "Failed to authenticate", "Invalid API key"))}


def run_v6(root, a):
    """Headless (CI, evals): start, a headless session with the same prompt, then finish; one fix pass with the facts."""
    t0 = time.time()
    try:
        _, prompt = setup(root, a)
    except SystemExit as e:
        return AG.err(False, str(e), "")
    info = _launch(root, prompt, getattr(a, "model", None), getattr(a, "agent_timeout", None) or 2700)
    if info.get("api_error") and (info.get("turns") or 0) <= 1:
        print("agent: the session did not start: %s\n(check `claude auth status`)" % (info.get("final") or "see agent_transcript.jsonl"))
        return 2
    runs = [info]
    a.no_dashboard = getattr(a, "no_dashboard", False) or getattr(a, "no_browser", False)
    rc = finish(root, a, runs=runs)
    if rc != 0 and info.get("session"):
        v = R.load(root, "validation.json", {})
        doc, _ = R.read_report(root)
        facts = "\n".join(_summary(v, doc)) if v else "finish could not run: commit your work on %s and write MIGRATION.json" % MIGRATION_BRANCH
        if _dirty(root):
            facts += "\nuncommitted changes: %s" % ", ".join(_dirty(root)[:8])
        info2 = _launch(root, "The final check reported:\n%s\nFix exactly what failed, commit, then stop." % facts,
                        getattr(a, "model", None), 1200, resume=info["session"])
        runs.append(info2)
        rc = finish(root, a, runs=[info2])
    print("total %.1f min" % ((time.time() - t0) / 60))
    return rc
