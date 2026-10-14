#!/usr/bin/env python3
"""Tool-use analysis from agent transcripts (Anthropic tool-eval practice): per arm and repo, how many router-migrate calls by
command, how many returned an error, how often the agent ran the test suite itself, total tool calls and turns.
Writes eval/v3/tool_usage.json."""
import glob
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
rows = []
for tr in sorted(glob.glob(os.path.join(HERE, "runs", "*", "*", "transcript.jsonl.gz"))):
    arm, repo = tr.split(os.sep)[-3], tr.split(os.sep)[-2]
    calls, errors, tests, tools, edits = {}, {}, 0, 0, 0
    pending = {}
    for line in gzip.open(tr, "rt", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                tools += 1
                name, inp = part.get("name"), part.get("input") or {}
                if name in ("Edit", "MultiEdit", "Write"):
                    edits += 1
                if name == "Bash":
                    cmd = inp.get("command", "")
                    m = re.search(r"(?:^|[\s;&|(]|/bin/)router-migrate\s+(run|check|wrap|claim|finish|scan|plan|apply|verify|report)\b", cmd)
                    if m:
                        calls[m.group(1)] = calls.get(m.group(1), 0) + 1
                        pending[part.get("id")] = m.group(1)
                    if re.search(r"pytest|jest|vitest|npm (run )?test|sim_test\.py|tsc --noEmit", cmd):
                        tests += 1
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in pending:
                text = json.dumps(part.get("content"))
                if "error:" in text or '\\"ok\\": false' in text:
                    c = pending[part["tool_use_id"]]
                    errors[c] = errors.get(c, 0) + 1
    rows.append({"arm": arm, "repo": repo, "tool_calls": tools, "edit_calls": edits, "router_migrate_calls": calls,
                 "router_migrate_errors": errors, "agent_test_runs": tests})
json.dump(rows, open(os.path.join(HERE, "tool_usage.json"), "w"), indent=1)
for arm in ("B", "B_prime", "C"):
    rs = [r for r in rows if r["arm"] == arm]
    if not rs:
        continue
    tot = {}
    for r in rs:
        for k, v in r["router_migrate_calls"].items():
            tot[k] = tot.get(k, 0) + v
    err = {}
    for r in rs:
        for k, v in r["router_migrate_errors"].items():
            err[k] = err.get(k, 0) + v
    print("%-8s repos=%d tool_calls=%d edits=%d test_runs=%d rm_calls=%s rm_errors=%s" % (
        arm, len(rs), sum(r["tool_calls"] for r in rs), sum(r["edit_calls"] for r in rs), sum(r["agent_test_runs"] for r in rs), tot, err))
