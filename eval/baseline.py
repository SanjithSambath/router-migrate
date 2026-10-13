#!/usr/bin/env python3
"""Baseline: the migration prompt from https://docs.router.com/getting-started/connect, run by Claude Code (`claude -p`)
on a fresh copy of each repo, then scored exactly like router-migrate (same scanner/planner, same recipe tests).

The agent gets file tools only (Read/Edit/Write/Glob/Grep): no shell, no network, no Router key. That matches what our CLI
does at apply time (it does not call Router either). Writes eval/baseline.json and eval/BASELINE.md.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
from router_migrate import cli  # noqa: E402
import run_eval as RE  # noqa: E402

PROMPT = open(os.path.join(ROOT, "research", "baseline_prompt.txt")).read()
DEFAULT = ["swarm", "shell_gpt", "llm-anthropic", "opencommit", "aicommits", "langchain-nextjs-template"]


def run_agent(src, timeout):
    cmd = ["claude", "-p", PROMPT, "--allowedTools", "Read,Edit,Write,Glob,Grep,MultiEdit", "--permission-mode", "acceptEdits",
           "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    t = time.time()
    try:
        r = subprocess.run(cmd, cwd=src, env=env, capture_output=True, text=True, timeout=timeout)
        out = r.stdout
        try:
            meta = json.loads(out)
        except ValueError:
            meta = {"raw": out[-2000:], "stderr": r.stderr[-2000:]}
        return {"exit": r.returncode, "seconds": round(time.time() - t, 1), "cost_usd": meta.get("total_cost_usd"),
                "turns": meta.get("num_turns"), "result": (meta.get("result") or "")[-3000:]}
    except subprocess.TimeoutExpired:
        return {"exit": None, "seconds": timeout, "result": "TIMEOUT"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(DEFAULT))
    ap.add_argument("--timeout", type=int, default=1500)
    args = ap.parse_args()
    repos = {r["name"]: r for r in json.load(open(os.path.join(ROOT, "eval", "repos.json")))}
    outp = os.path.join(ROOT, "eval", "baseline.json")
    results = json.load(open(outp)) if os.path.exists(outp) else {}
    for name in args.only.split(","):
        repo = repos[name]
        rp = os.path.join(ROOT, "eval", "recipes", name + ".json")
        rec = json.load(open(rp)) if os.path.exists(rp) else {"name": name}
        rec["name"] = name
        print("== baseline %s" % name, flush=True)
        src = RE.prepare(repo, rec)
        dst = os.path.join(RE.WORK, "baseline", name)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        src = dst
        runnable = bool(rec.get("test_cmd")) and (rec.get("baseline") or {}).get("exit") is not None
        before = RE.tests(rec, src, "baseline_before") if runnable else None
        agent = run_agent(src, args.timeout)
        diff = subprocess.run("git -C %s add -A -N . && git -C %s diff --stat | tail -1 && git -C %s diff | grep -c '^[+-][^+-]'" % (src, src, src),
                              shell=True, capture_output=True, text=True).stdout.strip().splitlines()
        after = RE.tests(rec, src, "baseline_after") if runnable else None
        tc = RE.typecheck(rec, src, "baseline_typecheck_after")
        cli.do_scan(src, quiet=True)
        p = cli.do_plan(src, quiet=True)
        sm = p["summary"]
        subprocess.run("git -C %s diff > %s" % (src, os.path.join(RE.LOGS, name + ".baseline.diff")), shell=True)
        results[name] = {"repo": repo["repo"], "agent": agent, "summary": sm, "diff_lines": int(diff[-1]) if diff and diff[-1].isdigit() else None,
                         "tests_before": RE.count(before) if before else None, "tests_after": RE.count(after) if after else None,
                         "typecheck_after": tc,
                         "sites": [{k: s.get(k) for k in ("file", "line", "sdk", "surface", "outcome", "reason")} for s in p["sites"] if s["prod"]]}
        json.dump(results, open(outp, "w"), indent=1)
        print("   %s" % json.dumps({"migrated": sm["migrated"], "sites": sm["sites_found"], "before": results[name]["tests_before"],
                                    "after": results[name]["tests_after"], "agent_s": agent["seconds"], "cost": agent.get("cost_usd")}), flush=True)


if __name__ == "__main__":
    main()
