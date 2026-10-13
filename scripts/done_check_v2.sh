#!/usr/bin/env bash
# MVP definition of done for v2. Prints DONE_CHECK_V2: PASS or lists failures.
cd "$(dirname "$0")/.."
fail=0; f(){ echo "FAIL: $*"; fail=1; }
bash scripts/done_check.sh >/tmp/rm_v1.txt 2>&1 || { f "v1 done_check failed"; tail -5 /tmp/rm_v1.txt; }
for p in SPEC_V2.md research/m0_measurements.md eval/scoreboard_v2.json eval/SCOREBOARD_V2.md; do [ -s "$p" ] || f "missing: $p"; done
grep -q "^## D11" DECISIONS.md || f "DECISIONS.md has no D11 (M0 findings)"
bin/router-migrate --help 2>&1 | grep -q "up" && bin/router-migrate --help 2>&1 | grep -q "decide" && bin/router-migrate --help 2>&1 | grep -q "status" || f "CLI help lacks up/decide/status"
[ "$(ls eval/reports/*/REPORT.html 2>/dev/null | wc -l)" -ge 15 ] || f "need eval/reports/<repo>/REPORT.html for 15 repos"
[ "$(ls eval/screenshots/*.png 2>/dev/null | wc -l)" -ge 3 ] || f "need >=3 dashboard screenshots in eval/screenshots/"
python3 - <<'PY' || fail=1
import json,sys
d=json.load(open("eval/scoreboard_v2.json")); t=d["totals"]; r=d["repos"]
need={"lossless_migrated","migrated_with_unlocks","repos_tests_worse_lossless","repos_tests_worse_unlocks","cards_total","live_spend_usd"}
if not need<=set(t): print("FAIL: totals missing",need-set(t)); sys.exit(1)
if len(r)<15: print("FAIL: scoreboard_v2 has",len(r),"repos"); sys.exit(1)
v1=json.load(open("eval/scoreboard.json"))["totals"]["migrated"]
if t["lossless_migrated"]!=v1: print("FAIL: lossless_migrated",t["lossless_migrated"],"!= v1",v1); sys.exit(1)
if t["repos_tests_worse_lossless"] or t["repos_tests_worse_unlocks"]: print("FAIL: tests got worse"); sys.exit(1)
if t["live_spend_usd"]>5: print("FAIL: live spend over $5"); sys.exit(1)
if any(x.get("model_cards_on_non_owned_sites",0) for x in r): print("FAIL: model cards on non-owned sites"); sys.exit(1)
print("scoreboard_v2 OK: lossless",t["lossless_migrated"],"with unlocks",t["migrated_with_unlocks"],"cards",t["cards_total"])
PY
[ $fail = 0 ] && echo "DONE_CHECK_V2: PASS" || echo "DONE_CHECK_V2: FAIL"
exit $fail
