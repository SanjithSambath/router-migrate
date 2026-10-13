"""report: REPORT.md + report.json with every site, its outcome and reason, coverage and verification results."""
import json
import os

from . import __version__


def report(root, quiet=False):
    from .cli import load, save, state_path, print_summary
    plan, applied, ver, scan = load(root, "plan.json"), load(root, "apply.json") or {}, load(root, "verify.json") or {}, load(root, "scan.json") or {}
    if not plan:
        print("report: nothing planned yet")
        return None
    sm = plan["summary"]
    data = {"tool_version": __version__, "root": root, "catalog": plan.get("catalog_source"), "summary": sm,
            "files_scanned": scan.get("files_scanned"), "scan_errors": scan.get("errors", []),
            "edits": applied.get("edits", []), "skipped_patches": applied.get("skipped", []), "runtime_files": applied.get("runtime_files", []),
            "diff_lines": applied.get("diff_lines"), "verify": ver,
            "sites": [{k: s.get(k) for k in ("id", "file", "line", "lang", "sdk", "surface", "kind", "prod", "stream", "model", "outcome", "reason", "via_client")}
                      for s in plan["sites"]]}
    save(root, "report.json", data)
    pct = lambda x: "-" if x is None else "%.0f%%" % (100 * x)
    L = ["# router-migrate report", "",
         "Router traffic activates when `RAMP_ROUTER_API_KEY` is set (kill switch: `ROUTER_MIGRATE_DISABLE=1`).", "",
         "| production LLM sites | migrated | handoff | kept | flagged | call-site coverage | Router-eligible coverage |", "|---|---|---|---|---|---|---|",
         "| %d | %d | %d | %d | %d | %s | %s |" % (sm["sites_found"], sm["migrated"], sm["handoff"], sm["kept"], sm["flagged"],
                                             pct(sm["callsite_coverage"]), pct(sm["weighted_coverage"])), "",
         "- call-site coverage = migrated / all production LLM sites (including endpoints Router does not offer, e.g. embeddings)",
         "- Router-eligible coverage = migrated / production sites whose traffic Router can serve (generation surfaces)",
         "- %d non-production sites (tests, examples, evals) are listed below but not counted" % sm["nonprod_sites"], ""]
    if ver:
        b, a = ver.get("tests_before") or {}, ver.get("tests_after") or {}
        L += ["## Verification", "", "| check | result |", "|---|---|",
              "| tests before (`%s`) | %s |" % (ver.get("test_cmd"), _t(b)), "| tests after | %s |" % _t(a),
              "| changed files compile | %d/%d |" % (ver["syntax"]["checked"] - len(ver["syntax"]["failed"]), ver["syntax"]["checked"])]
        st = ver.get("sim_tests")
        if st:
            L.append("| repo tests with Router on, against router-sim | %d requests, %d valid |" % (st["requests"], st["valid"]))
        sr = [r for r in (ver.get("sim_replay") or {}).get("results", []) if r.get("ok") is not None]
        L.append("| synthetic replay of migrated sites against router-sim | %d/%d valid |" % (sum(1 for r in sr if r["ok"]), len(sr)))
        lr = ver.get("live_replay")
        if lr and lr.get("results") is not None:
            rr = [r for r in lr["results"] if r.get("ok") is not None]
            L.append("| live replay against api.router.com | %d/%d ok, $%.4f |" % (sum(1 for r in rr if r["ok"]), len(rr), lr.get("spent_usd", 0)))
        L.append("")
    L += ["## Sites", "", "| site | sdk | surface | outcome | reason |", "|---|---|---|---|---|"]
    order = {"migrated": 0, "handoff": 1, "flagged": 2, "kept": 3}
    for s in sorted(plan["sites"], key=lambda s: (not s["prod"], order.get(s["outcome"], 9), s["file"], s["line"])):
        L.append("| `%s:%d`%s | %s | %s | **%s** | %s |" % (s["file"], s["line"], "" if s["prod"] else " (non-prod)", s["sdk"], s["surface"], s["outcome"],
                                                        s["reason"].replace("|", "/")))
    if applied.get("skipped"):
        L += ["", "## Patches not applied", ""] + ["- %s:%s %s" % (x.get("file"), x.get("line", ""), x.get("reason")) for x in applied["skipped"]]
    L += ["", "Agent tasks: `.router-migrate/HANDOFF.md`. Full diff: `.router-migrate/apply.diff`.", ""]
    open(state_path(root, "REPORT.md"), "w").write("\n".join(L))
    if not quiet:
        print_summary(sm, "report")
        print("wrote %s and report.json" % os.path.join(".router-migrate", "REPORT.md"))
    return data


def _t(t):
    if not t or t.get("skipped"):
        return "not run: %s" % (t or {}).get("skipped", "-")
    if t.get("passed") is None:
        return "exit %s" % t.get("exit")
    return "%s passed, %s failed, %s errors (exit %s)" % (t["passed"], t.get("failed") or 0, t.get("errors") or 0, t.get("exit"))
