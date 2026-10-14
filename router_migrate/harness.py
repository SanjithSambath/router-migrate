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
    try:
        return open(os.path.join(root, path), encoding="utf-8").read() != render(name, Catalog.load())
    except Exception:
        return False


def final_check(root, led, require_report=True):
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
    cmd = base.get("test_cmd")
    if cmd:
        t = AG.run_cmd(root, cmd)
        info["tests"] = {k: t.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")}
        if AG._worse(base.get("before"), t):
            fails.append({"gate": "tests", "files": None, "detail": "tests now %s, baseline %s\n%s" % (AG._tc(t), AG._tc(base.get("before")), t["tail"][-800:])})
        on = sim_test_run(root, cmd, 1800)
        info["tests_router_on"] = {k: on.get(k) for k in ("passed", "failed", "errors", "exit", "requests", "valid")}
        if on.get("invalid"):
            fails.append({"gate": "router", "files": None, "detail": "router-sim rejected %d request(s): %s" % (
                len(on["invalid"]), "; ".join("%s %s" % (x.get("feature"), x.get("error")) for x in on["invalid"][:3]))})
        elif AG._worse(base.get("before"), on):
            fails.append({"gate": "router", "files": None, "detail": "with Router on, tests are %s (Router off baseline %s): %s" % (
                AG._tc(on), AG._tc(base.get("before")), AG._last_error(on.get("tail")))})
    try:
        rows = check_sites(root)
        info["moved"] = [r for r in rows if r.get("routes")]
        info["sites"] = len(rows)
    except Exception as e:  # the check never decides pass/fail; it measures
        info["moved_error"] = str(e)[:300]
    return not fails, fails, info


def cmd_verify(a):
    root = AG.ledger_root(a.path)
    led = AG.load_ledger(root)
    if led is None:
        return AG.err(a.json, "no ledger", "this runs inside `router-migrate run --arm min`")
    ok, fails, info = final_check(root, led, require_report=led.get("arm") != "free")
    lines = ["verify: %s; %d call(s) route now" % ("OK" if ok else "would FAIL", len(info.get("moved") or []))]
    lines += ["  [%s] %s" % (f["gate"], f["detail"][:400]) for f in fails]
    return AG.out({"ok": ok, "failures": fails, "info": {k: v for k, v in info.items() if k != "moved"}}, a.json, "\n".join(lines))


# ------------------------------------------------------------------------------------------------ the agent
AGENT_COMMANDS = ("check", "wrap", "unwrap", "adapt")


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
    env = dict(os.environ, **DUMMY_KEYS)
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
            "final": (res.get("result") or "")[:2000], "session": session, "api_error": str(res.get("result") or "").startswith("API Error")}


