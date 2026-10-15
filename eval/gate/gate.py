"""Release gate: a candidate arm against the baseline (the current default), head-to-head on both judge rubrics, plus
the outside check. Reuses eval/lean: run_arms.py runs the arms, pairwise.py judges each pair.

    .venv/bin/python eval/gate/gate.py --candidate open --baseline ultra_levels --round r4 [--repos working] --judge-only

Runs and cached judgments live in eval/lean/runs/<round>/ (shared with eval/lean); GATE.md goes to
eval/gate/runs/<round>/GATE.md. --judge-only skips running; --report only re-renders from cached judgments (no judge
calls; a missing judgment is listed, never fetched)."""
import argparse
import concurrent.futures as cf
import json
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LEAN = os.path.join(ROOT, "eval", "lean")
sys.path.insert(0, LEAN)
import pairwise  # noqa: E402

RUNS = os.path.join(LEAN, "runs")  # run outputs and pair judgments, per round
OUT = os.path.join(HERE, "runs")  # GATE.md, per round
WORKING = ["sec-insights", "AI-Scientist", "chatgpt-telegram-bot", "llmcord", "OpenManus"]
HELDOUT = ["computer_use_ootb", "obsidian-copilot", "obsidian-textgenerator-plugin", "openui", "page-assist"]
RUBRICS = ("base", "open")
MIN_PAIRS = 3


def repo_list(spec):
    sets = {"working": WORKING, "heldout": HELDOUT, "all": WORKING + HELDOUT}
    return list(sets[spec]) if spec in sets else [r for r in spec.split(",") if r]


def run_status(rnd, arm, repo):
    p = os.path.join(RUNS, rnd, arm, repo, "run.json")
    if not os.path.exists(p):
        return "missing"
    return "invalid" if json.load(open(p)).get("invalid") else "ok"


def outside(rnd, arm, repo):
    p = os.path.join(RUNS, rnd, arm, repo, "validation.json")
    m = (json.load(open(p)).get("migration") or {}) if os.path.exists(p) else {}
    rt = m.get("routes") or {}
    o = {k: len(rt.get(k) or []) for k in ("verified", "unverified", "not_routing")}
    o.update(ok=m.get("ok") is True, tests=(m.get("tests") or {}).get("status") or "-")
    return o


def pair_path(rnd, cand, base, repo, rubric):
    return os.path.join(RUNS, rnd, "pairs", "%s__%s__%s%s.json" % (cand, base, repo, "" if rubric == "base" else "__" + rubric))


def cached(rnd, cand, base, repo, rubric):
    p = pair_path(rnd, cand, base, repo, rubric)
    j = json.load(open(p)) if os.path.exists(p) else {}
    return j if j.get("result") in ("win", "loss", "tie") else None


def run_arms_missing(rnd, arms, repos, parallel):
    """Runs every arm x repo without a valid run.json (missing, or cut off by an outage or quota)."""
    os.environ.setdefault("RM_PREP_TAG", "_" + rnd)
    import run_arms
    known = run_arms.load_arms()
    unknown = [a for a in arms if a not in known]
    if unknown:
        raise SystemExit("unknown arm(s): %s (see eval/lean/arms.json)" % ", ".join(unknown))
    jobs = [(arm, repo) for repo in repos for arm in arms if run_status(rnd, arm, repo) != "ok"]
    if not jobs:
        return
    signal.signal(signal.SIGTERM, run_arms._stop)
    signal.signal(signal.SIGINT, run_arms._stop)
    with cf.ThreadPoolExecutor(parallel) as ex:
        futs = {ex.submit(run_arms.one, rnd, arm, repo, None): (arm, repo) for arm, repo in jobs}
        for f in cf.as_completed(futs):
            try:
                print(*f.result(), flush=True)
            except Exception as e:  # one failed run never stops the others
                print(*futs[f], "ERROR", repr(e)[:300], flush=True)


def judgments(rnd, cand, base, repos, parallel, call):
    """{(repo, rubric): judgment or None} for every pair whose two runs are valid. call=False reads the cache only."""
    jobs = [(repo, rub) for repo in repos for rub in RUBRICS
            if run_status(rnd, cand, repo) == "ok" and run_status(rnd, base, repo) == "ok"]

    def get(job):
        repo, rub = job
        if call:
            j = pairwise.pair(rnd, cand, base, repo, rub)
            return j if j.get("result") in ("win", "loss", "tie") else None
        return cached(rnd, cand, base, repo, rub)

    with cf.ThreadPoolExecutor(max(1, parallel)) as ex:
        return dict(zip(jobs, ex.map(get, jobs)))


