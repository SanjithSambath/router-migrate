"""Blind judge for scaffold experiments: each run is scored by a fresh `claude -p` reviewer that sees the run's report,
the outside check's result and the real diffs, but not which arm produced it. Plus objective metrics from the state.

    .venv/bin/python eval/lean/judge.py --round r1 [--parallel 4]

Writes eval/lean/runs/<round>/<arm>/<repo>/judge.json and eval/lean/runs/<round>/SCORES.md."""
import argparse
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

RUBRIC = """You are a CTO reviewing a migration of one application's LLM traffic onto Ramp's Router (an LLM gateway: one
key, many vendors' models; it serves /v1/responses and /v1/messages; Flex tier, model fallbacks, prompt cache keys and
provider_timeout are Router-only fields on /v1/responses; it keeps no server-side state). An engineer (you don't know
who) did the work and wrote the report below. Judge the work, not the writing style.

Score each 1-10 (10 = what an excellent senior engineer who knows Router would do):
- migration_coverage: how much of the app's production LLM traffic really reaches Router now, relative to what could
  reasonably move (use the outside check's route truth; calls that stay need a real reason).
- behaviour_safety: behaviour preserved (tests/typecheck vs baseline from the check), losses stated honestly, no
  needless risk.
- improvement_depth: does the review think about the system as a whole on Router (where each call sits, who waits,
  cost, failure handling, model choice, design), across levels from quick wins to rework, with Router-specific
  leverage? Few shallow ideas = low; a broad, ranked, well-reasoned set = high. Zero improvements with a strong,
  specific reason can still score mid.
- improvement_quality: are the proposed or built improvements correct, grounded in this code, honest about gains and
  losses, and actionable? Penalise wrong claims and invented numbers.
- report_clarity: would a human reviewing a dashboard understand each decision quickly (plain titles, one-sentence
  summaries, concrete gains/losses)?
- overall: your overall judgment.

Then list the 3 most valuable things the engineer did, the 3 biggest misses (things an expert would have done or
proposed), and anything wrong.

Answer with ONLY a JSON object: {"migration_coverage": n, "behaviour_safety": n, "improvement_depth": n,
"improvement_quality": n, "report_clarity": n, "overall": n, "best": [..3 strings], "misses": [..3 strings],
"wrong": [..strings]}"""


def objective(d):
    doc = json.load(open(os.path.join(d, "MIGRATION.json"))) if os.path.exists(os.path.join(d, "MIGRATION.json")) else {}
    v = json.load(open(os.path.join(d, "validation.json"))) if os.path.exists(os.path.join(d, "validation.json")) else {}
    led = json.load(open(os.path.join(d, "ledger.json"))) if os.path.exists(os.path.join(d, "ledger.json")) else {}
    run = json.load(open(os.path.join(d, "run.json")))
    m = v.get("migration") or {}
    rt = m.get("routes") or {}
    items = doc.get("items") or []
    imps = [i for i in items if i.get("kind") == "improvement"]
    kinds = {}
    for i in items:
        kinds[i.get("kind")] = kinds.get(i.get("kind"), 0) + 1
    levels = {}
    for i in imps:
        levels[i.get("level") or "-"] = levels.get(i.get("level") or "-", 0) + 1
    return {"exit": run.get("exit"), "minutes": round((run.get("seconds") or 0) / 60, 1), "usd": (led.get("cost") or {}).get("agent_usd"),
            "check_ok": m.get("ok"), "verified": len(rt.get("verified") or []), "unverified": len(rt.get("unverified") or []),
            "not_routing": len(rt.get("not_routing") or []), "stays": len(rt.get("stays") or []), "items": len(items), "kinds": kinds,
            "improvements": len(imps), "built": sum(1 for i in imps if i.get("branch")), "levels": levels,
            "branch_ok": sum(1 for b in (v.get("branches") or {}).values() if b.get("ok")), "report_ok": bool(doc)}


