#!/usr/bin/env python3
"""Score router-migrate's plan on the held-out repos against blind, rubric-based inventories (eval/heldout/inventory/*.json).

  .venv/bin/python eval/heldout_audit.py            -> eval/heldout/AUDIT.md + eval/heldout/audit.json

Matching: a production inventory site matches a production plan site in the same file whose line is within 3 and whose surface
family is the same (nearest line wins; each plan site is used once). Reported:
  - discovery recall: inventory prod sites the scanner found at all; scanner-only sites (found by the tool, not in the inventory)
  - outcome agreement on matched sites, and the confusion matrix (tool outcome x expected outcome)
  - migrated precision (tool said migrated and the rubric agrees) and migrated recall (rubric says migrated and the tool migrated it)
Every disagreement is listed for adjudication; the adjudicated verdicts (eval/heldout/adjudication.json, if present) are applied
on top: {"<repo>|<file>:<line>": {"correct": "tool"|"inventory"|"both-wrong", "note": "..."}}.
"""
import collections
import json
import os

E = os.path.dirname(os.path.abspath(__file__))
H = os.path.join(E, "heldout")

FAMILY = {
    "chat.completions": "chat", "chat.completions.parse": "chat", "chat.completions.stream": "chat", "framework.chat": "chat",
    "other.chat": "chat", "chat": "chat", "completions.legacy": "completions", "completions": "completions",
    "responses": "responses", "messages": "messages", "messages.stream": "messages", "messages.count_tokens": "messages",
    "messages.beta": "messages", "embeddings": "embeddings", "framework.embeddings": "embeddings", "other.embeddings": "embeddings",
}


def fam(surface):
    s = surface or ""
    return FAMILY.get(s, s.split(".")[0])


def load_plan(name):
    p = os.path.join(H, "logs", name + ".plan.json")
    return json.load(open(p)) if os.path.exists(p) else None


def match(inv_sites, plan_sites):
    used, pairs, missed = set(), [], []
    for i in inv_sites:
        best = None
        for j, p in enumerate(plan_sites):
            if j in used or p["file"] != i["file"]:
                continue
            fi, fp = fam(i.get("surface")), fam(p.get("surface"))
            if fi != fp and not ({fi, fp} <= {"chat", "responses"} and i.get("sdk") == "vercel-ai"):
                continue
            d = abs(int(p["line"]) - int(i["line"]))
            if d <= 3 and (best is None or d < best[0]):
                best = (d, j)
        if best is None:
            missed.append(i)
        else:
            used.add(best[1])
            pairs.append((i, plan_sites[best[1]]))
    extra = [p for j, p in enumerate(plan_sites) if j not in used]
    return pairs, missed, extra


