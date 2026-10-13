"""v2 CLI commands: up, recommend, decide, status, apply --decisions (wired from cli.main)."""
import json
import os
import shlex
import sys
import threading
import webbrowser

from . import cli as v1, improve
from .catalog import Catalog


def _repo_name(root):
    return os.path.basename(os.path.abspath(root))


def _print_cards(r):
    for c in r["cards"]:
        imp = c["impact"].get("per_call_pct")
        print("  %-34s %-30s risk %d · %s · %s%s" % (c["id"], c["rule"], c["risk"], c["evidence"]["level"], c["change"]["kind"],
                                                    (" · %+d%%/call" % imp) if isinstance(imp, (int, float)) and imp else ""))
        print("      %s" % c["title"])


def cmd_recommend(root, as_json=False):
    r = improve.recommend(root, Catalog.load(), repo=_repo_name(root))
    if as_json:
        print(json.dumps(r, indent=2))
    else:
        print("recommend: %d cards (%s), %d considered-not-recommended; see %s" % (
            len(r["cards"]), ", ".join("%s %d" % (g, sum(1 for c in r["cards"] if c["lever_group"] == g)) for g in ("unlock", "same_model", "model")),
            len(r["considered_not_recommended"]), os.path.join(improve.STATE, "RECOMMENDATIONS.md")))
        _print_cards(r)
    return 0


def cmd_decide(a):
    root = os.path.abspath(a.path)
    try:
        e = improve.decide(root, a.card_id, a.action, reason=a.reason, until=a.until, by=a.by)
    except (ValueError, KeyError) as ex:
        print("decide: %s" % ex, file=sys.stderr)
        return 1
    print("decide: %s -> %s (etag %s)" % (a.card_id, e["action"], e["etag"]))
    return 0


def cmd_apply_decisions(root):
    log = improve.apply_decisions(root, Catalog.load())
    print("apply --decisions: %d applied, %d skipped%s" % (len(log["applied"]), len(log["skipped"]),
                                                         ("; policy %s" % log["policy_hash"][:8]) if log["policy_hash"] else ""))
    for x in log["applied"]:
        print("  applied %s (%s)" % (x["card"], x["rule"]))
    for x in log["skipped"]:
        print("  skipped %s: %s" % (x["card"], x["reason"]))
    for w in log.get("warnings") or []:
        print("  warning %s" % w)
    return 0


def cmd_status(root, as_json=False):
    rep, ok = improve.status(root)
    if as_json:
        print(json.dumps(dict(rep, ok=ok), indent=2))
    else:
        print("policy: %s%s" % ("router.policy.json %s" % rep["policy_hash"][:8] if rep["policy_hash"] else "none (v1 behaviour)",
                                (" ERROR %s" % rep["policy_error"]) if rep["policy_error"] else ""))
        for c in rep["compiled"]:
            print("  compiled %-50s %s" % (c["file"], "current" if c["current"] else "STALE (re-run apply --decisions)"))
        for o in rep["orphans"]:
            print("  orphan entry matches no site: %s" % json.dumps(o))
        for f, eff in sorted(rep["features"].items()):
            print("  %-60s %s" % (f, json.dumps(eff, sort_keys=True)))
        print("cards: %(total)d total, %(undecided)d undecided, %(approved)d approved" % rep["cards"])
    return 0 if ok else 1


def cmd_up(a):
    import contextlib
    root = os.path.abspath(a.path)
    with contextlib.redirect_stdout(sys.stderr):  # stdout carries only the --agent JSON / the dashboard URL
        steps = _up_steps(a, root)
    return _up_finish(a, root, *steps)


def _up_steps(a, root):
    improve.event(root, "scan", "running")
    sc = v1.do_scan(root, quiet=True)
    improve.event(root, "scan", "done", {"sites": len(sc["sites"])})
    p = v1.do_plan(root, live=a.live, quiet=True)
    improve.event(root, "plan", "done", p["summary"])
    log = v1.do_apply(root, live=a.live, quiet=True)
    improve.event(root, "apply", "done", {"edits": len(log["edits"]), "files": len(log["files_changed"]), "diff_lines": log.get("diff_lines")})
    tests = None
    if not a.skip_verify:
        try:
            ns = type("A", (), {"test_cmd": a.test_cmd, "baseline": None, "live": a.live, "budget": a.max_usd, "timeout": 1800, "json": False})()
            vr = v1.do_verify(root, ns)
            tests = {"before": (vr or {}).get("tests_before"), "after": (vr or {}).get("tests_after")}
            improve.event(root, "verify", "done", {})
        except Exception as e:  # verify is best effort inside `up`
            improve.event(root, "verify", "error", msg=str(e)[:200])
    else:
        improve.event(root, "verify", "skipped")
    r = improve.recommend(root, Catalog.load(), repo=_repo_name(root))
    v1.do_report(root)
    from .dashboard import render as dash_render
    html = dash_render.render_report(improve.bundle(root, repo=_repo_name(root), tests=tests))
    open(improve.sp(root, "REPORT.html"), "w").write(html)
    improve.event(root, "report", "done", {"cards": len(r["cards"])})
    return p, tests


def _up_finish(a, root, p, tests):
    pend = improve.undecided(root)
    if a.agent:
        print(json.dumps({"repo": _repo_name(root), "summary": p["summary"], "undecided": [
            {"id": c["id"], "rule": c["rule"], "title": c["title"], "risk": c["risk"], "applies": c.get("applies", True),
             "decide": "router-migrate decide %s approve|dismiss --reason R|postpone --until DATE|shadow-first --path %s" % (c["id"], shlex.quote(root))}
            for c in pend], "report": improve.sp(root, "REPORT.html")}, indent=2))
        return 2 if pend else 0
    from .dashboard import server as dash_server

    def on_decide(payload):
        return improve.decide(root, payload["card_id"], payload["action"], reason=payload.get("reason"), until=payload.get("until"), by="ui")

    def on_apply():
        out = improve.apply_decisions(root, Catalog.load())
        return {"command": "router-migrate apply --decisions %s" % shlex.quote(root), "output": out}

    srv, url = dash_server.serve(lambda: improve.bundle(root, repo=_repo_name(root), tests=tests), on_decide, on_apply, open_browser=not a.no_browser)
    print("router-migrate dashboard: %s  (Ctrl-C to stop; frozen copy: %s)" % (url, improve.sp(root, "REPORT.html")), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
    return 2 if improve.undecided(root) else 0


def run(a):
    if a.cmd == "up":
        return cmd_up(a)
    if a.cmd == "recommend":
        return cmd_recommend(os.path.abspath(a.path), a.json)
    if a.cmd == "decide":
        return cmd_decide(a)
    if a.cmd == "status":
        return cmd_status(os.path.abspath(a.path), a.json)
    if a.cmd == "apply":
        return cmd_apply_decisions(os.path.abspath(a.path))
    return 1
