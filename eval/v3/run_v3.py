#!/usr/bin/env python3
"""v3 eval: three arms on the fresh repo set, scored the same way against frozen blind inventories.

  A        rules only: `router-migrate run` (the deterministic floor), no agent
  B_prime  a Claude agent + Router's docs migration prompt (research/baseline_prompt.txt, verbatim) + the same abilities as C:
           the repo's test command and a Router-on test run against router-sim
  C        a Claude agent + the v3 toolkit: starts from arm A's floor, router_migrate/prompts/migrate.md, the check/wrap/claim/
           finish primitives, and a Stop hook that runs `router-migrate finish`

  .venv/bin/python eval/v3/run_v3.py trial A llmcord           # one trial
  .venv/bin/python eval/v3/run_v3.py repo llmcord              # A, then B_prime and C (sequential: some recipes share an env)
  .venv/bin/python eval/v3/run_v3.py all --jobs 4              # every repo, repos in parallel
  .venv/bin/python eval/v3/run_v3.py aggregate                 # eval/v3/results.json + RESULTS.md

Scoring (identical for every arm): after the trial, (1) the recipe's tests and typecheck vs the baseline measured in the same
harness: any regression = broken; (2) router-migrate's scanner + planner in measure-only mode on the result (only traffic that
actually goes to Router counts: wrapped clients or clients pointed at Router), lines mapped back to the pristine source and
matched to the frozen inventory (same matcher as the held-out audit); (3) the arm's *reported* outcome per site (A: the plan;
C: the plan overridden by the agent's ledger claims; B_prime: what its code does) against the inventory; (4) $ and minutes.
Trials run with no Router key (router-sim only), RAMP_ROUTER_API_KEY unset, and a per-trial copy with fresh git history.
"""
import argparse
import difflib
import glob
import gzip
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
import run_eval as RE  # noqa: E402

RE.SETDIR, RE.WORK = RE.SETS["v3"]
RE.LOGS = os.path.join(RE.WORK, "logs")
os.makedirs(RE.LOGS, exist_ok=True)
import heldout_audit as HA  # noqa: E402
from router_migrate import plan as planmod, scan_py  # noqa: E402
from router_migrate.catalog import Catalog  # noqa: E402

RUNS = os.path.join(HERE, "runs")
TRIALS = os.path.join(RE.WORK, "trials")
RM = os.path.join(ROOT, "bin", "router-migrate")
ARMS = ("A", "B_prime", "C")
AGENT_TIMEOUT = int(os.environ.get("RM_AGENT_TIMEOUT", 2700))
MODEL = os.environ.get("RM_AGENT_MODEL", "")  # empty: the CLI's default model (recorded per trial)
TOKEN_FILE = os.path.expanduser("~/.config/claude/oauth_token")


def repos():
    return {r["name"]: r for r in json.load(open(os.path.join(HERE, "REPOS.json")))}


def recipe(name):
    r = json.load(open(os.path.join(HERE, "recipes", name + ".json")))
    r["name"] = name
    return r


def sh(cmd, cwd=ROOT, env=None, timeout=1800):
    return RE.sh(cmd, cwd=cwd, env=env, timeout=timeout)


# ------------------------------------------------------------------------------------------------ trial copies
def fresh_copy(name, arm):
    """Pinned source + prepared dependencies, with fresh git history (no upstream log for the agent to read)."""
    rp = repos()[name]
    rec = recipe(name)
    src = RE.prepare(rp, rec)  # eval/work/v3/runs/<name>: clone + node_modules / editable reinstall
    dst = os.path.join(TRIALS, arm, name)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(src), shlex.quote(dst)) * 2))
    shutil.rmtree(os.path.join(dst, ".git"), ignore_errors=True)
    _git_base(dst)
    _reinstall(rec, dst)
    return dst, rec


def _git_base(dst):
    sh("git init -q && printf 'node_modules/\\n.next/\\n__pycache__/\\n' >> .git/info/exclude && git add -A && "
       "git -c user.email=eval@rm -c user.name=eval commit -qm base", cwd=dst)


