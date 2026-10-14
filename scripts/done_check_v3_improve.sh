#!/usr/bin/env bash
# v3 Improve phase: definition of done. Prints DONE_CHECK_V3_IMPROVE: PASS or lists failures.
cd "$(dirname "$0")/.."
fail=0; f(){ echo "FAIL: $*"; fail=1; }
bash scripts/done_check_v3.sh >/tmp/rm_v3.txt 2>&1 || { f "v3 done_check failed"; tail -5 /tmp/rm_v3.txt; }
make -s test >/tmp/rm_test.txt 2>&1 || { f "make test failed"; tail -10 /tmp/rm_test.txt; }
[ -s eval/v3/IMPROVE_RESULTS.md ] || f "missing eval/v3/IMPROVE_RESULTS.md"
repos=$(python3 -c "import json;d=json.load(open('eval/v3/REPOS.json'));r=d if isinstance(d,list) else d.get('repos',d);print(len(r))" 2>/dev/null)
[ "$(ls eval/v3/improve/*/REPORT.html 2>/dev/null | wc -l)" -ge "${repos:-10}" ] || f "need eval/v3/improve/<repo>/REPORT.html for every fresh repo"
[ "$(ls eval/v3/improve/*/recommendations.json 2>/dev/null | wc -l)" -ge "${repos:-10}" ] || f "need eval/v3/improve/<repo>/recommendations.json for every fresh repo"
[ "$(ls eval/v3/screenshots/*.png 2>/dev/null | wc -l)" -ge 4 ] || f "need >=4 v3 dashboard screenshots in eval/v3/screenshots/"
grep -qi "improve" WRITEUP.md || f "WRITEUP.md has no Improve section"
[ -z "$(git status --porcelain)" ] || f "uncommitted changes"
[ $fail = 0 ] && echo "DONE_CHECK_V3_IMPROVE: PASS" || echo "DONE_CHECK_V3_IMPROVE: FAIL"
exit $fail