def bundle(d):
    run = json.load(open(os.path.join(d, "run.json")))
    trial = run["trial"]
    parts = []
    for f in ("MIGRATION.json",):
        p = os.path.join(d, f)
        parts.append("## The engineer's report (%s)\n%s" % (f, open(p).read()[:30000] if os.path.exists(p) else "(missing)"))
    v = os.path.join(d, "validation.json")
    if os.path.exists(v):
        vv = json.load(open(v))
        for k in ("migration",):
            (vv.get(k) or {}).get("routes", {}).pop("rows", None)
        parts.append("## The outside check (validation.json)\n" + json.dumps(vv)[:12000])
    base = json.load(open(os.path.join(d, "ledger.json"))).get("base") if os.path.exists(os.path.join(d, "ledger.json")) else None
    git = lambda *a: subprocess.run(["git"] + list(a), cwd=trial, capture_output=True, text=True).stdout
    if base:
        parts.append("## Migration diff (base..router-migrate/migration, runtime file excluded)\n" +
                     git("diff", base, "router-migrate/migration", "--", ".", ":!*routerRuntime*", ":!*router_runtime*")[:25000])
    for b in run.get("branches") or []:
        if b.startswith("router-migrate/improve/"):
            parts.append("## Improvement branch %s\n" % b + git("diff", "router-migrate/migration..." + b)[:8000])
    return "\n\n".join(parts)


def judge(d):
    out = os.path.join(d, "judge.json")
    if os.path.exists(out) and "error" not in json.load(open(out)):
        return json.load(open(out))
    prompt = RUBRIC + "\n\n" + bundle(d)
    for _ in range(3):  # a reply that isn't the JSON object is retried, never scored
        r = subprocess.run(["claude", "-p", prompt, "--output-format", "json"], capture_output=True, text=True, timeout=1800)
        try:
            res = json.loads(r.stdout).get("result") or ""
            m = re.search(r"\{.*\}", res, re.S)
            j = json.loads(m.group(0)) if m else {"error": res[:500]}
        except ValueError:
            j = {"error": (r.stdout + r.stderr)[-500:]}
        if "error" not in j and j.get("overall") is not None:
            break
    j["objective"] = objective(d)
    json.dump(j, open(out, "w"), indent=1)
    return j


def table(rnd):
    rows = []
    for d in sorted(glob.glob(os.path.join(HERE, "runs", rnd, "*", "*"))):
        p = os.path.join(d, "judge.json")
        if not os.path.exists(p) or json.load(open(os.path.join(d, "run.json"))).get("invalid"):
            continue
        j = json.load(open(p))
        arm, repo = d.split(os.sep)[-2:]
        rows.append((arm, repo, j))
    keys = ("migration_coverage", "behaviour_safety", "improvement_depth", "improvement_quality", "report_clarity", "overall")
    L = ["# Round %s scores (blind judge, 1-10)" % rnd, "",
         "| arm | repo | " + " | ".join(k.replace("_", " ") for k in keys) + " | verified/unverified/stays | improvements (built) | levels | min | $ |",
         "|" + "---|" * (len(keys) + 7)]
    for arm, repo, j in rows:
        o = j.get("objective") or {}
        L.append("| %s | %s | %s | %s/%s/%s | %s (%s) | %s | %s | %s |" % (arm, repo, " | ".join(str(j.get(k, "-")) for k in keys),
                 o.get("verified"), o.get("unverified"), o.get("stays"), o.get("improvements"), o.get("built"),
                 ", ".join("%s %s" % kv for kv in (o.get("levels") or {}).items()) or "-", o.get("minutes"), o.get("usd")))
    arms = sorted({r[0] for r in rows})
    L += ["", "## Mean per arm", "", "| arm | " + " | ".join(k.replace("_", " ") for k in keys) + " | improvements | min | $ |", "|" + "---|" * (len(keys) + 4)]
    for a in arms:
        js = [r[2] for r in rows if r[0] == a and r[2].get("overall") is not None]
        if not js:
            continue
        mean = lambda k: round(sum(float(j.get(k) or 0) for j in js) / len(js), 1)
        mo = lambda k: round(sum(float((j.get("objective") or {}).get(k) or 0) for j in js) / len(js), 1)
        L.append("| %s | %s | %s | %s | %s |" % (a, " | ".join(str(mean(k)) for k in keys), mo("improvements"), mo("minutes"), mo("usd")))
    open(os.path.join(HERE, "runs", rnd, "SCORES.md"), "w").write("\n".join(L) + "\n")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--parallel", type=int, default=4)
    a = ap.parse_args()
    dirs = sorted(d for d in glob.glob(os.path.join(HERE, "runs", a.round, "*", "*")) if os.path.exists(os.path.join(d, "run.json"))
                  and not json.load(open(os.path.join(d, "run.json"))).get("invalid"))
    order = sorted(dirs, key=lambda d: hashlib.sha1(d.encode()).hexdigest())  # judged in an arm-blind order
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        list(ex.map(judge, order))
    print(table(a.round))


if __name__ == "__main__":
    main()
