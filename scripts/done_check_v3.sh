#!/usr/bin/env bash
# v3 Track 1 definition of done. Prints DONE_CHECK_V3: PASS or lists failures.
cd "$(dirname "$0")/.."
fail=0; f(){ echo "FAIL: $*"; fail=1; }
bash scripts/done_check.sh >/tmp/rm_v1.txt 2>&1 || { f "v1 done_check failed"; tail -5 /tmp/rm_v1.txt; }
bash scripts/done_check_v2.sh >/tmp/rm_v2.txt 2>&1 || { f "v2 done_check failed"; tail -5 /tmp/rm_v2.txt; }
help="$(bin/router-migrate --help 2>&1)"
for c in run check wrap claim finish; do echo "$help" | grep -qw "$c" || f "CLI help lacks '$c'"; done
for p in SPEC_V3.md eval/v3/REPOS.json eval/v3/results.json eval/v3/RESULTS.md router_migrate/prompts/migrate.md; do
  [ -s "$p" ] || f "missing: $p"; done
[ "$(ls eval/v3/inventory/*.json 2>/dev/null | wc -l)" -ge 10 ] || f "need >=10 frozen inventories in eval/v3/inventory/"
[ -n "$(ls eval/v3/smoke 2>/dev/null)" ] || f "no headless agent smoke run in eval/v3/smoke/"
inv=$(git log --reverse --format=%ct -- eval/v3/inventory | head -1)
runc=$(git log --reverse --format=%ct -- eval/v3/runs/C | head -1)
[ -n "$inv" ] && [ -n "$runc" ] && [ "$inv" -lt "$runc" ] || f "inventories must be committed before any arm C run"
grep -qi "v3" WRITEUP.md || f "WRITEUP.md has no v3 section"
python3 - <<'PY' || fail=1
import json,sys
d=json.load(open("eval/v3/results.json")); rows=d.get("repos",[])
if len({r.get("repo") for r in rows})<10: print("FAIL: results.json covers fewer than 10 repos"); sys.exit(1)
need={"repo","commit","arm","sites_moved","broken","usd","minutes"}
bad=[r.get("repo") for r in rows if not need<=set(r)]
if bad: print("FAIL: rows missing keys:",bad[:5]); sys.exit(1)
arms={r["arm"] for r in rows}
if not {"A","B_prime","C"}<=arms: print("FAIL: arms present",sorted(arms),"need A, B_prime, C"); sys.exit(1)
print("results OK:",len(rows),"rows, arms",sorted(arms))
PY
[ $fail = 0 ] && echo "DONE_CHECK_V3: PASS" || echo "DONE_CHECK_V3: FAIL"
exit $fail
