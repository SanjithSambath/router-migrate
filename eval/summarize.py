#!/usr/bin/env python3
"""Print the numbers WRITEUP.md quotes, computed from eval/scoreboard.json (+ baseline.json if present). No hand-typed numbers."""
import collections
import json
import os

E = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(E, "scoreboard.json")))
t, rows = d["totals"], [r for r in d["repos"] if "error" not in r]
print("repos: %d (errors: %d)" % (len(d["repos"]), len(d["repos"]) - len(rows)))
print("sites %d | migrated %d | handoff %d | kept %d | flagged %d | eligible %d" % (t["sites_found"], t["migrated"], t["handoff"], t["kept"], t["flagged"], t["router_eligible"]))
print("callsite coverage %.1f%% | eligible coverage %.1f%%" % (100 * t["callsite_coverage"], 100 * t["weighted_coverage"]))
print("tests: same %d | worse %d | not measured %d" % (t["repos_tests_same"], t["repos_tests_worse"], t["repos_no_tests"]))
agg = collections.defaultdict(collections.Counter)
for r in rows:
    for k, v in (r.get("outcomes_by_sdk") or {}).items():
        agg[k].update(v)
print("\nby sdk/surface:")
for k, v in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
    print("  %-40s %s" % (k, dict(v)))
rep = [r for r in rows if r.get("replay_unique_requests") is not None]
print("\nrouter-sim replay (unique request shapes, site literal kwargs): %d ok of %d | Router-on test runs that reached the sim: %d of %d repos" % (
    sum(r.get("replay_ok") or 0 for r in rep), sum(r["replay_unique_requests"] for r in rep),
    sum(1 for r in rows if (r.get("sim_requests_from_tests") or 0) > 0), len(rows)))
lv = [r for r in rows if r.get("live_success_pct") is not None]
if lv:
    print("live: %d repos, mean success %.1f%%, spent $%.6f" % (len(lv), sum(r["live_success_pct"] for r in lv) / len(lv), sum(r.get("live_spent_usd") or 0 for r in lv)))
print("diff lines total %d | runtime mean %.1fs" % (sum(r.get("diff_lines") or 0 for r in rows), sum(r.get("runtime_s") or 0 for r in rows) / max(1, len(rows))))
bp = os.path.join(E, "baseline.json")
if os.path.exists(bp):
    b = json.load(open(bp))
    print("\nbaseline (docs prompt run by a Claude agent; per original call site):")
    tot = {"b_moved": 0, "rm_moved": 0, "sites": 0, "b_clean": 0, "b_broke": 0, "rm_broke": 0, "repos": 0}

    def tc_before(name):
        rp = os.path.join(E, "logs", name + ".row.json")
        return ((json.load(open(rp)).get("_typecheck_before") or {}).get("errors") or 0) if os.path.exists(rp) else 0

    def regressed(before, after, typecheck_after=None, typecheck_before_errors=0):
        if not before or not after:
            return False
        if before.get("typecheck") is not None or after.get("typecheck") is not None:
            if (after.get("ts_errors") or 0) > (before.get("ts_errors") or 0):
                return True
        elif (after.get("passed") or 0) < (before.get("passed") or 0) or (after.get("failed") or 0) + (after.get("errors") or 0) > (
                (before.get("failed") or 0) + (before.get("errors") or 0)):
            return True
        return bool(typecheck_after and (typecheck_after.get("errors") or 0) > typecheck_before_errors)  # pre-existing errors don't count
    for name, x in sorted(b.items()):
        sb = next((r for r in rows if r["repo"] == x["repo"]), {})
        po = x.get("per_original_site") or {}
        if not po:
            print("  %-28s not scored yet" % name)
            continue
        tot["repos"] += 1
        rp = os.path.join(E, "logs", name + ".row.json")
        row = json.load(open(rp)) if os.path.exists(rp) else {}
        b_broke = regressed(x.get("tests_before"), x.get("tests_after"), x.get("typecheck_after"), tc_before(name))
        rm_broke = regressed(sb.get("tests_before"), sb.get("tests_after"), row.get("_typecheck_after"), tc_before(name))
        tot["b_broke"] += b_broke
        tot["rm_broke"] += rm_broke
        tot["b_clean"] += 0 if b_broke else po["moved"]
        tot["b_moved"] += po["moved"]
        tot["rm_moved"] += sb.get("migrated") or 0
        tot["sites"] += po["sites_found"]
        print("  %-28s baseline moved %s/%s, tests %s -> %s, typecheck after %s, diff %s lines, test files changed %s | "
              "router-migrate migrated %s/%s, tests %s -> %s, diff %s" % (
                  name, po["moved"], po["sites_found"], x.get("tests_before"), x.get("tests_after"),
              "%s errors (before %s)" % ((x.get("typecheck_after") or {}).get("errors"), tc_before(name)) if x.get("typecheck_after") else None,
                  x.get("diff_lines"), x.get("test_files_changed"), sb.get("migrated"), sb.get("sites_found"), sb.get("tests_before"),
                  sb.get("tests_after"), sb.get("diff_lines")))
    print("  totals over %d scored repos (%d original sites): baseline moved %d, in %d repos left a regression (tests or typecheck), "
          "moved %d in repos it left intact | router-migrate migrated %d, regressions in %d repos" % (
              tot["repos"], tot["sites"], tot["b_moved"], tot["b_broke"], tot["b_clean"], tot["rm_moved"], tot["rm_broke"]))
