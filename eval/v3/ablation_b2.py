#!/usr/bin/env python3
"""B'' ablation: does the v3 toolkit beat a detailed prompt running on the same deterministic core?

  B2  floor copy (arm A's trial dir) + migrate.md's facts, outcome rules, recipes and hard rules as plain instructions
      (prompt_B2.md, no router-migrate commands) + the floor's REPORT.md + the test/typecheck/sim commands (as B') + the
      same gate checks as C (agent.gate: parse, scope, typecheck, tests vs baseline, Router-on sim) as a Stop hook with
      revert-to-floor after 3 failures + .router-migrate/MIGRATION_REPORT.json as its report channel (scored like C's claims)
  C   `router-migrate run --agent claude --skip-improve` from the same floor copy (the current toolkit)

Only the toolkit differs (worklist, check/wrap/claim, ledger, claims-vs-oracle and worklist gates). Replicates are labelled
<arm>_r<k>; both arms use the same pinned model, run interleaved per repo (arm order alternates by repo), and are scored by
run_v3.score_trial unchanged. Nothing in run_v3.py, results.json or RESULTS.md is modified.

  .venv/bin/python eval/v3/ablation_b2.py trial B2_r1 llmcord
  .venv/bin/python eval/v3/ablation_b2.py all --k 3 --jobs 5 [--only a,b]
  .venv/bin/python eval/v3/ablation_b2.py gate DST --label L --name N [--hook]     (B2's gate; its Stop hook)
  .venv/bin/python eval/v3/ablation_b2.py report                                  -> eval/v3/ABLATION_B2.md, ablation_b2.json
  RM_EVAL_SET=dev .venv/bin/python eval/v3/ablation_b2.py trial D_r1 swarm [--dry-run]
                    (the original 15 SPEC repos: eval/repos.json + eval/recipes, results in eval/v3/runs_dev/<label>/<repo>/;
                     no inventory there, so scoring reports moved = valid Router traffic, tests before/after, broken)
Sites moved = valid Router traffic vs the mechanical movable ground truth: eval/v3/score_moved.py (written per run as
sites_moved.json by score_trial; `score_moved.py score` rescores saved runs); `report` appends its table.
"""
import argparse
import glob
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_v3 as R  # noqa: E402
from router_migrate import agent as AG  # noqa: E402

MODEL = os.environ.get("RM_AGENT_MODEL", "claude-sonnet-5")  # what arm C ran on in the original eval
R.MODEL = MODEL
PY = os.path.join(R.ROOT, ".venv", "bin", "python")
GATE_DIR = os.path.join(R.RE.WORK, "gate_state")
REPORT = os.path.join(".router-migrate", "MIGRATION_REPORT.json")
MAX_ATTEMPTS = 3
# The v4 agent's outcome vocabulary, which B2's MIGRATION_REPORT.json entries are validated against (the v4 agent itself was
# archived in the v7 cleanup: archive/README.md). The B2, C, Cnf and D trials ran the v4 toolkit (its gate, `run` proposal
# pass and `claim`), so only their recorded results score and report now; D3min / D3free run the current harness.
OUTCOMES = ("migrated", "kept", "flagged", "handoff", "needs_human", "not_a_site")
REASON_CODES = {
    "R_NO_ENDPOINT": "Router has no endpoint for this surface (embeddings, images, audio, files, moderation, legacy completions)",
    "R_OTHER_HOST": "the request goes to another vendor's endpoint in production (incl. a user-defined provider list)",
    "R_PER_TENANT": "the API key is per user / tenant / session",
    "R_STATEFUL": "stateful Responses use or provider-side files",
    "R_BETA": "Anthropic beta features",
    "R_MODEL_NOT_IN_CATALOG": "the model is not a Router catalog id",
    "R_DEAD_CODE": "unreachable or unused code",
    "R_NOT_PRODUCTION": "tests, examples, scripts, eval harnesses, docs, templates, or not an LLM call",
    "R_RAW_REQUESTS": "raw HTTP to OpenAI/Anthropic that needs a code change (no runtime adapter for this client)",
    "R_OTHER_GATEWAY": "another LLM gateway (Vercel AI Gateway, LiteLLM proxy, ...)",
    "R_OTHER_VENDOR_SDK": "another vendor's SDK whose model is in Router's catalog (needs a port or BYOK)",
    "R_BYOK_REQUIRED": "Router serves the model only with the customer's own provider key",
    "R_UNDECIDED": "the code doesn't settle it; say what a human needs to check",
    "R_GATE_FAILED": "the edit failed the gate and was reverted",
}


def base(label):
    return label.split("_r")[0]


# ------------------------------------------------------------------------------------------------ B2 gate
def _state_path(label, name):
    return os.path.join(GATE_DIR, label, name + ".json")