def _reinstall(rec, src):
    if rec.get("lang") == "ts":
        return
    cmds = rec.get("reinstall") or [c if "--no-deps" in c else c + " --no-deps" for c in rec.get("setup", []) if " -e " in c and "$SRC" in c]
    for c in cmds:
        sh(RE.subst(c, rec, src), timeout=900)


def test_cmd(rec, src):
    cmd = RE.subst(rec["test_cmd"], rec, src)
    env = {k: RE.subst(str(v), rec, src) for k, v in (rec.get("test_env") or {}).items()}
    pre = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in env.items())
    return ("env %s %s" % (pre, cmd)) if pre else cmd


def typecheck_cmd(rec, src):
    return RE.subst(rec["typecheck_cmd"], rec, src) if rec.get("typecheck_cmd") else None


# ------------------------------------------------------------------------------------------------ scoring
def measure(dst, name):
    """Prod sites that route to Router in the code as it is now, with lines mapped back to the pristine source."""
    scan = scan_py.scan(dst)
    sites = list(scan["sites"])
    from router_migrate import cli
    if cli.node_ok():
        try:
            sites += cli.run_ts(["scan", dst]).get("sites", [])
        except Exception:
            pass
    p = planmod.build({"sites": sites, "root": dst}, Catalog.load(), measure_only=True)
    pristine = os.path.join(RE.WORK, "pristine", name)
    maps = {}
    out = []
    for s in p["sites"]:
        if not s.get("prod"):
            continue
        f = s["file"]
        if f not in maps:
            maps[f] = line_map(os.path.join(pristine, f), os.path.join(dst, f))
        m = maps[f]
        out.append(dict(s, line=m(int(s["line"])) if m else int(s["line"]), line_now=int(s["line"])))
    return out


