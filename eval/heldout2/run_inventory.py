#!/usr/bin/env python3
"""Run the blind inventory agent (headless `claude -p`) for heldout2 repos, one after another, and record a DONE line each.

  nohup .venv/bin/python eval/heldout2/run_inventory.py <name> [<name> ...] > eval/heldout2/inventory/logs/<name>.out 2>&1 &

Same invocation as eval/v3/run_v3.py run_agent(): stream-json transcript, --dangerously-skip-permissions, CLI default model
(override with RM_AGENT_MODEL), the OAuth token from ~/.config/claude/oauth_token, RAMP_ROUTER_API_KEY/ANTHROPIC_API_KEY removed.
cwd = the pristine clone. Task: eval/heldout2/INVENTORY_TASK.md (Part A of eval/v3/AGENT_TASK.md + a `production` field).
"""
import collections
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WORK = os.path.join(ROOT, "eval", "work", "heldout2")
INV = os.path.join(HERE, "inventory")
LOGS = os.path.join(INV, "logs")
TOKEN_FILE = os.path.expanduser("~/.config/claude/oauth_token")
MODEL = os.environ.get("RM_AGENT_MODEL", "")
TIMEOUT = int(os.environ.get("RM_INV_TIMEOUT", "5400"))


def agent_env():
    e = {k: v for k, v in os.environ.items() if k not in ("RAMP_ROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    if os.path.exists(TOKEN_FILE):
        e["CLAUDE_CODE_OAUTH_TOKEN"] = open(TOKEN_FILE).read().strip()
    e["GIT_CEILING_DIRECTORIES"] = WORK
    return e


def run(name):
    repo = next(r for r in json.load(open(os.path.join(HERE, "REPOS.json"))) if r["name"] == name)
    prompt = open(os.path.join(HERE, "INVENTORY_TASK.md")).read().format(name=name, repo=repo["repo"], commit=repo["commit"])
    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(os.path.join(WORK, "scratch", name), exist_ok=True)
    out = os.path.join(INV, name + ".json")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions",
           "--max-turns", "250", "--disallowedTools", "Skill"]
    if MODEL:
        cmd += ["--model", MODEL]
    log = os.path.join(LOGS, name + ".transcript.jsonl")
    t = time.time()
    with open(log, "w") as fh:
        try:
            code = subprocess.run(cmd, cwd=os.path.join(WORK, "pristine", name), env=agent_env(), stdout=fh,
                                  stderr=subprocess.STDOUT, text=True, timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    mins = round((time.time() - t) / 60, 1)
    res = {}
    for line in open(log, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            res = ev
    stray = out.replace(ROOT, os.path.join(os.path.dirname(ROOT), "router-migrate"))  # agent resolved R through the eval/work symlink
    if not os.path.exists(out) and stray != out and os.path.exists(stray):
        os.replace(stray, out)
    status, summary = "FAILED", ""
    try:
        d = json.load(open(out))
        sites = d["sites"]
        prod = [s for s in sites if s.get("prod")]
        c = collections.Counter(s.get("expected") for s in prod)
        status = "ok"
        summary = "sites=%d prod=%d %s nonprod=%d" % (len(sites), len(prod), json.dumps(dict(sorted(c.items()))),
                                                     len(sites) - len(prod))
    except Exception as ex:  # noqa: BLE001
        summary = "no valid inventory: %r" % ex
    json.dump({"exit": code, "usd": res.get("total_cost_usd"), "turns": res.get("num_turns"), "minutes": mins,
               "model": list((res.get("modelUsage") or {}).keys()), "final": (res.get("result") or "")[:4000]},
              open(os.path.join(LOGS, name + ".result.json"), "w"), indent=1)
    with open(os.path.join(INV, "DONE.txt"), "a") as fh:
        fh.write("%s\t%s\t%s\texit=%s usd=%s min=%s\t%s\n" % (name, status, summary, code, res.get("total_cost_usd"), mins,
                                                             time.strftime("%Y-%m-%dT%H:%M:%S")))


if __name__ == "__main__":
    for n in sys.argv[1:]:
        run(n)