def _report_entries(dst):
    """(entries, error) from MIGRATION_REPORT.json, validated like `router-migrate claim` validates a claim."""
    p = os.path.join(dst, REPORT)
    if not os.path.exists(p):
        return None, "%s is missing: write it (sites you judged; an empty list is allowed if you agree with every floor outcome)" % REPORT
    try:
        d = json.load(open(p))
    except ValueError as e:
        return None, "%s is not valid JSON: %s" % (REPORT, e)
    sites = d.get("sites") if isinstance(d, dict) else None
    if not isinstance(sites, list):
        return None, '%s must be {"sites": [...]}' % REPORT
    floor = _floor_sites(dst)
    fr = os.path.join(dst, ".router-migrate", "FLOOR_REPORT.md")
    current = os.path.exists(fr) and CURRENT_MARK in open(fr).read()
    errs = []
    for i, s in enumerate(sites):
        if not isinstance(s, dict):
            errs.append("entry %d is not an object" % i)
            continue
        if s.get("floor_site"):
            fs = str(s["floor_site"]).strip().strip("`")
            if fs not in floor:
                errs.append("entry %d: floor_site %r is not a site in FLOOR_REPORT.md (copy the `site` value exactly)" % (i, fs))
                continue
            s["file"], s["line"], s["_pristine"] = fs.rsplit(":", 1)[0], int(fs.rsplit(":", 1)[1]), not current
        f, ln = s.get("file"), s.get("line")
        if not f or not os.path.exists(os.path.join(dst, f)) or not isinstance(ln, int):
            errs.append("entry %d: set floor_site, or file (must exist) and an integer line" % i)
        if s.get("outcome") not in OUTCOMES:
            errs.append("entry %d: outcome must be one of %s" % (i, ", ".join(OUTCOMES)))
        elif s["outcome"] != "migrated" and s.get("reason_code") not in REASON_CODES:
            errs.append("entry %d (%s:%s): %s needs a reason_code from the list" % (i, f, ln, s["outcome"]))
        if not s.get("why"):
            errs.append("entry %d (%s:%s): why is required" % (i, f, ln))
        ev = s.get("evidence")
        if not isinstance(ev, list) or not ev:
            errs.append("entry %d (%s:%s): at least one evidence FILE:LINE is required" % (i, f, ln))
        else:
            bad = AG._resolve_evidence(dst, [str(x) for x in ev])
            if isinstance(bad, str):
                errs.append("entry %d (%s:%s): %s" % (i, f, ln, bad))
    return sites, "; ".join(errs[:8]) if errs else None


CURRENT_MARK = "<!-- site lines refer to the files as they are now (after the floor's edits) -->"


def _current_lines(report, a_dir):
    """FLOOR_REPORT.md with each site's pre-floor line mapped to its line in the floored file (through the floor's own
    apply.diff), so `floor_site` names the code the agent actually reads (the convention C's worklist uses)."""
    import re
    from router_migrate.recommend import line_maps
    diff_p = os.path.join(a_dir, ".router-migrate", "apply.diff")
    maps = line_maps(open(diff_p).read()) if os.path.exists(diff_p) else {}

    def sub(m):
        f, n = m.group(1).rsplit(":", 1)
        return "| `%s:%d` |" % (f, maps[f](int(n)) if f in maps else int(n))
    return CURRENT_MARK + "\n" + re.sub(r"^\| `([^`]+:\d+)` \|", sub, report, flags=re.M)


def _floor_sites(dst):
    """`file:line` values in the Sites table of FLOOR_REPORT.md (pre-floor lines, as plan.json has them)."""
    import re
    p = os.path.join(dst, ".router-migrate", "FLOOR_REPORT.md")
    txt = open(p).read() if os.path.exists(p) else ""
    return set(re.findall(r"^\| `([^`]+:\d+)` \|", txt, re.M))


def gate_once(dst, label, name):
    """Run C's gate checks with B2's report standing in for claims. Returns (ok, failures, info, state)."""
    st = json.load(open(_state_path(label, name)))
    sites, rep_err = _report_entries(dst)
    led = {"floor": st["floor"], "baseline": st["baseline"], "edits": [],
           "claims": {"%s:%s" % (s["file"], s["line"]): {"file": s["file"], "line": s["line"]} for s in (sites or [])
                      if isinstance(s, dict) and s.get("file")}}
    ok, fails, info = AG.gate(dst, led)
    if rep_err:
        fails.append({"gate": "report", "detail": rep_err, "next": "fix %s" % REPORT})
        ok = False
    st["attempts"] = st.get("attempts", 0) + (0 if ok else 1)
    st.setdefault("history", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "gates": [f["gate"] for f in fails], "info": info})
    note = ""
    if not ok and st["attempts"] >= MAX_ATTEMPTS:
        hard = [f for f in fails if f["gate"] in ("parse", "scope", "typecheck", "tests", "sim")]
        if hard and not st.get("reverted"):
            changes = AG.changed_since(dst, st["floor"]["tree"])
            AG.restore(dst, st["floor"]["tree"], changes)
            st["reverted"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": [p for _, p in changes], "last_failures": fails}
            note = "reverted your edits to the floor after %d failed gate runs" % st["attempts"]
        ok, fails = True, []
        note = note or "gate attempts ran out"
    json.dump(st, open(_state_path(label, name), "w"), indent=1, default=str)
    return ok, fails, info, note


def cmd_gate(a):
    if a.hook:
        try:
            json.loads(sys.stdin.read() or "{}")
        except ValueError:
            pass
    ok, fails, _, note = gate_once(os.path.abspath(a.dst), a.label, a.name)
    if a.hook:
        if ok:
            print(json.dumps({"systemMessage": "gate: %s" % (note or "all checks pass")}))
        else:
            reason = "The gate failed; you are not done:\n" + "\n".join("- [%s] %s\n  next: %s" % (f["gate"], f["detail"][:600], f["next"])
                                                                        for f in fails)
            print(json.dumps({"decision": "block", "reason": reason[:6000]}))
        return 0
    print("gate: %s%s" % ("OK" if ok else "FAIL", (" (%s)" % note) if note else ""))
    for f in fails:
        print("FAIL [%s] %s" % (f["gate"], f["detail"][:800]))
    return 0 if ok else 1


# ------------------------------------------------------------------------------------------------ trials
def _copy_floor(label, name):
    a_dir = os.path.join(R.TRIALS, "A", name)
    if not os.path.exists(os.path.join(a_dir, ".router-migrate", "ledger.json")):
        raise SystemExit("arm A's floor for %s is missing; run run_v3.py trial A %s first" % (name, name))
    dst = os.path.join(R.TRIALS, label, name)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    R.sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(a_dir), shlex.quote(dst)) * 2))
    return a_dir, dst


def _runtime_files(dst):
    out = []
    for p in glob.glob(os.path.join(dst, "**", "router_runtime.py"), recursive=True) + \
            glob.glob(os.path.join(dst, "**", "routerRuntime.*"), recursive=True):
        rel = os.path.relpath(p, dst)
        if "node_modules" not in rel and not rel.startswith(".router-migrate"):
            out.append("`%s`" % rel)
    return ", ".join(sorted(out)) or "(none: the floor wrapped no client)"