def line_map(old_path, new_path):
    """f(new_line) -> old_line for one file (insertions map to the nearest preceding original line)."""
    try:
        old = open(old_path, encoding="utf-8", errors="replace").read().splitlines()
        new = open(new_path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return None
    if old == new:
        return lambda n: n
    m = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for k in range(j2 - j1):
            m[j1 + k + 1] = (i1 + k + 1) if tag in ("equal", "replace") and i1 + k < i2 else max(i1, 1)
    return lambda n: m.get(n, n)


def inventory(name):
    d = json.load(open(os.path.join(HERE, "inventory", name + ".json")))
    return [s for s in d["sites"] if s.get("prod")]


def score(name, measured, reported):
    """Routed sites vs the inventory, and the arm's reported outcomes vs the inventory."""
    inv = inventory(name)
    # credited as moved: the code routes it (measured) AND the arm reports it migrated (a site the agent says stays on its
    # provider in production, e.g. a config base URL to another vendor, is not claimed as moved even if a harmless wrap remains)
    rep_mig = [r for r in reported if r.get("outcome") == "migrated"]
    moved = [s for s in measured if s["outcome"] == "migrated" and
             any(r["file"] == s["file"] and abs(int(r["line"]) - int(s["line"])) <= 3 for r in rep_mig)]
    pairs, missed, extra = HA.match(inv, moved)
    good = {"migrated", "handoff"}
    correct = [(i, p) for i, p in pairs if i["expected"] in good]
    wrong = [(i, p) for i, p in pairs if i["expected"] not in good]
    expected_movable = [i for i in inv if i["expected"] == "migrated"]
    # reported outcome accuracy (all inventory prod sites the report covers)
    rpairs, _, _ = HA.match(inv, reported)
    agree = sum(1 for i, p in rpairs if _same_bucket(i["expected"], p["outcome"]))
    rep_mig = [(i, p) for i, p in rpairs if p["outcome"] == "migrated"]
    return {"routed_static": sum(1 for s in measured if s["outcome"] == "migrated"),
        "inventory_prod_sites": len(inv), "inventory_migrated": len(expected_movable),
        "inventory_handoff": sum(1 for i in inv if i["expected"] == "handoff"),
        "sites_moved": len(moved), "sites_moved_correct": len(correct), "sites_moved_wrong": len(wrong),
        "sites_moved_unmatched": len(extra),
        "moved_recall": round(len([1 for i, _ in correct if i["expected"] == "migrated"]) / len(expected_movable), 3) if expected_movable else None,
        "moved_precision": round(len(correct) / len(moved), 3) if moved else None,
        "reported_sites_matched": len(rpairs), "reported_outcome_agreement": round(agree / len(rpairs), 3) if rpairs else None,
        "reported_migrated": len(rep_mig), "reported_migrated_correct": sum(1 for i, _ in rep_mig if i["expected"] in good),
        "wrong_detail": [{"file": i["file"], "line": i["line"], "expected": i["expected"], "why": i.get("why", "")[:160]} for i, _ in wrong],
        "missed_migrated": [{"file": i["file"], "line": i["line"], "why": i.get("why", "")[:160]} for i in expected_movable
                            if not any(i is a for a, _ in correct)],
    }


def _same_bucket(expected, outcome):
    if expected == outcome:
        return True
    return expected == "nonprod" and outcome in ("not_a_site", "kept")


def broken(before, after, tc_before, tc_after, kind):
    if kind == "typecheck" or (before or {}).get("passed") is None:
        b = (tc_before or before or {})
        a = (tc_after or after or {})
        be, ae = b.get("errors", b.get("ts_errors")) or 0, a.get("errors", a.get("ts_errors")) or 0
        return ae > be or (bool(a.get("exit")) and not b.get("exit"))
    from router_migrate.agent import _worse
    if _worse(before, after or {}):
        return True
    if tc_before is not None and tc_after is not None:
        return (tc_after.get("errors") or 0) > (tc_before.get("errors") or 0)
    return False


# ------------------------------------------------------------------------------------------------ arms
def baseline(name):
    """Tests and typecheck on the untouched copy, once per repo, in this harness."""
    p = os.path.join(RUNS, "baseline", name + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    dst, rec = fresh_copy(name, "baseline")
    t = RE.tests(rec, dst, "v3_before")
    tc = RE.typecheck(rec, dst, "v3_tc_before")
    b = {"repo": name, "tests": RE.count(t, rec.get("test_kind", "unit")) or {"exit": t.get("exit"), "ts_errors": t.get("ts_errors")},
         "typecheck": tc, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(b, open(p, "w"), indent=1)
    return b


def agent_env():
    e = {k: v for k, v in os.environ.items() if k not in ("RAMP_ROUTER_API_KEY", "ANTHROPIC_API_KEY")}
    if os.path.exists(TOKEN_FILE):
        e["CLAUDE_CODE_OAUTH_TOKEN"] = open(TOKEN_FILE).read().strip()
    e["PATH"] = os.path.join(ROOT, "bin") + os.pathsep + e.get("PATH", "")
    RE.contain(e)
    return e


def run_agent(dst, prompt, settings, out_dir):
    """Headless Claude Code in the trial copy. Returns (result dict, wall seconds)."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions",
           "--max-turns", "250"]
    if MODEL:
        cmd += ["--model", MODEL]
    if settings:
        cmd += ["--settings", json.dumps(settings)]
    t = time.time()
    log = os.path.join(out_dir, "transcript.jsonl")
    with open(log, "w") as fh:
        try:
            p = subprocess.run(cmd, cwd=dst, env=agent_env(), stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=AGENT_TIMEOUT)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    wall = time.time() - t
    result = {}
    for line in open(log, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    return {"exit": code, "is_error": result.get("is_error"), "usd": result.get("total_cost_usd"), "turns": result.get("num_turns"),
            "final": (result.get("result") or "")[:4000], "model": list((result.get("modelUsage") or {}).keys()),
            "usage": result.get("usage"), "terminal_reason": result.get("terminal_reason")}, wall


def trial(arm, name):
    """Run one arm on one repo (the expensive part), then score it."""
    rec = recipe(name)
    baseline(name)
    out_dir = os.path.join(RUNS, arm, name)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    agent = None
    if arm == "C":
        a_dir = os.path.join(TRIALS, "A", name)
        if not os.path.exists(os.path.join(a_dir, ".router-migrate", "ledger.json")):
            trial("A", name)
        dst = os.path.join(TRIALS, "C", name)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(a_dir), shlex.quote(dst)) * 2))
        _reinstall(rec, dst)
        # the product path: `router-migrate run --agent claude` (floor already in place, so run only launches the agent + gate)
        cmd = [RM, "run", dst, "--agent", "claude", "--agent-timeout", str(AGENT_TIMEOUT)] + (["--model", MODEL] if MODEL else [])
        p = subprocess.run(cmd, env=agent_env(), capture_output=True, text=True, timeout=AGENT_TIMEOUT + 3600)
        open(os.path.join(out_dir, "run_agent.log"), "w").write((p.stdout or "")[-20000:] + (p.stderr or "")[-5000:])
        L = json.load(open(os.path.join(dst, ".router-migrate", "ledger.json")))
        cost = L.get("cost") or {}
        tr = os.path.join(dst, ".router-migrate", "agent_transcript.jsonl")
        final = ""
        if os.path.exists(tr):
            for line in open(tr, encoding="utf-8", errors="replace"):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    final = (ev.get("result") or "")[:4000]
            shutil.move(tr, os.path.join(out_dir, "transcript.jsonl"))
        agent = {"exit": cost.get("agent_exit"), "usd": cost.get("agent_usd"), "turns": cost.get("agent_turns"), "model": cost.get("agent_model"),
                 "final": final, "finish_exit": p.returncode}
    else:
        dst, rec = fresh_copy(name, arm)
        if arm == "A":
            cmd = "%s run %s --test-cmd %s" % (RM, shlex.quote(dst), shlex.quote(test_cmd(rec, dst)))
            if typecheck_cmd(rec, dst):
                cmd += " --typecheck-cmd %s" % shlex.quote("cd %s && %s" % (shlex.quote(dst), typecheck_cmd(rec, dst)))
            code, o = sh(cmd, timeout=3600)
            open(os.path.join(out_dir, "run.log"), "w").write(o[-20000:])
            sh("git add -A && git -c user.email=eval@rm -c user.name=eval commit -qm floor", cwd=dst)
            # the floor commit keeps the ledger's snapshot tree reachable for arm C's copy
        else:
            docs = open(os.path.join(ROOT, "research", "baseline_prompt.txt")).read().strip()
            tc = typecheck_cmd(rec, dst)
            prompt = open(os.path.join(HERE, "prompt_B_prime.md")).read().replace("{DOCS_PROMPT}", docs).replace(
                "{TEST_CMD}", test_cmd(rec, dst)).replace("{TYPECHECK_LINE}", ("- Typecheck command: %s\n" % tc) if tc else "").replace(
                "{SIM_CMD}", "%s %s %s %s" % (os.path.join(ROOT, ".venv", "bin", "python"), os.path.join(HERE, "sim_test.py"), shlex.quote(dst),
                                             shlex.quote(test_cmd(rec, dst))))
            agent, wall = run_agent(dst, prompt, None, out_dir)
    json.dump({"agent": agent, "minutes": round((time.time() - t0) / 60, 1)}, open(os.path.join(out_dir, "agent.json"), "w"), indent=1, default=str)
    return score_trial(arm, name)


def independent_check(name, dst, rec):
    """Breakage checks the agent's gate never runs: (1) import smoke of every production Python module the arm changed,
    compared with the same import on the pristine source; `node --check` on changed JS files; (2) held-back replay: every
    site the code now routes, rebuilt from its literal arguments and sent through the vendored runtime to router-sim."""
    pristine = os.path.join(RE.WORK, "pristine", name)
    py = os.path.join(RE.WORK, "envs", name, "bin", "python")
    changed = subprocess.run("git diff --name-only $(git rev-list --max-parents=0 HEAD) -- . ; git ls-files --others --exclude-standard",
                             cwd=dst, shell=True, capture_output=True, text=True).stdout.split()
    changed = sorted({f for f in changed if scan_py.is_prod(f) and not f.startswith(".router-migrate") and
                      not os.path.basename(f) in ("router_runtime.py", "router_policy.py", "routerRuntime.ts")})
    env = dict(os.environ, OPENAI_API_KEY="sk-dummy", ANTHROPIC_API_KEY="sk-ant-dummy", GROQ_API_KEY="x", DEEPSEEK_API_KEY="x",
               TELEGRAM_BOT_TOKEN="x", DISCORD_BOT_TOKEN="x", PYTHONDONTWRITEBYTECODE="1")
    env.pop("RAMP_ROUTER_API_KEY", None)
    regressed, checked = [], 0
    for f in changed:
        if f.endswith(".py") and os.path.exists(os.path.join(dst, f)) and os.path.exists(py):
            root_dir, mod = _module_of(dst, f)
            prog = "import importlib, sys; sys.path.insert(0, %r); importlib.import_module(%r)"
            res = []
            for base in (dst, pristine):
                if not os.path.exists(os.path.join(base, f)):
                    res.append(True)
                    continue
                rd = os.path.join(base, os.path.relpath(root_dir, dst))
                try:
                    r = subprocess.run([py, "-c", prog % (rd, mod)], cwd=rd, env=env, capture_output=True, text=True, timeout=90)
                    res.append(r.returncode == 0)
                except subprocess.TimeoutExpired:
                    res.append(None)
            checked += 1
            if res[0] is False and res[1] is True:
                regressed.append({"file": f, "check": "import"})
        elif f.endswith((".js", ".mjs", ".cjs")) and os.path.exists(os.path.join(dst, f)):
            checked += 1
            if subprocess.run(["node", "--check", os.path.join(dst, f)], capture_output=True).returncode:
                regressed.append({"file": f, "check": "node --check"})
    # held-back replay through the vendored runtime against router-sim
    from router_migrate import verify as V
    from router_migrate.sim.server import serve
    replay = None
    try:
        scan = scan_py.scan(dst)
        p = planmod.build({"sites": scan["sites"], "root": dst}, Catalog.load(), measure_only=True)
        srv, sim, base = serve()
        try:
            replay = V.replay(p, base, V.SIM_KEY, Catalog.load())
        finally:
            srv.shutdown()
        replay = {"unique_requests": replay.get("unique_requests"), "ok": replay.get("ok"), "invalid": (replay.get("invalid") or [])[:5]}
    except Exception as e:
        replay = {"error": str(e)[:300]}
    bad_replay = bool(replay and replay.get("unique_requests") and (replay.get("ok") or 0) < replay["unique_requests"])
    return {"import_smoke": {"checked": checked, "regressed": regressed}, "replay": replay, "broken": bool(regressed) or bad_replay}


def _module_of(root, rel):
    parts = rel[:-3].split("/")
    i = len(parts) - 1
    while i > 0 and os.path.exists(os.path.join(root, *parts[:i], "__init__.py")):
        i -= 1
    return os.path.join(root, *parts[:i]) if i else root, ".".join(parts[i:])


def score_trial(arm, name):
    """Score an existing trial copy (re-runnable with `rescore` when the scorer changes; the agent is not re-run)."""
    rec = recipe(name)
    base = baseline(name)
    out_dir = os.path.join(RUNS, arm, name)
    dst = os.path.join(TRIALS, arm, name)
    meta = json.load(open(os.path.join(out_dir, "agent.json"))) if os.path.exists(os.path.join(out_dir, "agent.json")) else {}
    agent, minutes = meta.get("agent"), meta.get("minutes")
    t = RE.tests(rec, dst, "v3_%s_after" % arm)
    tc = RE.typecheck(rec, dst, "v3_%s_tc_after" % arm)
    after = RE.count(t, rec.get("test_kind", "unit")) or {"exit": t.get("exit"), "ts_errors": t.get("ts_errors")}
    gate_broken = broken(base["tests"], after, base.get("typecheck"), tc, rec.get("test_kind", "unit"))
    ind = independent_check(name, dst, rec)
    is_broken = gate_broken or ind["broken"]
    measured = measure(dst, name)
    reported = reported_outcomes(arm, dst, name, measured)
    sc = score(name, measured, reported)
    sh("git add -A", cwd=dst)
    patch = subprocess.run("git diff --no-color --cached $(git rev-list --max-parents=0 HEAD) -- . ':(exclude).router-migrate' "
                           "':(exclude,glob)**/router_runtime.py' ':(exclude,glob)**/routerRuntime.ts' ':(exclude,glob)**/router_policy.py'",
                           cwd=dst, shell=True, capture_output=True, text=True).stdout
    open(os.path.join(out_dir, "diff.patch"), "w").write(patch)
    dl = sum(1 for l in patch.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    test_files = sorted({l[6:] for l in patch.splitlines() if l.startswith("+++ b/") and not scan_py.is_prod(l[6:])})
    leak = False
    tr = os.path.join(out_dir, "transcript.jsonl")
    if os.path.exists(tr):
        txt = open(tr, encoding="utf-8", errors="replace").read()
        leak = any(k in txt for k in ("eval/v3/inventory", "v3/inventory/", "heldout/inventory", "eval/v3/runs", "results.json"))
        with gzip.open(tr + ".gz", "wt") as g:
            g.write(txt)
        os.remove(tr)
    elif os.path.exists(tr + ".gz"):
        txt = gzip.open(tr + ".gz", "rt").read()
        leak = any(k in txt for k in ("eval/v3/inventory", "v3/inventory/", "heldout/inventory", "eval/v3/runs", "results.json"))
    led = os.path.join(dst, ".router-migrate", "ledger.json")
    if os.path.exists(led):
        shutil.copy(led, os.path.join(out_dir, "ledger.json"))
    row = {"repo": name, "commit": repos()[name]["commit"], "arm": arm, "broken": is_broken, "broken_gate_checks": gate_broken,
           "independent_check": ind, "tests_before": base["tests"], "tests_after": after,
           "typecheck_before": base.get("typecheck"), "typecheck_after": tc, "diff_lines": dl, "test_files_edited": test_files,
           "usd": (agent or {}).get("usd") or 0.0, "minutes": minutes, "agent": agent, "inventory_leak_in_transcript": leak,
           "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    row.update(sc)
    if os.path.exists(led):
        L = json.load(open(led))
        row["ledger"] = {"result": L.get("result"), "finish_attempts": (L.get("finish") or {}).get("attempts"),
                         "reverted": bool((L.get("finish") or {}).get("reverted")), "claims": len(L.get("claims") or {}),
                         "worklist": len(L.get("worklist") or [])}
    json.dump(row, open(os.path.join(out_dir, "result.json"), "w"), indent=1, default=str)
    print("%-8s %-30s broken=%s moved=%s (correct %s, wrong %s) usd=%.2f min=%s" % (
        arm, name, is_broken, row["sites_moved"], row["sites_moved_correct"], row["sites_moved_wrong"], row["usd"], minutes), flush=True)
    return row


def reported_outcomes(arm, dst, name, measured):
    """What the arm says happens to each site: A the plan (patch-assuming), C the plan overridden by ledger claims, B' its code."""
    if arm == "B_prime":
        return measured
    plan_p = os.path.join(dst, ".router-migrate", "plan.json")
    if not os.path.exists(plan_p):
        return measured
    pristine = os.path.join(RE.WORK, "pristine", name)
    # plan.json lines refer to the pre-apply (pristine) source
    rep = [dict(s) for s in json.load(open(plan_p))["sites"] if s.get("prod")]
    if arm == "C" and os.path.exists(os.path.join(dst, ".router-migrate", "ledger.json")):
        L = json.load(open(os.path.join(dst, ".router-migrate", "ledger.json")))
        maps = {}
        for c in (L.get("claims") or {}).values():
            f = c["file"]
            if f not in maps:
                maps[f] = line_map(os.path.join(pristine, f), os.path.join(dst, f))
            ln = maps[f](int(c["line"])) if maps[f] else int(c["line"])
            hit = [s for s in rep if s["file"] == f and abs(int(s["line"]) - ln) <= 3]
            outcome = "kept" if c["outcome"] == "not_a_site" else c["outcome"]
            if hit:
                hit[0]["outcome"] = outcome
            elif c["outcome"] not in ("not_a_site",):
                rep.append({"file": f, "line": ln, "surface": "chat", "outcome": outcome, "prod": True})
    return rep


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        import traceback
        print("%s FAILED: %s" % (args, e), flush=True)
        traceback.print_exc()


def run_repo(name, arms=ARMS):
    rows = []
    for arm in arms:
        try:
            rows.append(trial(arm, name))
        except Exception as e:
            import traceback
            print("%s %s FAILED: %s" % (arm, name, e), flush=True)
            traceback.print_exc()
    return rows


# ------------------------------------------------------------------------------------------------ aggregate
def clopper_pearson(k, n, alpha=0.05):
    if n == 0:
        return (None, None)
    from math import comb

    def cdf(x, p):
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, x + 1))

    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else solve(lambda p: 1 - cdf(k - 1, p) < alpha / 2)
    upper = 1.0 if k == n else solve(lambda p: cdf(k, p) > alpha / 2)
    return (round(lower, 3), round(upper, 3))


def aggregate():
    rows = []
    for f in sorted(glob.glob(os.path.join(RUNS, "*", "*", "result.json"))):
        if "/baseline/" in f:
            continue
        rows.append(json.load(open(f)))
    keep = ("repo", "commit", "arm", "sites_moved", "sites_moved_correct", "sites_moved_wrong", "sites_moved_unmatched", "moved_recall",
            "moved_precision", "broken", "usd", "minutes", "diff_lines", "test_files_edited", "inventory_prod_sites", "inventory_migrated",
            "inventory_handoff", "reported_migrated", "reported_migrated_correct", "reported_outcome_agreement", "reported_sites_matched",
            "inventory_leak_in_transcript", "ledger", "wrong_detail", "missed_migrated")
    slim = [{k: r.get(k) for k in keep} | {"model": (r.get("agent") or {}).get("model"), "agent_exit": (r.get("agent") or {}).get("exit"),
                                           "turns": (r.get("agent") or {}).get("turns")} for r in rows]
    arms = {}
    for r in slim:
        a = arms.setdefault(r["arm"], {"repos": 0, "broken": 0, "sites_moved": 0, "sites_moved_correct": 0, "sites_moved_wrong": 0,
                                        "inventory_migrated": 0, "usd": 0.0, "minutes": 0.0, "diff_lines": 0, "reported_migrated": 0,
                                        "reported_migrated_correct": 0, "test_files_edited": 0})
        a["repos"] += 1
        a["broken"] += int(bool(r["broken"]))
        for k in ("sites_moved", "sites_moved_correct", "sites_moved_wrong", "inventory_migrated", "usd", "minutes", "diff_lines",
                  "reported_migrated", "reported_migrated_correct"):
            a[k] += r.get(k) or 0
        a["test_files_edited"] += int(bool(r.get("test_files_edited")))
    for a in arms.values():
        a["broken_ci95"] = clopper_pearson(a["broken"], a["repos"])
        a["moved_precision"] = round(a["sites_moved_correct"] / a["sites_moved"], 3) if a["sites_moved"] else None
        a["reported_migrated_precision"] = round(a["reported_migrated_correct"] / a["reported_migrated"], 3) if a["reported_migrated"] else None
        a["usd"] = round(a["usd"], 2)
        a["minutes"] = round(a["minutes"], 1)
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "set": "eval/v3/REPOS.json (10 fresh repos)", "arms": arms, "repos": slim}
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=str)
    write_md(out)
    return out