def run_d3(root, a):
    from .verify import detect_test_cmd
    if AG._git(root, "rev-parse", "--is-inside-work-tree").returncode:
        return AG.err(False, "%s is not a git repository" % root, "run `git init && git add -A && git commit -m base` first")
    arm = a.arm
    t0 = time.time()
    test_cmd = getattr(a, "test_cmd", None) or detect_test_cmd(root)
    base = {"test_cmd": test_cmd, "typecheck_cmd": getattr(a, "typecheck_cmd", None)}
    if test_cmd:
        b = AG.run_cmd(root, test_cmd)
        base["before"] = {k: b.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")}
    if base["typecheck_cmd"]:
        tb = AG.run_cmd(root, base["typecheck_cmd"])
        base["typecheck_before"] = {"exit": tb["exit"], "errors": tb["ts_errors"]}
    py = re.search(r"(\S+/bin/python[\d.]*)\b", test_cmd or "")
    base["python"] = py.group(1) if py else None
    led = _new_ledger(root, arm, base)
    AG.save_ledger(root, led)
    try:
        rows = check_sites(root)
    except Exception as e:
        rows, led["check_error"] = [], str(e)[:300]
    ctx = ["## This repository",
           "Test command (the final check runs it; you may too): %s" % (test_cmd or "none found"),
           "Python with the repo's dependencies: %s" % base["python"] if base["python"] else "",
           "LLM calls the scanner sees (%d; it misses dynamically built clients), as the code stands:" % len(rows),
           _fmt(rows)]
    here = os.path.dirname(os.path.abspath(__file__))
    prompt = open(os.path.join(here, "prompts", "migrate_d3.md")).read().replace("{CONTEXT}", "\n".join(x for x in ctx if x))
    if arm == "free":
        prompt = prompt.replace("Then stop. A final check runs after you", "Then stop. (A report helps the reviewer; nothing checks it.) For reference, a full check would be")
    AG._event(root, "agent_migrate", "running", msg="arm %s; %d calls seen; setup %.0fs" % (arm, len(rows), time.time() - t0))
    info = _launch(root, prompt, getattr(a, "model", None), getattr(a, "agent_timeout", 2700))
    runs = [info]
    led = AG.load_ledger(root)
    ok, fails, cinfo = final_check(root, led, require_report=arm != "free")
    if not ok and arm == "min" and info.get("session"):
        facts = "The final check failed. Fix exactly these, then stop:\n" + "\n".join("- [%s] %s" % (f["gate"], f["detail"][:600]) for f in fails)
        runs.append(_launch(root, facts, getattr(a, "model", None), 900, resume=info["session"]))
        led = AG.load_ledger(root)
        ok, fails, cinfo = final_check(root, led, require_report=True)
    reverted = []
    if not ok and arm == "min":
        files = {f for x in fails for f in (x.get("files") or [])}
        whole = any(x.get("files") is None for x in fails)
        changes = AG.changed_since(root, led["floor"]["tree"])
        undo = changes if whole else [(st, p) for st, p in changes if p in files]
        AG.restore(root, led["floor"]["tree"], undo)
        reverted = [p for _, p in undo]
        ok2, fails2, cinfo = final_check(root, AG.load_ledger(root), require_report=False)
    sites, _ = read_report(root)
    led = AG.load_ledger(root)
    _report_to_claims(led, sites)
    moved = cinfo.get("moved") or []
    verified = ["%s:%s" % (r["file"], r["line"]) for r in moved] + sorted(led.get("confirmations") or {})
    near = lambda loc: any(v.rsplit(":", 1)[0] == loc.rsplit(":", 1)[0] and abs(int(v.rsplit(":", 1)[1]) - int(loc.rsplit(":", 1)[1])) <= 3
                           for v in verified)
    unverified = [s["site"] for s in sites or [] if s["outcome"] == "migrated" and not near(s["site"])]
    led["result"] = {"arm": arm, "ok": ok, "moved": len(set(verified)), "sites_seen": cinfo.get("sites"),
                     "moved_sites": sorted(set(verified)), "claimed_unverified": unverified, "tradeoffs": len(led.get("tradeoffs") or []),
                     "failures": [f["gate"] for f in fails], "reverted": reverted, "report_sites": len(sites or [])}
    led["cost"] = {"agent_usd": sum(r.get("usd") or 0 for r in runs), "agent_minutes": sum(r["minutes"] for r in runs),
                   "agent_turns": sum(r.get("turns") or 0 for r in runs), "resumed": len(runs) > 1,
                   "api_error": any(r.get("api_error") for r in runs), "agent_exit": runs[-1]["exit"]}
    led["finish"]["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "gates": [f["gate"] for f in fails], "info":
                                     {k: v for k, v in cinfo.items() if k != "moved"}})
    AG.save_ledger(root, led)
    AG._event(root, "gate", "done" if ok else "error", {"moved": len(moved), "tradeoffs": len(led.get("tradeoffs") or [])},
              msg="reverted %d file(s)" % len(reverted) if reverted else "")
    print("D3 %s: %s; %d call(s) route; %d trade-off(s); %.1f min, $%.2f%s" % (
        arm, "check passed" if ok else "check FAILED (%s)" % ", ".join(sorted({f["gate"] for f in fails})), len(moved),
        len(led.get("tradeoffs") or []), (time.time() - t0) / 60, led["cost"]["agent_usd"],
        "; reverted %s" % ", ".join(reverted[:5]) if reverted else ""))
    rc = 0 if ok or arm == "free" else 1
    if arm == "min" and not getattr(a, "skip_improve", False):
        # step 5-6: Improve on what actually routes (cards, an agent judging latency/task), then REPORT.html + the dashboard
        rc = AG._improve_phase(root, a) or rc
    return rc