def b2_prompt(rec, dst):
    tc = R.typecheck_cmd(rec, dst)
    sim = "%s %s %s %s" % (PY, os.path.join(HERE, "sim_test.py"), shlex.quote(dst), shlex.quote(R.test_cmd(rec, dst)))
    return open(os.path.join(HERE, "prompt_B2.md")).read().replace("{RUNTIME_FILES}", _runtime_files(dst)).replace(
        "{TEST_CMD}", R.test_cmd(rec, dst)).replace("{TYPECHECK_LINE}", ("- Typecheck command: %s\n" % tc) if tc else "").replace(
        "{SIM_CMD}", sim).replace("{REASON_CODES}", "; ".join("`%s` = %s" % kv for kv in REASON_CODES.items()))


def trial_b2(label, name, out_dir):
    rec = R.recipe(name)
    a_dir, dst = _copy_floor(label, name)
    led = json.load(open(os.path.join(a_dir, ".router-migrate", "ledger.json")))
    floor_report = open(os.path.join(a_dir, ".router-migrate", "REPORT.md")).read()
    shutil.rmtree(os.path.join(dst, ".router-migrate"), ignore_errors=True)  # no ledger, worklist or plan: those are the toolkit
    os.makedirs(os.path.join(dst, ".router-migrate"))
    open(os.path.join(dst, ".router-migrate", "FLOOR_REPORT.md"), "w").write(_current_lines(floor_report, a_dir))
    R._reinstall(rec, dst)
    os.makedirs(os.path.join(GATE_DIR, label), exist_ok=True)
    state = json.loads(json.dumps({"floor": led["floor"], "baseline": led["baseline"]}).replace(a_dir, dst))
    json.dump(dict(state, attempts=0, history=[]), open(_state_path(label, name), "w"), indent=1)
    hook = "%s %s gate %s --label %s --name %s --hook" % (PY, os.path.abspath(__file__), shlex.quote(dst), label, name)
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 1800, "command": hook}]}]}}
    agent, wall = R.run_agent(dst, b2_prompt(rec, dst), settings, out_dir)
    # the harness re-runs the gate itself after the agent exits (as `run --agent` does for C)
    ok, fails, info, note = gate_once(dst, label, name)
    st = json.load(open(_state_path(label, name)))
    sites, rep_err = _report_entries(dst)
    agent.update({"final_gate_ok": ok, "final_gate_failures": [f["gate"] for f in fails], "gate_note": note,
                  "gate_attempts": st.get("attempts"), "reverted": bool(st.get("reverted")),
                  "report_sites": len(sites or []), "report_error": rep_err})
    return agent


def trial_c(label, name, out_dir):
    rec = R.recipe(name)
    a_dir, dst = _copy_floor(label, name)
    R.retarget(dst, a_dir)
    R._reinstall(rec, dst)
    cmd = [R.RM, "run", dst, "--agent", "claude", "--agent-timeout", str(R.AGENT_TIMEOUT), "--model", MODEL, "--skip-improve"]
    p = subprocess.run(cmd, env=R.agent_env(), capture_output=True, text=True, timeout=R.AGENT_TIMEOUT + 3600)
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
    fin = L.get("finish") or {}
    return {"exit": cost.get("agent_exit"), "usd": cost.get("agent_usd"), "turns": cost.get("agent_turns"), "model": cost.get("agent_model"),
            "final": final, "finish_exit": p.returncode, "gate_attempts": fin.get("attempts"), "reverted": bool(fin.get("reverted"))}


def trial_cnf(label, name, out_dir):
    """C with no floor: the same toolkit, prompt (floor sentences adapted: prompt_C_nofloor.md) and gate, from the untouched
    source. `run --no-floor` builds the ledger, baseline and worklist without the codemod; then `run --agent` as for C."""
    dst, rec = R.fresh_copy(name, label)
    cmd = [R.RM, "run", dst, "--no-floor", "--test-cmd", R.test_cmd(rec, dst)] + \
        (["--typecheck-cmd", R.typecheck_cmd(rec, dst)] if R.typecheck_cmd(rec, dst) else [])
    p0 = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    open(os.path.join(out_dir, "run_setup.log"), "w").write((p0.stdout or "")[-20000:] + (p0.stderr or "")[-5000:])
    env = R.agent_env()
    env["ROUTER_MIGRATE_PROMPT"] = os.path.join(HERE, "prompt_C_nofloor.md")
    cmd = [R.RM, "run", dst, "--agent", "claude", "--agent-timeout", str(R.AGENT_TIMEOUT), "--model", MODEL, "--skip-improve"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=R.AGENT_TIMEOUT + 3600)
    open(os.path.join(out_dir, "run_agent.log"), "w").write((p.stdout or "")[-20000:] + (p.stderr or "")[-5000:])
    L = json.load(open(os.path.join(dst, ".router-migrate", "ledger.json")))
    cost, fin = L.get("cost") or {}, L.get("finish") or {}
    tr = os.path.join(dst, ".router-migrate", "agent_transcript.jsonl")
    if os.path.exists(tr):
        shutil.move(tr, os.path.join(out_dir, "transcript.jsonl"))
    return {"exit": cost.get("agent_exit"), "usd": cost.get("agent_usd"), "turns": cost.get("agent_turns"), "model": cost.get("agent_model"),
            "finish_exit": p.returncode, "gate_attempts": fin.get("attempts"), "reverted": bool(fin.get("reverted")),
            "worklist": len(L.get("worklist") or []), "claims": len(L.get("claims") or {}),
            "wraps": sum(1 for e in L.get("edits") or [] if e.get("by") == "agent-wrap" and e.get("changed"))}


