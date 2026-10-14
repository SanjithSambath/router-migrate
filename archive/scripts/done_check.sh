#!/usr/bin/env bash
# Deterministic definition of done. Prints DONE_CHECK: PASS or lists failures.
cd "$(dirname "$0")/.."
fail=0; f(){ echo "FAIL: $*"; fail=1; }
[ -d .git ] || f "not a git repo"
for p in README.md ARCHITECTURE.md DECISIONS.md PROGRESS.md WRITEUP.md AGENTS.md Makefile \
         .claude/skills/router-migrate/SKILL.md eval/SCOREBOARD.md eval/scoreboard.json eval/scoreboard.html; do
  [ -s "$p" ] || f "missing or empty: $p"; done
make -s cli-help >/tmp/rm_help.txt 2>&1 && grep -qiE "scan|plan|apply|verify|report" /tmp/rm_help.txt || f "make cli-help failed or help lacks subcommands"
make -s test >/tmp/rm_test.txt 2>&1 || { f "make test failed"; tail -20 /tmp/rm_test.txt; }
python3 - <<'PY' || fail=1
import json,sys
need={"repo","commit","sites_found","migrated","handoff","kept","flagged","callsite_coverage","weighted_coverage","tests_before","tests_after","sim_valid_pct","diff_lines","runtime_s"}
try: d=json.load(open("eval/scoreboard.json"))
except Exception as e: print("FAIL: scoreboard.json unreadable:",e); sys.exit(1)
rows=d if isinstance(d,list) else d.get("repos",[])
bad=[r.get("repo") for r in rows if not need<=set(r)]
if len(rows)<15: print("FAIL: scoreboard has %d repos, need 15"%len(rows)); sys.exit(1)
if bad: print("FAIL: rows missing keys:",bad); sys.exit(1)
print("scoreboard: %d repos OK"%len(rows))
PY
git grep -qI "sk-routgw" -- . ':!scripts/done_check.sh' && f "API key string found in repo"
grep -qiE "TODO|TBD|lorem" WRITEUP.md README.md 2>/dev/null && f "TODO/TBD left in WRITEUP.md or README.md"
[ -z "$(git status --porcelain)" ] || f "uncommitted changes"
[ $fail = 0 ] && echo "DONE_CHECK: PASS" || echo "DONE_CHECK: FAIL"
exit $fail
