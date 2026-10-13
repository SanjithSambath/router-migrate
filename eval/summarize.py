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
st = [r["sim_detail"] for r in rows if r.get("sim_detail")]
print("\nrouter-sim: repo-test requests %d (valid %d), replayed sites %d (valid %d)" % (
    sum(s["repo_test_requests"] for s in st), sum(s["repo_test_valid"] for s in st), sum(s["replayed_sites"] for s in st), sum(s["replay_valid"] for s in st)))
lv = [r for r in rows if r.get("live_success_pct") is not None]
if lv:
    print("live: %d repos, mean success %.1f%%, spent $%.4f" % (len(lv), sum(r["live_success_pct"] for r in lv) / len(lv), sum(r.get("live_spent_usd") or 0 for r in lv)))
print("diff lines total %d | runtime mean %.1fs" % (sum(r.get("diff_lines") or 0 for r in rows), sum(r.get("runtime_s") or 0 for r in rows) / max(1, len(rows))))
bp = os.path.join(E, "baseline.json")
if os.path.exists(bp):
    b = json.load(open(bp))
    print("\nbaseline (docs prompt via claude -p):")
    for name, x in b.items():
        sb = next((r for r in rows if r["repo"] == x["repo"]), {})
        print("  %-28s baseline migrated %s/%s, tests %s -> %s, diff %s lines, $%s | router-migrate migrated %s/%s, tests %s -> %s, diff %s" % (
            name, x["summary"]["migrated"], x["summary"]["sites_found"], x.get("tests_before"), x.get("tests_after"), x.get("diff_lines"),
            x["agent"].get("cost_usd"), sb.get("migrated"), sb.get("sites_found"), sb.get("tests_before"), sb.get("tests_after"), sb.get("diff_lines")))
