"""D3 harness: give the agent facts, a few optional tools and one outside check; it picks its own method.

    run --arm min   baseline -> check --all injected into the prompt -> the agent (full shell, no gate loop) ->
                    the final check (parse, scope, tests/typecheck vs baseline, a Router-on run, a required report,
                    "moved" re-derived by the check itself) -> one resume with the failure facts -> revert what still fails
    run --arm free  the same agent and tools, no final check and no required report ("moved" is still re-derived)

The v4 per-item protocol (worklist, claim per item, mandatory proof, Stop-hook gate) stays in agent.py for older arms."""
import json
import os
import re
import subprocess
import tempfile
import time

from . import agent as AG

D3_TOOLS = "Read,Grep,Glob,Edit,MultiEdit,Write,TodoWrite,Task,Bash"
REPORT = "MIGRATION.json"
OUTCOMES = ("migrated", "kept", "cannot_route", "needs_decision")
DUMMY_KEYS = {"OPENAI_API_KEY": "sk-dummy-not-a-real-key", "ANTHROPIC_API_KEY": "sk-ant-dummy-not-a-real-key"}


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
        lines.append("%s:%s  %s %s  %s" % (r["file"], r["line"], r.get("sdk") or "", r.get("surface") or "",
                                           "ROUTES" if r.get("routes") else "stays: %s" % (r.get("reason") or "")[:160]))
    if len(rows) > limit:
        lines.append("... %d more (run `router-migrate check --all`)" % (len(rows) - limit))
    return "\n".join(lines)


def cmd_check(a):
    """check --all | check FILE:LINE[,...] | check --run "<cmd>" (the old probe sandbox)."""
    root = AG.ledger_root(a.path)
    if getattr(a, "run", None):
        a.probe_cmd, a.timeout = a.run, getattr(a, "timeout", AG.PROBE_TIMEOUT)
        if AG.load_ledger(root) is None:
            AG.save_ledger(root, _new_ledger(root, None, {}))
        return AG.cmd_probe(a)
    locs = AG._locs(a.loc) if a.loc else None
    if a.loc and locs is None:
        return AG.err(a.json, "expected FILE:LINE[,FILE:LINE...], got %r" % a.loc, "e.g. `router-migrate check app/llm.py:42`")
    rows = check_sites(root, locs)
    n = sum(1 for r in rows if r.get("routes"))
    return AG.out({"ok": True, "sites": rows, "routes": n}, a.json,
                  "%d of %d LLM call(s) the scanner sees route to Router now (the scanner misses dynamically built clients)\n%s"
                  % (n, len(rows), _fmt(rows)))


# ------------------------------------------------------------------------------------------------ ledger + report
def _new_ledger(root, arm, base):
    return {"version": 5, "arm": arm, "root": root, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "baseline": base,
            "floor": {"tree": AG.snapshot_tree(root), "migrated": 0, "applied": False}, "claims": {}, "edits": [], "tradeoffs": [],
            "probes": {}, "confirmations": {}, "cards": [], "worklist": [], "proposals": [], "finish": {"attempts": 0, "history": []}}


def read_report(root):
    """(entries, error) from .router-migrate/MIGRATION.json."""
    p = AG.sp(root, REPORT)
    if not os.path.exists(p):
        return None, "no %s: write it before stopping (one entry per production LLM call you decided)" % REPORT
    try:
        d = json.load(open(p))
    except ValueError as e:
        return None, "%s is not valid JSON: %s" % (REPORT, e)
    sites = d.get("sites") if isinstance(d, dict) else d
    if not isinstance(sites, list):
        return None, "%s must be {\"sites\": [...]}" % REPORT
    bad = [s for s in sites if not isinstance(s, dict) or s.get("outcome") not in OUTCOMES or not AG.parse_loc(str(s.get("site") or ""))[0]]
    if bad:
        return None, "%d entr(ies) need site FILE:LINE and outcome in %s, e.g. %s" % (len(bad), "|".join(OUTCOMES), json.dumps(bad[0])[:160])
    lossy = [s for s in sites if s.get("levers") and not (s.get("loses") or "").strip()]
    if lossy:
        return None, "%d entr(ies) took a lever but don't say what is lost (`loses`): %s" % (len(lossy), lossy[0].get("site"))
    return sites, None


