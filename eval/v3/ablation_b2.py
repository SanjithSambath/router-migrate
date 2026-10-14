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
"""
import argparse
import glob
import json
import os
import shlex
import shutil
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
    errs = []
    for i, s in enumerate(sites):
        if not isinstance(s, dict):
            errs.append("entry %d is not an object" % i)
            continue
        f, ln = s.get("file"), s.get("line")
        if not f or not os.path.exists(os.path.join(dst, f)) or not isinstance(ln, int):
            errs.append("entry %d: file must exist and line must be an integer" % i)
        if s.get("outcome") not in AG.OUTCOMES:
            errs.append("entry %d: outcome must be one of %s" % (i, ", ".join(AG.OUTCOMES)))
        elif s["outcome"] != "migrated" and s.get("reason_code") not in AG.REASON_CODES:
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
        "{SIM_CMD}", sim).replace("{REASON_CODES}", "; ".join("`%s` = %s" % kv for kv in AG.REASON_CODES.items()))


def trial_b2(label, name, out_dir):
    rec = R.recipe(name)
    a_dir, dst = _copy_floor(label, name)
    led = json.load(open(os.path.join(a_dir, ".router-migrate", "ledger.json")))
    floor_report = open(os.path.join(a_dir, ".router-migrate", "REPORT.md")).read()
    shutil.rmtree(os.path.join(dst, ".router-migrate"), ignore_errors=True)  # no ledger, worklist or plan: those are the toolkit
    os.makedirs(os.path.join(dst, ".router-migrate"))
    open(os.path.join(dst, ".router-migrate", "FLOOR_REPORT.md"), "w").write(floor_report)
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


def trial(label, name):
    out_dir = os.path.join(R.RUNS, label, name)
    if os.path.exists(os.path.join(out_dir, "result.json")):
        print("skip %s %s (done)" % (label, name), flush=True)
        return json.load(open(os.path.join(out_dir, "result.json")))
    R.baseline(name)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    agent = trial_b2(label, name, out_dir) if base(label) == "B2" else trial_c(label, name, out_dir)
    json.dump({"agent": agent, "minutes": round((time.time() - t0) / 60, 1)}, open(os.path.join(out_dir, "agent.json"), "w"),
              indent=1, default=str)
    return R.score_trial(label, name)


# ------------------------------------------------------------------------------------------------ scoring hook
_orig_reported = R.reported_outcomes


def reported_outcomes(arm, dst, name, measured):
    """B2: the floor's plan overridden by MIGRATION_REPORT.json entries (exactly how C's plan is overridden by its claims).
    C_r<k>: run_v3's C logic. Anything else: unchanged."""
    b = base(arm)
    if b == "C":
        return _orig_reported("C", dst, name, measured)
    if b != "B2":
        return _orig_reported(arm, dst, name, measured)
    plan_p = os.path.join(R.TRIALS, "A", name, ".router-migrate", "plan.json")
    pristine = os.path.join(R.RE.WORK, "pristine", name)
    rep = [dict(s) for s in json.load(open(plan_p))["sites"] if s.get("prod")]
    sites, _ = _report_entries(dst)
    maps = {}
    for c in sites or []:
        if not isinstance(c, dict) or not c.get("file") or not isinstance(c.get("line"), int) or c.get("outcome") not in AG.OUTCOMES:
            continue
        f = c["file"]
        if f not in maps:
            maps[f] = R.line_map(os.path.join(pristine, f), os.path.join(dst, f))
        ln = maps[f](int(c["line"])) if maps[f] else int(c["line"])
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
    by_label = {}
    for f in sorted(glob.glob(os.path.join(R.RUNS, "*", "*", "result.json"))):
        lab = f.split(os.sep)[-3]
        if lab == "baseline":
            continue
        by_label.setdefault(lab, []).append(json.load(open(f)))
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
        if base(lab) in ("B2", "C") and "_r" in lab:
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
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": MODEL, "labels": labels, "per_repo": per_repo}
    json.dump(out, open(os.path.join(HERE, "ablation_b2.json"), "w"), indent=1, default=str)

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
    for name in ("B2", "C"):
        row = fmt_arm(name)
        if row:
            L.append(row)
    for lab in ("A", "B_prime", "C"):
        t = labels.get(lab)
        if t:
            L.append("| %s (old, k=1) | %d | %d | %d | %d | %d | %s | %d | %.2f | %.0f | %d | %d |" % (
                lab, t["broken"], t["sites_moved_correct"], t["sites_moved_wrong"], t["sites_moved_correct_adj"], t["sites_moved_wrong_adj"],
                t["agreement_mean"], t["diff_lines"], t["usd"], t["minutes"], t["turns"], t["reverted"]))
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
    open(os.path.join(HERE, "ABLATION_B2.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    return out


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    t = sp.add_parser("trial")
    t.add_argument("label")
    t.add_argument("name")
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
        trial(a.label, a.name)
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
