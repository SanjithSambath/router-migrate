#!/usr/bin/env python3
"""The whole v3 run end to end, as a person would do it: `router-migrate run <repo> --agent claude` (floor -> agent migrates ->
finish gate -> agent judges -> cards), then the live dashboard (approve every applicable card in the browser, click Apply),
then `router-migrate finish` again on the applied repo, then the Router request body each routed feature now sends.

  .venv/bin/python eval/v3/e2e_run.py chatgpt-telegram-bot     # a fresh repo from REPOS.json (pinned commit, fresh history)
  .venv/bin/python eval/v3/e2e_run.py showcase                 # tests/test_v2.py's synthetic app: every lever applies

Writes eval/v3/e2e/<name>/: summary.json, run.log, apply.txt, finish.txt, policy_probe.json, ledger.json, decisions.json,
router.policy.json, recommendations.json, events.jsonl, REPORT.html (re-rendered after Apply), transcripts (gz); and
eval/v3/screenshots/e2e-<name>-*.png. Claude usage only (OAuth token); no Router spend: the suites mock the SDKs."""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import run_v3 as R  # noqa: E402
import e2e_dashboard as E  # noqa: E402

OUT = os.path.join(HERE, "e2e")
SMOKE = "def test_imports():\n    import svc.workers.nightly_summaries, svc.api.chat, svc.vendors.xai, svc.agent  # noqa\n"


def showcase():
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_v2 import FILES
    dst = os.path.join(R.TRIALS, "E2E", "showcase")
    shutil.rmtree(dst, ignore_errors=True)
    for rel, src in list(FILES.items()) + [("tests/test_smoke.py", SMOKE)]:
        p = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(src)
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=dst, capture_output=True, check=True)  # noqa: E731
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=e2e@local", "-c", "user.name=e2e", "commit", "-qm", "base")
    py = os.path.join(ROOT, ".venv", "bin", "python")
    return dst, "OPENAI_API_KEY=sk-dummy ANTHROPIC_API_KEY=x XAI_API_KEY=x %s -m pytest -q -p no:cacheprovider tests" % py


def secrets_clean(d):
    """No Router key, no OAuth token, no real-looking provider key in anything we save."""
    bad = [v for v in (os.environ.get("RAMP_ROUTER_API_KEY"), open(R.TOKEN_FILE).read().strip() if os.path.exists(R.TOKEN_FILE) else None) if v]
    pat = re.compile(r"(sk-(?!dummy|test)[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,})")
    hits = []
    for dp, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(dp, f)
            data = gzip.open(p, "rt", errors="replace").read() if f.endswith(".gz") else open(p, errors="replace").read()
            if any(v in data for v in bad) or pat.search(data):
                hits.append(os.path.relpath(p, d))
    return hits


def main():
    name = sys.argv[1]
    if name == "showcase":
        dst, tc = showcase()
    else:
        dst, rec = R.fresh_copy(name, "E2E")
        tc = R.test_cmd(rec, dst)
    out = os.path.join(OUT, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    env = R.agent_env()
    print("repo: %s" % dst, flush=True)
    t0 = time.time()
    p = subprocess.run([R.RM, "run", dst, "--test-cmd", tc, "--agent", "claude", "--no-dashboard"], cwd=dst, env=env,
                       capture_output=True, text=True, timeout=5400)
    run_min = round((time.time() - t0) / 60, 1)
    open(os.path.join(out, "run.log"), "w").write(p.stdout[-30000:] + p.stderr[-4000:])
    print(p.stdout[-2500:], flush=True)
    if p.returncode != 0:
        sys.exit("run exited %d; see %s" % (p.returncode, os.path.join(out, "run.log")))
    res = E.drive(dst, out, None, prefix="e2e-" + name)
    # the frozen report as it stands after Apply
    from router_migrate import improve as imp
    from router_migrate.dashboard import render
    html = render.render_report(imp.bundle(dst, repo=os.path.basename(dst)))
    open(os.path.join(dst, ".router-migrate", "REPORT.html"), "w").write(html)
    sm = os.path.join(dst, ".router-migrate")
    for f in ("ledger.json", "decisions.json", "recommendations.json", "events.jsonl", "REPORT.html"):
        if os.path.exists(os.path.join(sm, f)):
            shutil.copy(os.path.join(sm, f), os.path.join(out, f))
    if os.path.exists(os.path.join(dst, "router.policy.json")):
        shutil.copy(os.path.join(dst, "router.policy.json"), os.path.join(out, "router.policy.json"))
    for t in ("agent_transcript.jsonl", "agent_improve_transcript.jsonl"):
        if os.path.exists(os.path.join(sm, t)):
            with gzip.open(os.path.join(out, t + ".gz"), "wt") as g:
                g.write(open(os.path.join(sm, t), encoding="utf-8", errors="replace").read())
    led = json.load(open(os.path.join(sm, "ledger.json")))
    recs = json.load(open(os.path.join(sm, "recommendations.json")))
    run = imp.run_summary(dst) or {}
    summary = {
        "repo": name, "commit": R.repos()[name]["commit"] if name in R.repos() else "tests/test_v2.py FILES",
        "run_minutes": run_min, "floor_migrated": run.get("floor_migrated"), "routes_now": run.get("routes_now"),
        "added_by_agent": run.get("added_by_agent"), "migrate": run.get("migrate"), "improve": run.get("improve"),
        "judged_features": len(led.get("judgments") or {}),
        "cards": [{"rule": c["rule"], "title": c["title"], "state": c.get("state")} for c in recs["cards"]],
        "approved_in_ui": res["approved_in_ui"], "quick_gate_ok": res["quick_gate_ok"], "finish_ok_after_apply": res["finish_ok"],
        "decision_edits": [e for e in led.get("edits") or [] if e.get("by") == "decisions"], "request_bodies": res["probe"]}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    hits = secrets_clean(out)
    if hits:
        sys.exit("secret-like strings in %s; not keeping them" % hits)
    print(json.dumps({k: summary[k] for k in ("routes_now", "added_by_agent", "judged_features", "approved_in_ui", "quick_gate_ok",
                                              "finish_ok_after_apply")}, indent=1))
    return 0 if res["quick_gate_ok"] and res["finish_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