def trial_d(label, name, out_dir):
    """v4: the agent does the migration from the untouched source. `run` records the baseline and proposals (nothing applied);
    `run --agent` launches the agent with the v4 prompt and toolkit, then the gate."""
    dst, rec = R.fresh_copy(name, label)
    cmd = [R.RM, "run", dst, "--test-cmd", R.test_cmd(rec, dst)] + \
        (["--typecheck-cmd", R.typecheck_cmd(rec, dst)] if R.typecheck_cmd(rec, dst) else [])
    p0 = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    open(os.path.join(out_dir, "run_setup.log"), "w").write((p0.stdout or "")[-20000:] + (p0.stderr or "")[-5000:])
    cmd = [R.RM, "run", dst, "--agent", "claude", "--agent-timeout", str(R.AGENT_TIMEOUT), "--model", MODEL, "--skip-improve"]
    p = subprocess.run(cmd, env=R.agent_env(), capture_output=True, text=True, timeout=R.AGENT_TIMEOUT + 3600)
    open(os.path.join(out_dir, "run_agent.log"), "w").write((p.stdout or "")[-20000:] + (p.stderr or "")[-5000:])
    L = json.load(open(os.path.join(dst, ".router-migrate", "ledger.json")))
    cost, fin = L.get("cost") or {}, L.get("finish") or {}
    tr = os.path.join(dst, ".router-migrate", "agent_transcript.jsonl")
    if os.path.exists(tr):
        shutil.move(tr, os.path.join(out_dir, "transcript.jsonl"))
    return {"exit": cost.get("agent_exit"), "usd": cost.get("agent_usd"), "turns": cost.get("agent_turns"), "model": cost.get("agent_model"),
            "finish_exit": p.returncode, "gate_attempts": fin.get("attempts"), "reverted": bool(fin.get("reverted")),
            "worklist": len(L.get("worklist") or []), "claims": len(L.get("claims") or {}), "probes": len(L.get("probes") or {}),
            "cards": [{"kind": c["kind"], "site": c["site"]} for c in L.get("cards") or []], "result": L.get("result")}


D3_ARMS = {"D3min": "min", "D3free": "free"}  # labels D3min / D3free (optionally _r<k>) -> `router-migrate run --arm`
MIGRATION = os.path.join(".router-migrate", "MIGRATION.json")


def trial_d3(label, name, out_dir):
    """D3 harness: the agent picks its own method from facts + optional tools, from the untouched source, in one command:
    `router-migrate run <dst> --agent claude --arm min|free` (baseline, `check --all` facts in the prompt, the agent, then for
    `min` one final outside check + one resume + revert of what still fails). Its report is .router-migrate/MIGRATION.json."""
    dst, rec = R.fresh_copy(name, label)
    cmd = [R.RM, "run", dst, "--agent", "claude", "--arm", D3_ARMS[base(label)], "--agent-timeout", str(R.AGENT_TIMEOUT),
           "--model", MODEL, "--test-cmd", R.test_cmd(rec, dst)] + \
        (["--typecheck-cmd", R.typecheck_cmd(rec, dst)] if R.typecheck_cmd(rec, dst) else [])
    try:
        p = subprocess.run(cmd, env=R.agent_env(), capture_output=True, text=True, timeout=R.AGENT_TIMEOUT + 3600)
        code, so, se = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        code, so, se = "timeout", (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), ""
    open(os.path.join(out_dir, "run_agent.log"), "w").write(so[-20000:] + se[-5000:])
    led_p = os.path.join(dst, ".router-migrate", "ledger.json")
    L = json.load(open(led_p)) if os.path.exists(led_p) else {}
    cost = L.get("cost") or {}
    for src, to in ((os.path.join(dst, ".router-migrate", "agent_transcript.jsonl"), "transcript.jsonl"), (os.path.join(dst, MIGRATION), "MIGRATION.json")):
        if os.path.exists(src):
            (shutil.move if to.startswith("transcript") else shutil.copy)(src, os.path.join(out_dir, to))
    return {"exit": cost.get("agent_exit"), "usd": cost.get("agent_usd"), "turns": cost.get("agent_turns"), "model": MODEL,
            "run_exit": code, "resumed": cost.get("resumed"), "api_error": cost.get("api_error"), "result": L.get("result"),
            "claims": len(L.get("claims") or {}), "tradeoffs": len(L.get("tradeoffs") or [])}


REVERIFY_TIMEOUT = int(os.environ.get("RM_REVERIFY_TIMEOUT", 120))  # per probe command: one slow probe can't stall scoring