def main():
    adj_p = os.path.join(H, "adjudication.json")
    adj = json.load(open(adj_p)) if os.path.exists(adj_p) else {}
    repos = json.load(open(os.path.join(H, "repos.json")))
    conf = collections.Counter()
    tot = collections.Counter()
    rows, disagreements = [], []
    for r in repos:
        name = r["name"]
        ip = os.path.join(H, "inventory", name + ".json")
        plan = load_plan(name)
        if not os.path.exists(ip) or plan is None:
            rows.append({"repo": r["repo"], "status": "missing " + ("inventory" if not os.path.exists(ip) else "plan")})
            continue
        inv = [s for s in json.load(open(ip))["sites"] if s.get("prod") and s.get("expected") != "nonprod"]
        ps = [s for s in plan["sites"] if s.get("prod")]
        pairs, missed, extra = match(inv, ps)
        row = collections.Counter(inventory=len(inv), tool=len(ps), matched=len(pairs), missed=len(missed), extra=len(extra),
                                  agree=0, tool_migrated=0, exp_migrated=0, both_migrated=0)
        for i, p in pairs:
            t, x = p["outcome"], i["expected"]
            conf[(t, x)] += 1
            key = "%s|%s:%s" % (r["repo"], i["file"], i["line"])
            verdict = adj.get(key, {}).get("correct")
            agree = t == x or verdict == "tool"
            row["agree"] += agree
            row["tool_migrated"] += t == "migrated"
            row["exp_migrated"] += x == "migrated"
            row["both_migrated"] += t == "migrated" and (x == "migrated" or verdict == "tool")
            if not agree:
                disagreements.append({"key": key, "tool": t, "expected": x, "tool_reason": p["reason"][:220], "inventory_why": i.get("why", "")[:220],
                                      "verdict": verdict, "note": adj.get(key, {}).get("note")})
        for i in missed:
            disagreements.append({"key": "%s|%s:%s" % (r["repo"], i["file"], i["line"]), "tool": "NOT FOUND", "expected": i["expected"],
                                  "inventory_why": i.get("why", "")[:220], "surface": i.get("surface"), "sdk": i.get("sdk"),
                                  "verdict": adj.get("%s|%s:%s" % (r["repo"], i["file"], i["line"]), {}).get("correct")})
        for p in extra:
            disagreements.append({"key": "%s|%s:%s" % (r["repo"], p["file"], p["line"]), "tool": p["outcome"], "expected": "NOT IN INVENTORY",
                                  "tool_reason": p["reason"][:220], "surface": p.get("surface"), "sdk": p.get("sdk"),
                                  "verdict": adj.get("%s|%s:%s" % (r["repo"], p["file"], p["line"]), {}).get("correct")})
        relevant = [i for i in inv if i["expected"] in ("migrated", "flagged", "handoff")]
        row["relevant"] = len(relevant)
        row["relevant_found"] = sum(1 for i in relevant if i not in missed)
        row["exp_migrated_found"] = sum(1 for i in inv if i["expected"] == "migrated" and i not in missed)
        row["exp_migrated_all"] = sum(1 for i in inv if i["expected"] == "migrated")
        tot.update(row)
        rows.append(dict(row, repo=r["repo"], status="ok"))  # Counter drops nothing here: keys were initialised
    pct = lambda a, b: "%.0f%%" % (100.0 * a / b) if b else "-"  # noqa: E731
    summary = {
        "repos_scored": sum(1 for x in rows if x.get("status") == "ok"),
        "inventory_prod_sites": tot["inventory"], "tool_prod_sites": tot["tool"], "matched": tot["matched"],
        "discovery_recall": pct(tot["matched"], tot["inventory"]), "scanner_only_sites": tot["extra"],
        "outcome_agreement": pct(tot["agree"], tot["matched"]),
        "router_relevant_discovery_recall": pct(tot["relevant_found"], tot["relevant"]),
        "migrated_precision": pct(tot["both_migrated"], tot["tool_migrated"]),
        "migrated_recall_on_found_sites": pct(tot["both_migrated"], tot["exp_migrated"]),
        "migrated_recall_end_to_end": pct(tot["both_migrated"], tot["exp_migrated_all"]),
        "counts": dict(tot),
    }
    json.dump({"summary": summary, "rows": rows, "confusion": {"%s->%s" % k: v for k, v in conf.items()}, "disagreements": disagreements},
              open(os.path.join(H, "audit.json"), "w"), indent=1)
    outs = ["migrated", "kept", "flagged", "handoff"]
    L = ["# Held-out audit: tool plan vs blind rubric inventories", "",
         "Generated by `eval/heldout_audit.py`. Rubric: `eval/heldout/RUBRIC.md`.", "",
         "| metric | value |", "|---|---|"]
    L += ["| %s | %s |" % (k, v) for k, v in summary.items() if k != "counts"]
    L += ["", "## Confusion (rows: tool outcome, columns: rubric expected outcome; matched production sites)", "",
          "| tool \\ expected | " + " | ".join(outs) + " |", "|---|" + "---|" * len(outs)]
    for t in outs:
        L.append("| %s | %s |" % (t, " | ".join(str(conf[(t, x)]) for x in outs)))
    L += ["", "## Per repo", "", "| repo | inventory | tool | matched | missed | scanner-only | agree | migrated (tool / rubric / both) |", "|---|---|---|---|---|---|---|---|"]
    for x in rows:
        if x.get("status") != "ok":
            L.append("| %s | %s |" % (x["repo"], x["status"]))
            continue
        L.append("| %s | %d | %d | %d | %d | %d | %d/%d | %d / %d / %d |" % (x["repo"], x["inventory"], x["tool"], x["matched"], x["missed"], x["extra"],
                                                                         x["agree"], x["matched"], x["tool_migrated"], x["exp_migrated"], x["both_migrated"]))
    L += ["", "## Disagreements (%d)" % len(disagreements), ""]
    for d in disagreements:
        L.append("- `%s`: tool **%s**, rubric **%s**%s. Tool: %s. Inventory: %s" % (
            d["key"], d["tool"], d["expected"], (" (adjudicated: %s%s)" % (d["verdict"], ", " + d["note"] if d.get("note") else "")) if d.get("verdict") else "",
            d.get("tool_reason", "-"), d.get("inventory_why", "-")))
    open(os.path.join(H, "AUDIT.md"), "w").write("\n".join(L) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