def decide(rnd, cand, base, repos, js):
    """The verdict rule. Returns a dict with verdict, reasons, excluded, missing, rows, tallies."""
    rows, excluded, missing = [], [], []
    for repo in repos:
        st = {"candidate": run_status(rnd, cand, repo), "baseline": run_status(rnd, base, repo)}
        bad = ["%s run %s" % (k, "invalid (outage or quota)" if v == "invalid" else "missing") for k, v in st.items() if v != "ok"]
        if bad:
            excluded.append((repo, "; ".join(bad)))
            continue
        row = {"repo": repo, "cand": outside(rnd, cand, repo), "base": outside(rnd, base, repo),
               "j": {rub: js.get((repo, rub)) for rub in RUBRICS}}
        missing += [(repo, rub) for rub in RUBRICS if row["j"][rub] is None]
        rows.append(row)
    judged = [r for r in rows if all(r["j"].values())]
    reasons, tally = [], {}
    for rub in RUBRICS:
        res = [r["j"][rub]["result"] for r in judged]
        w, t, l = res.count("win"), res.count("tie"), res.count("loss")
        tally[rub] = (w, t, l)
        if l > w:
            reasons.append("%s rubric: %d wins < %d losses" % (rub, w, l))
    for r in rows:  # the objective check counts on every pair with two valid runs, judged or not
        c, b = r["cand"], r["base"]
        if b["ok"] and not c["ok"]:
            reasons.append("%s: candidate fails the outside check where the baseline passes" % r["repo"])
        if c["verified"] < b["verified"]:
            reasons.append("%s: candidate verified routes %d < baseline %d" % (r["repo"], c["verified"], b["verified"]))
    if len(judged) < MIN_PAIRS:
        verdict = "INSUFFICIENT"
        reasons.insert(0, "only %d valid judged pair(s); need %d" % (len(judged), MIN_PAIRS))
    else:
        verdict = "HOLD" if reasons else "SHIP"
    return {"verdict": verdict, "reasons": reasons, "excluded": excluded, "missing": missing, "rows": rows,
            "pairs": len(judged), "tally": tally}


def verdict_line(rnd, cand, base, d):
    t = ", ".join("%s %d-%d-%d" % ((rub,) + d["tally"][rub]) for rub in RUBRICS)
    return "%s: %s vs %s on %s (W-T-L %s; %d valid pairs)" % (d["verdict"], cand, base, rnd, t, d["pairs"])


def render(rnd, cand, base, d):
    cell = lambda s, n: (s or "").replace("|", "/").replace("\n", " ")[:n]
    L = ["# Release gate: %s vs %s, round %s" % (cand, base, rnd), "", "**%s**" % verdict_line(rnd, cand, base, d), ""]
    if d["reasons"]:
        L += ["Reasons:", ""] + ["- " + r for r in d["reasons"]] + [""]
    L += ["Rule: SHIP if on both rubrics wins >= losses (ties count as neither), the candidate never fails the outside "
          "check where the baseline passes, and its verified-route count is never below the baseline's on a repo. "
          "Fewer than %d valid pairs: INSUFFICIENT." % MIN_PAIRS, ""]
    for rub in RUBRICS:
        L += ["## Head-to-head, %s rubric (W-T-L %d-%d-%d)" % ((rub,) + d["tally"][rub]), "",
              "| repo | result | margin | why |", "|---|---|---|---|"]
        for r in d["rows"]:
            j = r["j"][rub]
            L.append("| %s | %s | %s | %s |" % ((r["repo"], j["result"], j.get("margin", "-"), cell(j.get("why"), 240))
                                                if j else (r["repo"], "not judged", "-", "")))
        L.append("")
    L += ["## Outside check (candidate / baseline)", "",
          "| repo | check ok | verified | unverified | not routing | tests |", "|---|---|---|---|---|---|"]
    for r in d["rows"]:
        c, b = r["cand"], r["base"]
        L.append("| %s | %s / %s | %d / %d | %d / %d | %d / %d | %s / %s |" % (
            r["repo"], "yes" if c["ok"] else "NO", "yes" if b["ok"] else "NO", c["verified"], b["verified"],
            c["unverified"], b["unverified"], c["not_routing"], b["not_routing"], c["tests"], b["tests"]))
    L.append("")
    if d["excluded"]:
        L += ["## Excluded", ""] + ["- %s: %s" % e for e in d["excluded"]] + [""]
    if d["missing"]:
        L += ["## Missing judgments (pair excluded from the head-to-head)", ""] + \
             ["- %s, %s rubric" % m for m in d["missing"]] + [""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="release gate: candidate arm vs baseline arm")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--repos", default="all", help="working | heldout | all | a,b,c")
    ap.add_argument("--round", default="gate", help="results in eval/lean/runs/<round>/, GATE.md in eval/gate/runs/<round>/")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--judge-only", action="store_true", help="skip running; judge what exists")
    ap.add_argument("--report", action="store_true", help="re-render GATE.md from cached judgments only")
    a = ap.parse_args(argv)
    repos = repo_list(a.repos)
    if not (a.judge_only or a.report):
        run_arms_missing(a.round, [a.candidate, a.baseline], repos, a.parallel)
    js = judgments(a.round, a.candidate, a.baseline, repos, a.parallel, call=not a.report)
    d = decide(a.round, a.candidate, a.baseline, repos, js)
    out = os.path.join(OUT, a.round)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "GATE.md"), "w").write(render(a.round, a.candidate, a.baseline, d) + "\n")
    print(verdict_line(a.round, a.candidate, a.baseline, d))
    for r in d["reasons"]:
        print("  - " + r)
    return d


if __name__ == "__main__":
    main()