def _reverify_key(dst, led, cmds):
    """What a reverify result depends on: the probe commands, the claims, the final code (the trial's diff vs its base),
    and the per-probe timeout. Saved runs never change, so the same key means the same answer."""
    import hashlib
    diff = subprocess.run(R._PATCH_CMD, cwd=dst, shell=True, capture_output=True, text=True).stdout if os.path.isdir(os.path.join(dst, ".git")) else ""
    blob = json.dumps({"cmds": cmds, "claims": led.get("claims") or {}, "diff": diff, "timeout": REVERIFY_TIMEOUT}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def reverify(dst, name):
    """The harness's own check of v4's dynamic evidence: re-run every probe command the agent recorded, on the final code,
    against a fresh router-sim, and return the migrated claims whose feature sent a valid request (pristine-mapped lines).
    A claim the agent says was probed but that doesn't reproduce here earns nothing. Cached per run in
    <runs>/<label>/<repo>/reverify.json (keyed by probes + claims + final diff); RM_REVERIFY_CACHE=0 re-runs."""
    led_p = os.path.join(dst, ".router-migrate", "ledger.json")
    if not os.path.exists(led_p):
        return []
    led = json.load(open(led_p))
    cmds = sorted({pr["cmd"] for pr in (led.get("probes") or {}).values() if pr.get("cmd")})
    if not cmds:
        return []
    cache_p = os.path.join(R.RUNS, os.path.basename(os.path.dirname(os.path.abspath(dst))), name, "reverify.json")
    key = _reverify_key(dst, led, cmds)
    use_cache = os.environ.get("RM_REVERIFY_CACHE", "1") != "0"
    if use_cache and os.path.exists(cache_p):
        try:
            c = json.load(open(cache_p))
            if c.get("key") == key:
                return c["sites"]
        except (ValueError, KeyError):
            pass
    out = _reverify(dst, name, led, cmds)
    if os.path.isdir(os.path.dirname(cache_p)):
        json.dump({"key": key, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "probes": len(cmds), "timeout_s": REVERIFY_TIMEOUT, "sites": out},
                  open(cache_p, "w"), indent=1)
    return out


def _reverify(dst, name, led, cmds):
    from router_migrate import agent as AGv4
    from router_migrate.sim.server import serve
    srv, sim, base = serve()
    try:
        env = AGv4.probe_env(dst, led, base)
        pin = "export RAMP_ROUTER_API_KEY=%s RAMP_ROUTER_BASE_URL=%s OPENAI_API_KEY=%s ANTHROPIC_API_KEY=%s; " % (
            env["RAMP_ROUTER_API_KEY"], env["RAMP_ROUTER_BASE_URL"], env["OPENAI_API_KEY"], env["ANTHROPIC_API_KEY"])
        for c in cmds:
            # own process group, killed whole on timeout: a hung probe (e.g. jest waiting on an open handle) must not
            # outlive the check
            pr = subprocess.Popen(AGv4.test_env_prefix(led) + pin + c, shell=True, cwd=dst, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                pr.wait(timeout=REVERIFY_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pr.pid, signal.SIGKILL)
                except OSError:
                    pass
                pr.wait()
        recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
    finally:
        srv.shutdown()
    probe_led = {"claims": led.get("claims") or {}, "worklist": [], "proposals": []}
    hits = set(AGv4.confirm_from_records(dst, probe_led, recs, "harness", p=AGv4.current_plan(dst)))
    pm = R.PristineMap(dst, name)
    out = []
    for c in (led.get("claims") or {}).values():
        k = "%s:%d" % (c["file"], int(c["line"]))
        if c.get("outcome") != "migrated" or k not in hits:
            continue
        ln = pm(c["file"], int(c["line"]))
        out.append({"file": c["file"], "line": ln, "line_now": int(c["line"]), "prod": True, "outcome": "migrated", "surface": "chat",
                    "kind": "call", "reason": "dynamic: the harness re-ran the agent's probe on the final code and saw a valid request"})
    return out


_orig_measure = R.measure


def measure(dst, name):
    """Static measure, plus (for v4 runs) the sites the harness itself re-verified dynamically. The static-only count is kept
    in the trial's agent.json as `static_only` so both numbers are reported."""
    static = _orig_measure(dst, name)
    label = os.path.basename(os.path.dirname(dst))
    if base(label) not in ("D",) + tuple(D3_ARMS):
        return static
    dyn = [d for d in reverify(dst, name) if not any(s["file"] == d["file"] and abs(int(s["line"]) - int(d["line"])) <= 3
                                                   and s.get("outcome") == "migrated" for s in static)]
    meta_p = os.path.join(R.RUNS, label, name, "agent.json")
    if os.path.exists(meta_p):
        meta = json.load(open(meta_p))
        meta["measure"] = {"static_routed": sum(1 for s in static if s.get("outcome") == "migrated"), "dynamic_added": len(dyn),
                           "dynamic_sites": ["%s:%s" % (d["file"], d["line"]) for d in dyn]}
        json.dump(meta, open(meta_p, "w"), indent=1, default=str)
    return static + dyn


R.measure = measure


def dry_run(label, name):
    """Everything `trial` does before the agent, without the agent: resolve the repo and recipe in the current set
    (RM_EVAL_SET), the baseline (tests/typecheck on an untouched copy, cached in RUNS/baseline), a fresh trial copy with the
    editable reinstall, and the commands the agent and gate would get. The copy is removed afterwards and the env's editable
    install is pointed back at the prepared clone (where RE.prepare leaves it)."""
    rp, rec = R.repos()[name], R.recipe(name)
    b = base(label)
    if b not in ("B2", "C", "Cnf", "D") + tuple(D3_ARMS):
        raise SystemExit("unknown arm %r (labels are B2_r<k>, C_r<k>, Cnf_r<k>, D_r<k>, D3min[_r<k>], D3free[_r<k>])" % label)
    info = {"set": R.EVAL_SET, "repo": rp.get("repo") or rp.get("url"), "commit": rp["commit"], "lang": rec.get("lang"),
            "runs_dir": os.path.join(R.RUNS, label, name), "trial_dir": os.path.join(R.TRIALS, label, name),
            "pristine": os.path.isdir(os.path.join(R.RE.WORK, "pristine", name)), "env": os.path.isdir(os.path.join(R.RE.WORK, "envs", name)),
            "base": os.path.isdir(os.path.join(R.RE.WORK, "base", name))}
    if b in ("B2", "C"):
        info["floor_ready"] = os.path.exists(os.path.join(R.TRIALS, "A", name, ".router-migrate", "ledger.json"))
    t0 = time.time()
    bl = R.baseline(name)
    info["baseline"] = {"tests": bl.get("tests"), "typecheck": bl.get("typecheck")}
    dst, rec = R.fresh_copy(name, label + "_dryrun")
    try:
        info["copy_git_ok"] = subprocess.run(["git", "log", "--oneline", "-1"], cwd=dst, capture_output=True, text=True).returncode == 0
        info["test_cmd"] = R.test_cmd(rec, dst)
        info["typecheck_cmd"] = R.typecheck_cmd(rec, dst)
        if b == "B2":
            info["agent"] = "claude -p <prompt_B2.md> + Stop hook gate (model %s)" % MODEL
        elif b == "C":
            info["agent"] = "%s run <dst> --agent claude --skip-improve --model %s" % (R.RM, MODEL)
        elif b in D3_ARMS:
            info["agent"] = "%s run <dst> --agent claude --arm %s --model %s --test-cmd %s%s" % (
                R.RM, D3_ARMS[b], MODEL, shlex.quote(info["test_cmd"]),
                (" --typecheck-cmd %s" % shlex.quote(info["typecheck_cmd"])) if info["typecheck_cmd"] else "")
        else:
            info["setup"] = "%s run <dst> %s--test-cmd %s%s" % (R.RM, "--no-floor " if b == "Cnf" else "", shlex.quote(info["test_cmd"]),
                                                             (" --typecheck-cmd %s" % shlex.quote(info["typecheck_cmd"])) if info["typecheck_cmd"] else "")
            info["agent"] = "%s run <dst> --agent claude --skip-improve --model %s" % (R.RM, MODEL)
    finally:
        shutil.rmtree(dst, ignore_errors=True)
        R._reinstall(rec, os.path.join(R.RE.WORK, "runs", name))
    info["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(info, indent=1, default=str), flush=True)
    return info


def trial(label, name):
    out_dir = os.path.join(R.RUNS, label, name)
    if os.path.exists(os.path.join(out_dir, "result.json")):
        print("skip %s %s (done)" % (label, name), flush=True)
        return json.load(open(os.path.join(out_dir, "result.json")))
    if base(label) not in D3_ARMS:
        raise SystemExit("arm %s ran the v4 toolkit, archived in the v7 cleanup (archive/README.md); its recorded results "
                         "still score and report" % label)
    R.baseline(name)
    R.clear_run_dir(out_dir)
    t0 = time.time()
    agent = dict({"B2": trial_b2, "C": trial_c, "Cnf": trial_cnf, "D": trial_d}, **{k: trial_d3 for k in D3_ARMS})[base(label)](label, name, out_dir)
    json.dump({"agent": agent, "minutes": round((time.time() - t0) / 60, 1)}, open(os.path.join(out_dir, "agent.json"), "w"),
              indent=1, default=str)
    return R.score_trial(label, name)


# ------------------------------------------------------------------------------------------------ scoring hook
_orig_reported = R.reported_outcomes


D3_OUTCOME = {"migrated": "migrated", "kept": "kept", "cannot_route": "kept", "needs_decision": "flagged"}


def migration_entries(dst, out_dir=None):
    """A D3 arm's report (.router-migrate/MIGRATION.json: {site, outcome, levers, loses, why}) as per-site judgments, or None
    when there is no readable report (the copy saved beside the run is used when the tree is gone)."""
    for p in (os.path.join(dst or "", MIGRATION), os.path.join(out_dir or "", "MIGRATION.json")):
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p))
        except ValueError:
            return None
        sites = d.get("sites") if isinstance(d, dict) else d
        out = []
        for s in sites if isinstance(sites, list) else []:
            f, _, ln = str((s or {}).get("site") or "").strip("`").rpartition(":") if isinstance(s, dict) else ("", "", "")
            if f and ln.isdigit() and s.get("outcome") in D3_OUTCOME:
                out.append({"file": f, "line": int(ln), "outcome": D3_OUTCOME[s["outcome"]], "reason_code": s["outcome"],
                            "why": s.get("why") or "", "levers": s.get("levers"), "loses": s.get("loses")})
        return out
    return None


def decisions(arm, dst, name, out_dir=None):
    """How many per-site decisions the arm's agent reported: MIGRATION.json entries (D3; ledger claims when there is no
    report), MIGRATION_REPORT.json entries (B2), ledger claims (C/Cnf/D). None for arms with no decision channel (A, B,
    B_prime: their code is their report). 0 means the agent decided nothing: the run is not scored from the rules' proposals."""
    b = base(arm)
    led_p = os.path.join(dst or "", ".router-migrate", "ledger.json")
    if not os.path.exists(led_p) and out_dir:
        led_p = os.path.join(out_dir, "ledger.json")
    claims = lambda: len((json.load(open(led_p)).get("claims") or {})) if os.path.exists(led_p) else 0
    if b in D3_ARMS:
        e = migration_entries(dst, out_dir)
        return len(e) if e else claims()
    if b == "B2":
        if not dst or not os.path.isdir(dst):
            return None  # tree gone: the report can't be read, so this can't be judged
        sites, _ = _report_entries(dst)
        return len(sites) if sites is not None else 0
    if b in ("C", "Cnf", "D"):
        return claims()
    return None


def reported_outcomes(arm, dst, name, measured):
    """B2: the floor's plan overridden by MIGRATION_REPORT.json entries (exactly how C's plan is overridden by its claims).
    C_r<k>: run_v3's C logic. D3: the code as it stands overridden by MIGRATION.json (ledger claims for a run without one).
    Anything else: unchanged."""
    b = base(arm)
    if b == "C":
        return _orig_reported("C", dst, name, measured)
    if b in D3_ARMS:
        rep = [dict(s) for s in measured]
        entries = migration_entries(dst, os.path.join(R.RUNS, arm, name))
        if entries is None:
            led_p = os.path.join(dst, ".router-migrate", "ledger.json")
            entries = list((json.load(open(led_p)).get("claims") or {}).values()) if os.path.exists(led_p) else []
        return _override(rep, [dict(c, _pristine=False) for c in entries], dst, name)
    if b in ("Cnf", "D"):  # no floor plan to start from: the code as it stands (measure-only), overridden by the ledger's claims
        rep = [dict(s) for s in measured]
        led_p = os.path.join(dst, ".router-migrate", "ledger.json")
        claims = list((json.load(open(led_p)).get("claims") or {}).values()) if os.path.exists(led_p) else []
        return _override(rep, [dict(c, _pristine=False) for c in claims], dst, name)
    if b != "B2":
        return _orig_reported(arm, dst, name, measured)
    plan_p = os.path.join(R.TRIALS, "A", name, ".router-migrate", "plan.json")
    rep = [dict(s) for s in json.load(open(plan_p))["sites"] if s.get("prod")]
    sites, _ = _report_entries(dst)
    return _override(rep, sites or [], dst, name)


def _override(rep, entries, dst, name):
    """Apply an arm's per-site judgments to a base report, exactly as run_v3 applies C's claims: lines mapped back to the
    pristine source, nearest site within 3 lines overridden, judged sites the base lacks appended (not_a_site never is)."""
    pm = R.PristineMap(dst, name)
    for c in entries:
        if not isinstance(c, dict) or not c.get("file") or not isinstance(c.get("line"), int) or c.get("outcome") not in OUTCOMES:
            continue
        f = c["file"]
        if c.get("_pristine"):  # floor_site from an old-convention FLOOR_REPORT: already a pristine line, as plan.json has it
            ln = int(c["line"])
        else:
            ln = pm(f, int(c["line"]))
        hit = [s for s in rep if s["file"] == f and abs(int(s["line"]) - ln) <= 3]
        outcome = "kept" if c["outcome"] == "not_a_site" else c["outcome"]
        why = "%s %s: %s" % (c["outcome"], c.get("reason_code") or "", c.get("why") or "")
        if hit:
            hit[0]["outcome"], hit[0]["reason"] = outcome, why
        elif c["outcome"] != "not_a_site":
            rep.append({"file": f, "line": ln, "surface": "chat", "outcome": outcome, "prod": True, "reason": why})
    return rep


R.reported_outcomes = reported_outcomes


# ------------------------------------------------------------------------------------------------ orchestration
def run_repo(name, k, idx):
    order = []
    for r in range(1, k + 1):
        pair = ["B2_r%d" % r, "C_r%d" % r]
        order += pair if (idx + r) % 2 else pair[::-1]  # alternate which arm goes first, across repos and replicates
    for label in order:
        try:
            trial(label, name)
        except Exception as e:
            import traceback
            print("%s %s FAILED: %s" % (label, name, e), flush=True)
            traceback.print_exc()


def cmd_all(a):
    names = [n for n in R.repos() if not a.only or n in a.only.split(",")]
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(lambda x: run_repo(x[1], a.k, x[0]), list(enumerate(names))))
    report()


