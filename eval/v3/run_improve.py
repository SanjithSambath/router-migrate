#!/usr/bin/env python3
"""v3 Improve phase on the 10 fresh repos, starting from each repo's arm C result (migrated, `finish` passed).

  .venv/bin/python eval/v3/run_improve.py all --jobs 5      # every repo
  .venv/bin/python eval/v3/run_improve.py one llmcord        # one repo
  .venv/bin/python eval/v3/run_improve.py aggregate          # eval/v3/improve/summary.json + IMPROVE_RESULTS.md

Per repo: copy arm C's trial, record the deterministic cards and the filename-heuristic latency guesses (no judgments), run
`router-migrate improve <copy> --agent claude --no-dashboard` (the agent judges latency, task and cacheability per migrated
feature, with evidence; the tool builds the cards), and save recommendations.json, REPORT.html, the judgments, the
transcript, cost and time to eval/v3/improve/<repo>/."""
import argparse
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
sys.path.insert(0, HERE)
import run_v3 as R  # noqa: E402

OUT = os.path.join(HERE, "improve")
LEVERS = {"tier": ["tier.flex_background"], "fallback": ["fallback.candidates"], "cache": ["cache.key"], "tags": ["attr.metadata"],
          "unlock": ["unlock.vendor_endpoint", "unlock.stated_loss"],
          "model": ["model.deprecated", "model.newer_cheaper_same_family", "model.right_size"]}


def lever(rule):
    return next((k for k, rs in LEVERS.items() if rule in rs or (k == "model" and rule == "model.*")), "other")


def rm_json(args, cwd, env):
    p = subprocess.run([R.RM] + args + ["--json"], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stdout + p.stderr)[-500:]}


def one(name):
    src = os.path.join(R.TRIALS, "C", name)
    dst = os.path.join(R.TRIALS, "I", name)
    out = os.path.join(OUT, name)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    R.sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(src), shlex.quote(dst)) * 2))
    R.retarget(dst, src)
    env = R.agent_env()
    # before: deterministic cards and the filename heuristics, no judgments
    before = rm_json(["improve", dst], dst, env)
    rec0 = json.load(open(os.path.join(dst, ".router-migrate", "recommendations.json")))
    t0 = time.time()
    p = subprocess.run([R.RM, "improve", dst, "--agent", "claude", "--no-dashboard"], cwd=dst, env=env, capture_output=True, text=True,
                       timeout=3000)
    minutes = round((time.time() - t0) / 60, 1)
    open(os.path.join(out, "run.log"), "w").write((p.stdout or "")[-20000:] + (p.stderr or "")[-4000:])
    sm = os.path.join(dst, ".router-migrate")
    rec = json.load(open(os.path.join(sm, "recommendations.json")))
    shutil.copy(os.path.join(sm, "recommendations.json"), os.path.join(out, "recommendations.json"))
    shutil.copy(os.path.join(sm, "REPORT.html"), os.path.join(out, "REPORT.html"))
    led = json.load(open(os.path.join(sm, "ledger.json")))
    tr = os.path.join(sm, "agent_improve_transcript.jsonl")
    if os.path.exists(tr):
        with gzip.open(os.path.join(out, "transcript.jsonl.gz"), "wt") as g:
            g.write(open(tr, encoding="utf-8", errors="replace").read())
    heur = {f["feature"]: f.get("latency_heuristic") for f in before.get("features") or []}
    judg = led.get("judgments") or {}
    cost = (led.get("improve") or {}).get("cost") or {}
    by_lever = {}
    for c in rec["cards"]:
        by_lever[lever(c["rule"])] = by_lever.get(lever(c["rule"]), 0) + 1
    considered = {}
    for x in rec["considered_not_recommended"]:
        considered.setdefault(lever(x["rule"]), []).append(x["reason"][:160])
    ids0, ids1 = {c["id"] for c in rec0["cards"]}, {c["id"] for c in rec["cards"]}
    summary = {
        "repo": name, "commit": R.repos()[name]["commit"], "features": len(before.get("features") or []),
        "judged": sum(1 for f in judg.values() if "latency" in f and "task" in f), "judgments": judg,
        "latency_heuristic_vs_agent": [{"feature": f, "heuristic": heur.get(f), "agent": (j.get("latency") or {}).get("value"),
                                        "why": (j.get("latency") or {}).get("why", "")[:200]} for f, j in judg.items()],
        "cards": len(rec["cards"]), "cards_by_lever": by_lever, "considered_by_lever": considered,
        "cards_before_judgments": len(rec0["cards"]), "cards_added_by_judgments": sorted(ids1 - ids0),
        "cards_removed_by_judgments": sorted(ids0 - ids1),
        "cards_with_agent_rationale": sum(1 for c in rec["cards"] if c.get("agent_rationale")),
        "usd": cost.get("usd") or 0.0, "agent_minutes": cost.get("minutes"), "turns": cost.get("turns"), "minutes": minutes,
        "exit": p.returncode, "card_titles": [c["title"] for c in rec["cards"]]}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print("%-30s features %d judged %d cards %d (before %d) levers %s $%.2f %.1f min" % (
        name, summary["features"], summary["judged"], summary["cards"], summary["cards_before_judgments"], by_lever,
        summary["usd"], minutes), flush=True)
    return summary


