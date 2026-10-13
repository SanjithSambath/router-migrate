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
    print("live: %d repos, mean success %.1f%%, spent $%.4f" % (len(lv), sum(r["live_success_pct"] for r in lv) / len(lv), sum(r.get("live_spent_usd") or 0 for r in lv)))
print("diff lines total %d | runtime mean %.1fs" % (sum(r.get("diff_lines") or 0 for r in rows), sum(r.get("runtime_s") or 0 for r in rows) / max(1, len(rows))))
bp = os.path.join(E, "baseline.json")
if os.path.exists(bp):
    b = json.load(open(bp))
    print("\nbaseline (docs prompt run by a Claude agent; per original call site):")
    tot = {"b_moved": 0, "rm_moved": 0, "sites": 0}
    for name, x in sorted(b.items()):
        sb = next((r for r in rows if r["repo"] == x["repo"]), {})
        po = x.get("per_original_site") or {}
        if not po:
            print("  %-28s not scored yet" % name)
            continue
        tot["b_moved"] += po["moved"]
        tot["rm_moved"] += sb.get("migrated") or 0
        tot["sites"] += po["sites_found"]
        print("  %-28s baseline moved %s/%s, tests %s -> %s, typecheck after %s, diff %s lines, test files changed %s | "
              "router-migrate migrated %s/%s, tests %s -> %s, diff %s" % (
                  name, po["moved"], po["sites_found"], x.get("tests_before"), x.get("tests_after"), x.get("typecheck_after"),
                  x.get("diff_lines"), x.get("test_files_changed"), sb.get("migrated"), sb.get("sites_found"), sb.get("tests_before"),
                  sb.get("tests_after"), sb.get("diff_lines")))
    print("  totals over scored repos: baseline moved %d, router-migrate migrated %d, of %d original sites" % (tot["b_moved"], tot["rm_moved"], tot["sites"]))