def _report_to_claims(led, sites):
    """The report as ledger claims, so the dashboard and report pages show it unchanged."""
    for s in sites or []:
        f, ln = AG.parse_loc(s["site"])
        led["claims"]["%s:%d" % (f, ln)] = {"file": f, "line": ln, "outcome": "flagged" if s["outcome"] == "needs_decision" else
                                            "kept" if s["outcome"] == "cannot_route" else s["outcome"],
                                            "reason_code": s["outcome"] if s["outcome"] in ("cannot_route", "needs_decision") else None,
                                            "why": s.get("why") or "", "evidence": [], "by": "agent", "origin": "report",
                                            "loses": s.get("loses"), "levers": s.get("levers")}


# ------------------------------------------------------------------------------------------------ the final check
def _runtime_edited(root, path):
    """True when a vendored runtime file differs from what the tool renders (agents must use levers, not edit the runtime)."""
    from .catalog import Catalog
    from .runtime import render
    name = os.path.basename(path)
    if name not in ("router_runtime.py", "routerRuntime.ts"):
        return False
    tool_lines = lambda t: "\n".join(x for x in t.splitlines() if "router-migrate: compiled policy" not in x).rstrip()
    try:  # the policy compile hooks the compiled policy into the runtime with one line: that is the tool's, not a hand edit
        return tool_lines(open(os.path.join(root, path), encoding="utf-8").read()) != tool_lines(render(name, Catalog.load()))
    except Exception:
        return False