def write_md(d):
    L = ["# v3 results: rules only (A) vs agent + docs prompt (B') vs agent + toolkit (C)", "",
         "Generated by `eval/v3/run_v3.py aggregate` from eval/v3/runs/*/*/result.json. Set: 10 fresh repos (eval/v3/REPOS.json),",
         "never used to build or tune v1/v2. Ground truth: blind rubric inventories committed before any arm C run",
         "(eval/v3/inventory/). One trial per arm per repo (k=1).", "",
         "| arm | repos | broken (95% CI) | sites moved | correct | wrong | moved precision | reported migrated precision | diff lines | $ | minutes |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ("A", "B_prime", "C"):
        a = d["arms"].get(arm)
        if not a:
            continue
        ci = a["broken_ci95"]
        L.append("| %s | %d | %d/%d (%s-%s) | %d | %d | %d | %s | %s | %d | %.2f | %.0f |" % (
            arm, a["repos"], a["broken"], a["repos"], ci[0], ci[1], a["sites_moved"], a["sites_moved_correct"], a["sites_moved_wrong"],
            a["moved_precision"], a["reported_migrated_precision"], a["diff_lines"], a["usd"], a["minutes"]))
    L += ["", "Inventory: %d sites expected migrated across the set (handoff sites count as correct when moved)." % (
        max((a["inventory_migrated"] for a in d["arms"].values()), default=0)), "",
          "## Per repo", "", "| repo | arm | broken | moved | correct | wrong | reported migrated (correct) | diff | $ | min | notes |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(d["repos"], key=lambda r: (r["repo"].lower(), r["arm"])):
        notes = []
        if r.get("test_files_edited"):
            notes.append("edited tests: %s" % ", ".join(r["test_files_edited"][:2]))
        if r.get("inventory_leak_in_transcript"):
            notes.append("INVENTORY PATH IN TRANSCRIPT")
        if r.get("ledger") and r["ledger"].get("reverted"):
            notes.append("finish reverted to floor")
        if r.get("agent_exit") not in (None, 0):
            notes.append("agent exit %s" % r["agent_exit"])
        L.append("| %s | %s | %s | %s | %s | %s | %s (%s) | %s | %.2f | %.0f | %s |" % (
            r["repo"], r["arm"], "**yes**" if r["broken"] else "no", r["sites_moved"], r["sites_moved_correct"], r["sites_moved_wrong"],
            r.get("reported_migrated"), r.get("reported_migrated_correct"), r["diff_lines"], r["usd"] or 0, r["minutes"] or 0, "; ".join(notes)))
    open(os.path.join(HERE, "RESULTS.md"), "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    t = sp.add_parser("trial")
    t.add_argument("arm", choices=ARMS)
    t.add_argument("name")
    r = sp.add_parser("repo")
    r.add_argument("name")
    r.add_argument("--arms", default=",".join(ARMS))
    al = sp.add_parser("all")
    al.add_argument("--jobs", type=int, default=4)
    al.add_argument("--arms", default=",".join(ARMS))
    al.add_argument("--only")
    sp.add_parser("aggregate")
    rs = sp.add_parser("rescore")
    rs.add_argument("--only")
    rs.add_argument("--arms", default=",".join(ARMS))
    rs.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "trial":
        trial(a.arm, a.name)
    elif a.cmd == "repo":
        run_repo(a.name, a.arms.split(","))
    elif a.cmd == "all":
        names = [n for n in repos() if not a.only or n in a.only.split(",")]
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda n: run_repo(n, a.arms.split(",")), names))
        aggregate()
    elif a.cmd == "aggregate":
        aggregate()
    elif a.cmd == "rescore":
        todo = [(arm, n) for arm in a.arms.split(",") for n in repos() if (not a.only or n in a.only.split(","))
                and os.path.exists(os.path.join(TRIALS, arm, n))]
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda x: _safe(score_trial, *x), todo))
        aggregate()


if __name__ == "__main__":
    main()