# ------------------------------------------------------------------------------------------------ report
def _rows(label_glob):
    return [json.load(open(f)) for f in sorted(glob.glob(os.path.join(R.RUNS, label_glob, "*", "result.json")))]


def report():
    by_label, invalid = {}, {}
    for f in sorted(glob.glob(os.path.join(R.RUNS, "*", "*", "result.json"))):
        lab = f.split(os.sep)[-3]
        if lab == "baseline":
            continue
        r = json.load(open(f))
        sm = os.path.join(os.path.dirname(f), "sites_moved.json")
        why = r.get("invalid") or (json.load(open(sm)).get("invalid") if os.path.exists(sm) else None)
        if why:  # infrastructure failure or no agent decisions: not a measurement of the arm (counted separately)
            invalid.setdefault(lab, []).append("%s: %s" % (r.get("repo"), why))
            continue
        by_label.setdefault(lab, []).append(r)
    keys = ("broken", "sites_moved", "sites_moved_correct", "sites_moved_wrong", "sites_moved_correct_adj", "sites_moved_wrong_adj",
            "reported_migrated", "reported_migrated_correct", "diff_lines", "usd", "minutes")

    def totals(rows):
        t = {k: sum((int(bool(r.get(k))) if k == "broken" else (r.get(k) or 0)) for r in rows) for k in keys}
        t["repos"] = len(rows)
        t["agreement_mean"] = round(sum(r.get("reported_outcome_agreement") or 0 for r in rows) / len(rows), 3) if rows else None
        t["turns"] = sum(((r.get("agent") or {}).get("turns") or 0) for r in rows)
        t["reverted"] = sum(1 for r in rows if (r.get("agent") or {}).get("reverted") or (r.get("ledger") or {}).get("reverted"))
        return t

    labels = {lab: totals(rows) for lab, rows in by_label.items()}
    arms = {}
    for lab, t in labels.items():
        if base(lab) in ("B2", "C", "Cnf", "D") and "_r" in lab:
            arms.setdefault(base(lab), []).append((lab, t))
    # per-repo paired comparison (mean over replicates)
    per_repo = {}
    for lab, rows in by_label.items():
        if "_r" not in lab:
            continue
        for r in rows:
            d = per_repo.setdefault(r["repo"], {}).setdefault(base(lab), [])
            d.append({"correct": r.get("sites_moved_correct") or 0, "wrong": r.get("sites_moved_wrong") or 0, "broken": bool(r.get("broken")),
                      "correct_adj": r.get("sites_moved_correct_adj") or 0, "wrong_adj": r.get("sites_moved_wrong_adj") or 0,
                      "usd": r.get("usd") or 0, "minutes": r.get("minutes") or 0, "diff": r.get("diff_lines") or 0,
                      "agreement": r.get("reported_outcome_agreement")})
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": MODEL, "labels": labels, "per_repo": per_repo, "invalid": invalid}
    json.dump(out, open(os.path.join(HERE, "ablation_b2.json" if R.EVAL_SET == "v3" else "ablation_b2_%s.json" % R.EVAL_SET), "w"), indent=1, default=str)

    def fmt_arm(name):
        reps = sorted(arms.get(name, []))
        if not reps:
            return None
        cols = []
        for k in ("broken", "sites_moved_correct", "sites_moved_wrong", "sites_moved_correct_adj", "sites_moved_wrong_adj", "agreement_mean",
                  "diff_lines", "usd", "minutes", "turns", "reverted"):
            vals = [t[k] for _, t in reps if t.get(k) is not None]
            if not vals:
                cols.append("-")
                continue
            m = sum(vals) / len(vals)
            cols.append(("%.2f" % m if isinstance(m, float) and m < 10 else "%.1f" % m) + (" [%s–%s]" % (round(min(vals), 2), round(max(vals), 2)) if len(vals) > 1 else ""))
        return "| %s (k=%d, %s repos) | %s |" % (name, len(reps), "/".join(str(t["repos"]) for _, t in reps), " | ".join(cols))

    L = ["# B'' ablation: detailed prompt (B2) vs toolkit (C), same deterministic floor and gate checks", "",
         "Generated by `eval/v3/ablation_b2.py report`. Model pinned: %s. Mean over replicates per arm (sum over repos per replicate), "
         "[min–max] across replicates. Old single runs (A, B_prime, C) shown for reference." % MODEL, "",
         "| arm | broken repos | correct moves | wrong moves | correct (adj) | wrong (adj) | reported-outcome agreement | diff lines | $ | minutes | turns | reverted |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("B2", "Cnf", "D", "C"):
        row = fmt_arm(name)
        if row:
            L.append(row)
    for lab in ("A", "B_prime", "C"):
        t = labels.get(lab)
        if t:
            L.append("| %s (old, k=1) | %d | %d | %d | %d | %d | %s | %d | %.2f | %.0f | %d | %d |" % (
                lab, t["broken"], t["sites_moved_correct"], t["sites_moved_wrong"], t["sites_moved_correct_adj"], t["sites_moved_wrong_adj"],
                t["agreement_mean"], t["diff_lines"], t["usd"], t["minutes"], t["turns"], t["reverted"]))
    if invalid:
        L += ["", "Invalid runs excluded from every table (infrastructure failure, or the agent made no decisions): " +
              "; ".join("%s %d (%s)" % (lab, len(v), ", ".join(v)[:300]) for lab, v in sorted(invalid.items()))]
    L += ["", "## Per repo (mean over replicates; B2 vs C)", "",
          "| repo | B2 correct | C correct | B2 wrong | C wrong | B2 broken runs | C broken runs | B2 $ | C $ | B2 min | C min |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else float("nan")
    for repo in sorted(per_repo, key=str.lower):
        b, c = per_repo[repo].get("B2", []), per_repo[repo].get("C", [])
        L.append("| %s | %.1f | %.1f | %.1f | %.1f | %d/%d | %d/%d | %.2f | %.2f | %.0f | %.0f |" % (
            repo, mean([x["correct"] for x in b]), mean([x["correct"] for x in c]), mean([x["wrong"] for x in b]), mean([x["wrong"] for x in c]),
            sum(x["broken"] for x in b), len(b), sum(x["broken"] for x in c), len(c), mean([x["usd"] for x in b]), mean([x["usd"] for x in c]),
            mean([x["minutes"] for x in b]), mean([x["minutes"] for x in c])))
    if any("D" in per_repo[r] for r in per_repo):
        L += ["", "## Per repo: v4 (D) vs v3 toolkit without floor (Cnf)", "",
              "| repo | D correct (adj) | Cnf correct (adj) | D wrong (adj) | Cnf wrong (adj) | D broken | D $ | Cnf $ | D min | Cnf min |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for repo in sorted(per_repo, key=str.lower):
            d, c = per_repo[repo].get("D", []), per_repo[repo].get("Cnf", [])
            L.append("| %s | %.1f | %.1f | %.1f | %.1f | %d/%d | %.2f | %.2f | %.0f | %.0f |" % (
                repo, mean([x["correct_adj"] for x in d]), mean([x["correct_adj"] for x in c]), mean([x["wrong_adj"] for x in d]),
                mean([x["wrong_adj"] for x in c]), sum(x["broken"] for x in d), len(d), mean([x["usd"] for x in d]), mean([x["usd"] for x in c]),
                mean([x["minutes"] for x in d]), mean([x["minutes"] for x in c])))
    # sites moved = real Router traffic vs the mechanical movable ground truth (eval/v3/score_moved.py; per-run sites_moved.json,
    # written by score_trial for new trials and by `score_moved.py score` for saved ones)
    try:
        import score_moved
        if score_moved.load_rows():
            sm = score_moved.report()
            L += ["", "## Sites moved = valid Router traffic (migrate-by-default scoring; details in %s)" % (
                "SITES_MOVED.md" if R.EVAL_SET == "v3" else "SITES_MOVED_%s.md" % R.EVAL_SET.upper()), ""] + sm["table_md"]
            out["sites_moved"] = sm["arms"]
            json.dump(out, open(os.path.join(HERE, "ablation_b2.json" if R.EVAL_SET == "v3" else "ablation_b2_%s.json" % R.EVAL_SET), "w"),
                      indent=1, default=str)
    except Exception as e:
        L += ["", "(sites-moved table unavailable: %s)" % str(e)[:200]]
    open(os.path.join(HERE, "ABLATION_B2.md" if R.EVAL_SET == "v3" else "ABLATION_B2_%s.md" % R.EVAL_SET.upper()), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    return out


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    t = sp.add_parser("trial")
    t.add_argument("label")
    t.add_argument("name")
    t.add_argument("--dry-run", action="store_true", help="resolve repo/recipe, baseline, trial copy and commands; no agent")
    al = sp.add_parser("all")
    al.add_argument("--k", type=int, default=3)
    al.add_argument("--jobs", type=int, default=5)
    al.add_argument("--only")
    g = sp.add_parser("gate")
    g.add_argument("dst")
    g.add_argument("--label", required=True)
    g.add_argument("--name", required=True)
    g.add_argument("--hook", action="store_true")
    sp.add_parser("report")
    rs = sp.add_parser("rescore")
    rs.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "trial":
        (dry_run if a.dry_run else trial)(a.label, a.name)
    elif a.cmd == "all":
        cmd_all(a)
    elif a.cmd == "gate":
        sys.exit(cmd_gate(a))
    elif a.cmd == "report":
        report()
    elif a.cmd == "rescore":
        todo = [(f.split(os.sep)[-3], f.split(os.sep)[-2]) for f in glob.glob(os.path.join(R.RUNS, "*_r*", "*", "agent.json"))]
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda x: R._safe(R.score_trial, *x), todo))
        report()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