def aggregate():
    rows = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(OUT, "*", "summary.json")))]
    tot = {"repos": len(rows), "features": sum(r["features"] for r in rows), "judged": sum(r["judged"] for r in rows),
           "cards": sum(r["cards"] for r in rows), "cards_before_judgments": sum(r["cards_before_judgments"] for r in rows),
           "usd": round(sum(r["usd"] for r in rows), 2), "cards_with_agent_rationale": sum(r["cards_with_agent_rationale"] for r in rows)}
    lev = {}
    for r in rows:
        for k, v in r["cards_by_lever"].items():
            lev[k] = lev.get(k, 0) + v
    tot["cards_by_lever"] = lev
    flips = [dict(x, repo=r["repo"]) for r in rows for x in r["latency_heuristic_vs_agent"] if x["heuristic"] and x["agent"] and x["heuristic"] != x["agent"]]
    tot["latency_disagreements"] = len(flips)
    json.dump({"totals": tot, "repos": rows}, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    names = [("tier", "Tiers / Flex"), ("fallback", "Fallbacks"), ("cache", "Caching"), ("tags", "Spend tags"), ("unlock", "Unlocks"), ("model", "Model recs")]
    L = ["# v3 Improve phase on the 10 fresh repos", "",
         "Generated by `eval/v3/run_improve.py aggregate`. Each repo starts from its arm C result (migrated by the agent + toolkit, "
         "`finish` passed). `router-migrate improve <repo> --agent claude` then has the agent judge every migrated feature's latency "
         "class, task and prompt cacheability with cited lines, and the tool turns those judgments plus catalog facts into cards. "
         "Nothing is applied: every card waits for a human. Per repo: eval/v3/improve/<repo>/ (recommendations.json, REPORT.html, "
         "summary.json, transcript).", "",
         "## Totals", "",
         "| repos | migrated features | judged (latency + task) | cards | cards before judgments | cards with agent rationale | agent $ |",
         "|---|---|---|---|---|---|---|",
         "| %d | %d | %d | %d | %d | %d | %.2f |" % (tot["repos"], tot["features"], tot["judged"], tot["cards"], tot["cards_before_judgments"],
                                                   tot["cards_with_agent_rationale"], tot["usd"]), "",
         "Cards by lever: " + ", ".join("%s **%d**" % (n, lev.get(k, 0)) for k, n in names) + ".", "",
         "## Per repo", "",
         "| repo | features | judged | " + " | ".join(n for _, n in names) + " | cards (before) | $ | min |",
         "|---|---|---|" + "---|" * len(names) + "---|---|---|"]
    for r in rows:
        L.append("| %s | %d | %d | %s | %d (%d) | %.2f | %s |" % (r["repo"], r["features"], r["judged"],
                                                             " | ".join(str(r["cards_by_lever"].get(k, 0)) for k, _ in names),
                                                             r["cards"], r["cards_before_judgments"], r["usd"], r["minutes"]))
    L += ["", "## Every lever, every repo", "",
          "Each cell is the number of cards, or why the lever has none in that repo (the first reason from the repo's "
          "considered-not-recommended list). Every lever is evaluated on every migrated feature; a lever with no card is a "
          "decision with a reason, never silence.", "",
          "| repo | " + " | ".join(n for _, n in names) + " |", "|---|" + "---|" * len(names)]
    for r in rows:
        cells = []
        for k, _ in names:
            n = r["cards_by_lever"].get(k, 0)
            if n:
                cells.append("**%d**" % n)
            elif k == "unlock":  # unlock rules read the sites that did NOT move, so they apply with or without migrated features
                why = (r["considered_by_lever"].get(k) or [""])[0]
                cells.append((why[:70] + ("…" if len(why) > 70 else "")).replace("|", "/") if why else
                             "no unlock candidate: no OpenAI-compatible vendor endpoint on a catalog model, no droppable beta")
            elif not r["features"]:
                cells.append("no migrated feature")
            else:
                why = (r["considered_by_lever"].get(k) or [""])[0]
                cells.append((why[:70] + ("…" if len(why) > 70 else "")).replace("|", "/") if why else "not applicable")
        L.append("| %s | %s |" % (r["repo"], " | ".join(cells)))
    L += ["", "## Where the agent's judgment differed from the filename heuristic (latency class)", ""]
    if flips:
        L += ["| repo | feature | heuristic | agent | agent's reason |", "|---|---|---|---|---|"]
        L += ["| %s | `%s` | %s | %s | %s |" % (x["repo"], x["feature"], x["heuristic"], x["agent"], x["why"].replace("|", "/")) for x in flips]
    else:
        L.append("None: on these repos the agent agreed with the heuristic on every migrated feature.")
    L += ["", "## Levers with no card, and why (from the considered-not-recommended lists)", ""]
    for k, n in names:
        reasons = {}
        for r in rows:
            for why in r["considered_by_lever"].get(k, []):
                reasons[why] = reasons.get(why, 0) + 1
        if reasons:
            top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
            L.append("- **%s**: " % n + "; ".join("%s (x%d)" % (w, c) for w, c in top))
    notes = os.path.join(HERE, "IMPROVE_NOTES.md")
    if os.path.exists(notes):
        L += ["", open(notes).read().rstrip()]
    open(os.path.join(HERE, "IMPROVE_RESULTS.md"), "w").write("\n".join(L) + "\n")
    print(json.dumps(tot, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    a1 = sp.add_parser("all")
    a1.add_argument("--jobs", type=int, default=5)
    a2 = sp.add_parser("one")
    a2.add_argument("name")
    sp.add_parser("aggregate")
    a = ap.parse_args()
    if a.cmd == "one":
        one(a.name)
    elif a.cmd == "all":
        with ThreadPoolExecutor(a.jobs) as ex:
            list(ex.map(lambda n: R._safe(one, n), list(R.repos())))
        aggregate()
    else:
        aggregate()


if __name__ == "__main__":
    main()