def run_tests(root, base):
    """(failures, info) for the suite: ONE run with Router on (the runtime pointed at router-sim). Without the key the runtime
    is inert, so a Router-on run no worse than the baseline also covers Router-off; only when it is worse does a Router-off
    run follow, to say whether the change or Router is the cause."""
    from .verify import sim_test_run
    cmd, before, fails, info = base.get("test_cmd"), base.get("before"), [], {}
    if not cmd:
        return fails, {"status": "skipped", "why": "no test command"}
    on = sim_test_run(root, cmd, 3600)
    info = {"status": "passed", "counts": {k: on.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")},
            "requests": on.get("requests"), "valid": on.get("valid")}
    if on.get("invalid"):
        fails.append({"gate": "router", "files": None, "detail": "router-sim rejected %d request(s): %s" % (
            len(on["invalid"]), "; ".join("%s %s" % (x.get("feature"), x.get("error")) for x in on["invalid"][:3]))})
    elif AG._worse(before, on):
        off = AG.run_cmd(root, cmd)
        cause = "the change itself (Router off is worse too)" if AG._worse(before, off) else "Router-specific (Router off matches the baseline)"
        fails.append({"gate": "tests", "files": None, "detail": "tests with Router on are %s vs baseline %s; cause: %s: %s" % (
            AG._tc(on), AG._tc(before), cause, AG._last_error(on.get("tail")))})
    if fails:
        info["status"] = "failed"
    return fails, info


def final_check(root, led, require_report=True, tests=True):
    """(ok, failures, info). Everything is re-derived from the tree; nothing the agent wrote is trusted."""
    from .verify import syntax_check, sim_test_run
    fails, info = [], {}
    tree = led["floor"]["tree"]
    changes = AG.changed_since(root, tree)
    changed = [p for st, p in changes if st != "D"]
    for x in syntax_check(root, changed)["failed"]:
        fails.append({"gate": "parse", "files": [x["file"]], "detail": "%s: %s" % (x["file"], x["error"][-300:])})
    for st, path in changes:
        if st == "D":
            fails.append({"gate": "scope", "files": [path], "detail": "%s was deleted" % path})
        elif not AG.scan_py.is_prod(path) or re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[jt]sx?$", path):
            fails.append({"gate": "scope", "files": [path], "detail": "test file %s was edited" % path})
        elif os.path.basename(path) in AG.LOCKFILES or path.startswith((".github/", ".gitlab")):
            fails.append({"gate": "scope", "files": [path], "detail": "%s (dependencies/CI) was edited" % path})
        elif _runtime_edited(root, path):
            fails.append({"gate": "scope", "files": [path], "detail": "the vendored runtime %s was edited by hand; use a lever "
                                                                      "(`adapt --unlock-host/--drop/--model-map`) instead" % path})
    gone = AG.provider_key_removals(root, tree)
    if gone:
        fails.append({"gate": "invariant", "files": [], "detail": "provider key reference(s) removed: %s" % ", ".join(gone)})
    if require_report:
        sites, rerr = read_report(root)
        if rerr:
            fails.append({"gate": "report", "files": [], "detail": rerr})
        info["report_sites"] = len(sites or [])
    base = led.get("baseline") or {}
    if base.get("typecheck_cmd"):
        tc = AG.run_cmd(root, base["typecheck_cmd"])
        tb = base.get("typecheck_before") or {}
        info["typecheck"] = {"exit": tc["exit"], "errors": tc["ts_errors"]}
        if tc["ts_errors"] > (tb.get("errors") or 0) or (tc["exit"] and not tb.get("exit")):
            fails.append({"gate": "typecheck", "files": None, "detail": "%d type errors (baseline %s)\n%s" % (tc["ts_errors"], tb.get("errors"), tc["tail"][-600:])})
    if tests == "background":
        info["tests"] = {"status": "running"}
    elif tests:
        tf, info["tests"] = run_tests(root, base)
        fails += tf
    else:
        info["tests"] = {"status": "skipped", "why": "dry run (pass --tests to run the suite)"}
    try:
        rows = check_sites(root)
        info["moved"] = [r for r in rows if r.get("routes")]
        info["sites"] = len(rows)
    except Exception as e:  # the check never decides pass/fail; it measures
        info["moved_error"] = str(e)[:300]
    return not fails, fails, info


def _merge_baseline(root, led, b=None):
    """The background baseline's results into the ledger (True when merged). Without them a dry run would compare against
    nothing and call every pre-existing type error a regression."""
    if b is None:
        p = baseline_path(root)
        b = json.load(open(p)) if os.path.exists(p) else None
    if not b or b.get("status") != "done":
        return False
    led["baseline"].update({k: b.get(k) for k in ("before", "typecheck_before") if b.get(k) is not None})
    return True


def cmd_verify(a):
    root = AG.ledger_root(a.path)
    led = AG.load_ledger(root)
    if led is None:
        return AG.err(a.json, "no ledger", "start with `router-migrate start .`")
    if led.get("version") == 5 and not _merge_baseline(root, led):
        p = baseline_path(root)
        if os.path.exists(p) and json.load(open(p)).get("status") == "running":
            print("(the baseline is still running in the background; typecheck/tests are compared once it is done)")
            led["baseline"].pop("typecheck_cmd", None)
            a.tests = False
    ok, fails, info = final_check(root, led, require_report=led.get("arm") != "free", tests=bool(getattr(a, "tests", False)))
    lines = ["check --final: %s; %d call(s) route now; tests: %s" % ("OK" if ok else "would FAIL", len(info.get("moved") or []),
                                                                     (info.get("tests") or {}).get("status"))]
    lines += ["  [%s] %s" % (f["gate"], f["detail"][:400]) for f in fails]
    return AG.out({"ok": ok, "failures": fails, "info": {k: v for k, v in info.items() if k != "moved"}}, a.json, "\n".join(lines))


# ------------------------------------------------------------------------------------------------ the agent
AGENT_COMMANDS = ("check", "levers", "wrap", "unwrap", "adapt")


def _shim(real):
    """A bin dir whose `router-migrate` exposes only the D3 tools; older pipelines (scan/plan/apply/verify/up/run...)
    are not the agent's to call."""
    d = tempfile.mkdtemp(prefix="router-migrate-d3-bin-")
    p = os.path.join(d, "router-migrate")
    with open(p, "w") as f:
        f.write('#!/bin/sh\ncase "$1" in\n  %s) exec "%s" "$@" ;;\n  *) echo "router-migrate: available here: %s '
                '(check --all | FILE:LINE | --run CMD | --final). Older pipeline commands are not part of this migration." >&2; '
                'exit 2 ;;\nesac\n' % ("|".join(AGENT_COMMANDS), real, ", ".join(AGENT_COMMANDS)))
    os.chmod(p, 0o755)
    return d


def _launch(root, prompt, model, timeout, resume=None):
    """Headless agent with a full shell (no Router key, dummy provider keys) and router-migrate on PATH. No Stop hook."""
    here = os.path.dirname(os.path.abspath(__file__))
    rm_bin = os.path.join(os.path.dirname(here), "bin")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--allowedTools", D3_TOOLS, "--max-turns", "300"]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["--model", model]
    # Provider keys are dummied so the agent's own test runs can't reach a real provider. ANTHROPIC_API_KEY is left alone:
    # it may be how the agent CLI itself authenticates (a dummy one fails it with a 401); `check --run` dummies it anyway.
    env = dict(os.environ, **{k: v for k, v in DUMMY_KEYS.items() if k != "ANTHROPIC_API_KEY"})
    env.pop("RAMP_ROUTER_API_KEY", None)
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


BACKGROUND_TESTS_SECS = 90  # a suite slower than this runs in the background after finish; Apply waits for it


def _tree(root):
    return AG.snapshot_tree(root)


def baseline_path(root):
    return AG.sp(root, "baseline.json")


def run_baseline(root, test_cmd, typecheck_cmd):
    """The "before" picture (tests + typecheck on the untouched code), cached by git tree so re-runs never repeat it."""
    tree = _tree(root)
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


def setup(root, a, arm="min"):
    """Step 1 (code, seconds): ledger, the map of every LLM call (does it route now, and why not), and the baseline started in
    the background (the agent only needs the map). Returns (ledger, prompt)."""
    from .verify import detect_test_cmd
    test_cmd = getattr(a, "test_cmd", None) or detect_test_cmd(root)
    tc_cmd = getattr(a, "typecheck_cmd", None)
    base = {"test_cmd": test_cmd, "typecheck_cmd": tc_cmd}
    py = re.search(r"(\S+/bin/python[\d.]*)\b", test_cmd or "")
    base["python"] = py.group(1) if py else None
    old = AG.load_ledger(root) or {}
    led = _new_ledger(root, arm, base)
    if old.get("version") == 5 and old.get("floor"):  # a re-run keeps the original "before" tree
        led["floor"] = old["floor"]
    AG.save_ledger(root, led)
    cached = os.path.exists(baseline_path(root)) and json.load(open(baseline_path(root))).get("tree") == led["floor"]["tree"]
    if not cached or json.load(open(baseline_path(root))).get("status") != "done":
        _detach(root, ["_baseline", root] + (["--test-cmd", test_cmd] if test_cmd else []) + (["--typecheck-cmd", tc_cmd] if tc_cmd else []),
                "baseline.log")
    try:
        rows = check_sites(root)
    except Exception as e:
        rows, led["check_error"] = [], str(e)[:300]
    secs = (json.load(open(baseline_path(root))).get("before") or {}).get("seconds") if cached else None
    ctx = ["## This repository",
           ("Test command: %s%s. The final check runs the full suite after you; how you test along the way is up to you." % (
               test_cmd, " (about %ds per full run)" % secs if secs else "")) if test_cmd else
           "No test command found (pass `--test-cmd` to `start` if there is one); the final check still parses, typechecks and route-checks.",
           "Typecheck command: %s (the final check compares its errors with the baseline)." % tc_cmd if tc_cmd else "",
           "Python with the repo's dependencies: %s" % base["python"] if base["python"] else "",
           "LLM calls the scanner sees (%d; it misses dynamically built clients), as the code stands:" % len(rows), _fmt(rows)]
    here = os.path.dirname(os.path.abspath(__file__))
    prompt = open(os.path.join(here, "prompts", "migrate_d3.md")).read().replace("{CONTEXT}", "\n".join(x for x in ctx if x))
    if arm == "free":
        prompt = prompt.replace("Then stop. A final check runs after you", "Then stop. (A report helps the reviewer; nothing checks it.) For reference, a full check would be")
    AG._event(root, "setup", "done", msg="%d calls seen" % len(rows))
    return led, prompt


def cmd_start(a):
    """Claude Code (any model): set up, then print the instructions + context for the session that is already running."""
    root = os.path.abspath(a.path)
    if AG._git(root, "rev-parse", "--is-inside-work-tree").returncode:
        return AG.err(False, "%s is not a git repository" % root, "run `git init && git add -A && git commit -m base` first")
    _, prompt = setup(root, a, "min")
    print(prompt.replace(" Then stop.", " Then run `router-migrate finish .` from the repository root.", 1))
    print("\n## Finishing\n`router-migrate finish .` runs the outside check, builds the improvement cards and starts the "
          "dashboard (it prints the URL). If it reports failures, fix exactly those and run it again; a second failure reverts "
          "only the offending files.")
    return 0


def cmd_baseline(a):
    root = os.path.abspath(a.path)
    run_baseline(root, getattr(a, "test_cmd", None), getattr(a, "typecheck_cmd", None))
    return 0


def _site_feature(root, loc, plan):
    f, ln = AG.parse_loc(loc)
    return AG._feature(root, f, ln), AG.site_at(plan["sites"], f, ln)


def record_report(root, led, sites):
    """The agent's report into the ledger: claims (dashboard), judgments (latency/task/cacheable → Improve's rules and the
    guardrails), and improvement proposals (→ cards after validation)."""
    _report_to_claims(led, sites)
    plan = AG.current_plan(root)
    led.setdefault("judgments", {})
    props = []
    try:  # proposals may also come as a top-level list, each naming its site
        top = json.load(open(AG.sp(root, REPORT))).get("proposals") or []
    except (OSError, ValueError, AttributeError):
        top = []
    for p in top:
        if isinstance(p, dict) and p.get("site") and p.get("lever") and AG.parse_loc(str(p["site"]))[0]:
            props.append(dict(p, feature=_site_feature(root, p["site"], plan)[0]))
    for s in sites or []:
        feat, site = _site_feature(root, s["site"], plan)
        f, ln = AG.parse_loc(s["site"])
        c = led["claims"].get("%s:%d" % (f, ln))
        if c is not None:
            c["feature"], c["stream"] = feat, bool((site or {}).get("stream"))
            for k in ("latency", "task"):
                if s.get(k):
                    c[k] = s[k]
        if s["outcome"] == "migrated":
            for k in ("latency", "task", "cacheable"):
                if s.get(k) not in (None, ""):
                    led["judgments"].setdefault(feat, {})[k] = {"value": str(s[k]), "why": s.get("why") or "", "evidence": [s["site"]], "by": "migrate agent"}
        for imp_ in s.get("improvements") or []:
            if isinstance(imp_, dict) and imp_.get("lever"):
                props.append(dict(imp_, site=s["site"], feature=feat))
    led["proposals"] = props


def route_truth(root, led, sites, cinfo):
    moved = cinfo.get("moved") or []
    verified = sorted({"%s:%s" % (r["file"], r["line"]) for r in moved} | set(led.get("confirmations") or {}))
    near = lambda loc, pool: any(v.rsplit(":", 1)[0] == loc.rsplit(":", 1)[0] and abs(int(v.rsplit(":", 1)[1]) - int(loc.rsplit(":", 1)[1])) <= 3 for v in pool)
    seen = cinfo.get("seen") or []
    # a move the scanner can't see counts as verified when a `check --run` sent a valid Router request from its feature
    probed = {r.get("feature") for p in (led.get("probes") or {}).values() for r in p.get("requests") or [] if r.get("valid")}
    for s in sites or []:
        if s["outcome"] == "migrated" and not near(s["site"], verified):
            f, ln = AG.parse_loc(s["site"])
            if AG._feature(root, f, ln) in probed:
                verified.append(s["site"])
    claimed = [s["site"] for s in sites or [] if s["outcome"] == "migrated" and not near(s["site"], verified)]
    not_routing = [x for x in claimed if near(x, seen)]
    unverified = [x for x in claimed if x not in not_routing]
    stays = [{"site": "%s:%s" % (r["file"], r["line"]), "reason": r.get("reason")} for r in cinfo.get("rows") or [] if not r.get("routes")]
    return {"moved_verified": verified, "moved_unverified": unverified, "claimed_not_routing": not_routing, "stays": stays,
            # older names, kept for the eval scorer and older dashboards
            "moved": len(verified), "moved_sites": verified, "claimed_unverified": unverified}


def finish(root, a, arm="min", resume=None, runs=None):
    """Steps 3-6 (code): the outside check, route truth, the report into judgments/proposals, guardrails, Improve's cards,
    REPORT.html and the dashboard. `resume(facts)` (headless) gets one fix pass; in Claude Code the session runs finish again
    after fixing (the second failing finish reverts only the offending files)."""
    led = AG.load_ledger(root)
    if led is None or led.get("version") != 5:
        return AG.err(False, "no v5 ledger here", "start with `router-migrate start .` (Claude Code) or `router-migrate run <repo> --agent claude`")
    if _merge_baseline(root, led, _wait_baseline(root)):
        AG.save_ledger(root, led)
    slow = ((led["baseline"].get("before") or {}).get("seconds") or 0) > BACKGROUND_TESTS_SECS
    mode = "background" if slow and not getattr(a, "foreground_tests", False) else True
    ok, fails, cinfo = final_check(root, led, require_report=arm != "free", tests=mode)
    attempts = led.setdefault("finish", {"attempts": 0, "history": []}).get("attempts", 0) + (0 if ok else 1)
    led["finish"]["attempts"] = attempts
    AG.save_ledger(root, led)
    if not ok and arm == "min" and attempts < 2:
        facts = "The final check failed. Fix exactly these, then stop:\n" + "\n".join("- [%s] %s" % (f["gate"], f["detail"][:600]) for f in fails)
        if resume is None:  # Claude Code: hand the facts back to the session
            print(facts + "\n\nThen run `router-migrate finish .` again.")
            return 1
        runs.append(resume(facts))
        return finish(root, a, arm, resume=None if attempts >= 1 else resume, runs=runs)
    reverted = []
    if not ok and arm == "min":
        files = {f for x in fails for f in (x.get("files") or [])}
        whole = any(x.get("files") is None for x in fails)
        changes = AG.changed_since(root, led["floor"]["tree"])
        undo = changes if whole else [(st, pth) for st, pth in changes if pth in files]
        AG.restore(root, led["floor"]["tree"], undo)
        reverted = [pth for _, pth in undo]
        _, _, cinfo = final_check(root, AG.load_ledger(root), require_report=False, tests=False)
    sites, _ = read_report(root)
    led = AG.load_ledger(root)
    try:
        rows = check_sites(root)
        cinfo["rows"], cinfo["seen"] = rows, ["%s:%s" % (r["file"], r["line"]) for r in rows]
    except Exception:
        pass
    record_report(root, led, sites)
    res = route_truth(root, led, sites, cinfo)
    res.update({"arm": arm, "ok": ok, "sites_seen": cinfo.get("sites"), "tradeoffs": len([t for t in led.get("tradeoffs") or [] if not t.get("undone")]),
                "failures": [f["gate"] for f in fails], "reverted": reverted, "report_sites": len(sites or []),
                "tests": {"baseline": led["baseline"].get("before"), "full": cinfo.get("tests") or {}}})
    led["result"] = res
    if runs:
        led["cost"] = {"agent_usd": sum(r.get("usd") or 0 for r in runs), "agent_minutes": sum(r["minutes"] for r in runs),
                       "agent_turns": sum(r.get("turns") or 0 for r in runs), "resumed": len(runs) > 1,
                       "api_error": any(r.get("api_error") for r in runs), "agent_exit": runs[-1]["exit"]}
    led["finish"]["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "gates": [f["gate"] for f in fails],
                                     "info": {k: v for k, v in cinfo.items() if k not in ("moved", "rows", "seen")}})
    files = AG.compile_guardrails(root, led)
    if files:
        led["edits"].append({"by": "guardrails", "files": files, "guardrails": led.get("guardrails"), "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    AG.save_ledger(root, led)
    if mode == "background" and ok:
        _detach(root, ["_fulltest", root], "fulltest.log")
    AG._event(root, "check", "done" if ok else "error", {"moved": len(res["moved_verified"]), "unverified": len(res["moved_unverified"]),
                                                       "tradeoffs": res["tradeoffs"]}, msg="reverted %d file(s)" % len(reverted) if reverted else "")
    print("finish: %s; %d call(s) on Router (verified), %d moved but not verifiable statically, %d claimed but not routing; "
          "%d trade-off(s); tests: %s%s" % (
              "check passed" if ok else "check FAILED (%s)" % ", ".join(sorted({f["gate"] for f in fails})), len(res["moved_verified"]),
              len(res["moved_unverified"]), len(res["claimed_not_routing"]), res["tradeoffs"], (cinfo.get("tests") or {}).get("status"),
              "; reverted %s" % ", ".join(reverted[:5]) if reverted else ""))
    if getattr(a, "skip_improve", False):
        return 0 if ok or arm == "free" else 1
    imp = AG.do_improve(root)
    r = imp["recommendations"]
    print("improve: %d card(s); %d lever(s) considered and not recommended (with reasons)" % (len(r["cards"]), len(r["considered_not_recommended"])))
    dashboard(root, a) if not getattr(a, "no_dashboard", False) else _report_only(root)
    return 0 if ok or arm == "free" else 1


def _report_only(root):
    from . import improve as imp
    from .dashboard import render as dash_render
    open(AG.sp(root, "REPORT.html"), "w").write(dash_render.render_report(imp.bundle(root, repo=os.path.basename(root))))
    print("report: %s" % AG.sp(root, "REPORT.html"))
    return 0


def dashboard(root, a):
    """REPORT.html, then the live dashboard in a detached process (so Claude Code / a terminal isn't blocked); prints its URL."""
    _report_only(root)
    p = _detach(root, ["dashboard", root, "--serve"] + (["--no-browser"] if getattr(a, "no_browser", False) else []), "dashboard.log")
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
    if getattr(a, "serve", False):
        return AG.open_dashboard(root, a)
    return dashboard(root, a)


def cmd_fulltest(a):
    """Background worker: the full suite once, Router on; the result lands in ledger.result.tests.full (Apply waits for it)."""
    root = os.path.abspath(a.path)
    led = AG.load_ledger(root)
    fails, info = run_tests(root, led["baseline"])
    led = AG.load_ledger(root)
    led.setdefault("result", {}).setdefault("tests", {})["full"] = dict(info, failures=fails)
    AG.save_ledger(root, led)
    AG._event(root, "tests", "done" if not fails else "error", msg=info.get("status"))
    return 0


def cmd_finish(a):
    root = AG.ledger_root(a.path)
    return finish(root, a, (AG.load_ledger(root) or {}).get("arm") or "min")


def run_d3(root, a):
    """Headless (CI, evals): setup, a headless Claude Code session with the same prompt, then finish."""
    if AG._git(root, "rev-parse", "--is-inside-work-tree").returncode:
        return AG.err(False, "%s is not a git repository" % root, "run `git init && git add -A && git commit -m base` first")
    arm = a.arm or "min"
    t0 = time.time()
    _, prompt = setup(root, a, arm)
    AG._event(root, "agent_migrate", "running", msg="arm %s" % arm)
    info = _launch(root, prompt, getattr(a, "model", None), getattr(a, "agent_timeout", 2700))
    runs = [info]
    resume = (lambda facts: _launch(root, facts, getattr(a, "model", None), 900, resume=info["session"])) if info.get("session") and arm == "min" else None
    if resume is None:
        resume = lambda facts: {"minutes": 0, "exit": "no-session"}
    if info.get("api_error") and (info.get("turns") or 0) <= 1 and not AG.changed_since(root, AG.load_ledger(root)["floor"]["tree"]):
        print("agent: the session did not start: %s\n(check `claude auth status`; nothing was changed)" % (info.get("final") or "see .router-migrate/agent_transcript.jsonl"))
        return 2
    rc = finish(root, a, arm, resume=resume, runs=runs)
    print("total %.1f min" % ((time.time() - t0) / 60))
    return rc


def cmd_levers(a):
    """Improvement facts for migrated calls: model, Flex tier, price, what the rules would suggest (cards) and what they
    considered and why not. The agent uses these facts to propose improvements in its report; the human approves cards."""
    from . import recommend as R
    from .catalog import Catalog
    root = AG.ledger_root(a.path)
    led = AG.load_ledger(root) or {}
    if led.get("version") != 5:
        return AG.err(a.json, "no v5 ledger here", "start with `router-migrate start .`")
    imp = AG.do_improve(root)
    r = imp["recommendations"]
    cat = Catalog.load()
    locs = AG._locs(a.loc) if getattr(a, "loc", None) else None
    by_feat = {}
    for sid, sg in r["signals"].items():
        by_feat.setdefault(sg["feature"], sg)
    rows = []
    for f in imp["features"]:
        if locs and not any(f["file"] == x and abs(int(f["line"]) - int(y)) <= 5 for x, y in locs):
            continue
        sg = by_feat.get(f["feature"]) or {}
        mid = sg.get("resolved_model")
        row = cat.info(mid) if mid else None
        pr = (row or {}).get("pricing") or {}
        price = ("$%s in / $%s out per 1M tokens" % (pr.get("input"), pr.get("output"))) if pr.get("input") is not None else "price unknown"
        cards = [c for c in r["cards"] if f["feature"] in (c.get("scope") or {}).get("features", [])]
        cons = [c for c in r["considered_not_recommended"] if f["feature"] in (c.get("scope") or {}).get("features", [])]
        rows.append({"feature": f["feature"], "site": "%s:%s" % (f["file"], f["line"]), "model": mid, "flex_tier": bool(mid and R._has_flex(mid)),
                     "price": price, "latency": sg.get("latency_class"), "latency_source": (sg.get("sources") or {}).get("latency_class"),
                     "suggested": [{"rule": c["rule"], "title": c["title"], "loses": c.get("loses")} for c in cards],
                     "considered": [{"rule": c["rule"], "reason": c["reason"]} for c in cons]})
    lines = []
    for x in rows:
        lines.append("%s  (%s)\n  model %s · Flex tier %s · %s · latency %s (%s)" % (
            x["feature"], x["site"], x["model"] or "unknown", "yes" if x["flex_tier"] else "no", x["price"], x["latency"], x["latency_source"]))
        lines += ["  suggest %-26s %s%s" % (c["rule"], c["title"][:90], (" — loses: %s" % "; ".join(c["loses"])[:120]) if c.get("loses") else "") for c in x["suggested"]]
        lines += ["  not     %-26s %s" % (c["rule"], c["reason"][:120]) for c in x["considered"]]
    if not rows:
        lines.append("no migrated calls yet (levers apply to calls that route to Router)")
    lines.append("propose what fits this repo in MIGRATION.json `improvements` (lever, change, gains, loses, why); a human approves each card")
    return AG.out({"ok": True, "features": rows}, a.json, "\n".join(lines))
