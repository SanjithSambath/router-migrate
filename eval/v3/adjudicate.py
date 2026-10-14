#!/usr/bin/env python3
"""Blind adjudication of disagreements between the frozen inventory and the arms' reports (SPEC_V3 5.9).

  .venv/bin/python eval/v3/adjudicate.py collect     # eval/v3/adjudication/questions/<repo>.json (readings anonymized, shuffled)
  (adjudicator agents answer each file per eval/v3/ADJUDICATE_TASK.md -> eval/v3/adjudication/answers/<repo>.json)
  .venv/bin/python eval/v3/adjudicate.py apply       # eval/v3/inventory_adjudicated/<repo>.json (inventory with verdicts applied)

A question is a production site where at least one arm's reported outcome differs from the inventory on the one thing the
headline metric depends on (should this call's traffic move to Router?), plus sites an arm moved that the inventory lacks.
Readings are listed without saying which came from the inventory or which arm, in a seeded random order."""
import glob
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.argv = sys.argv[:1] + sys.argv[1:]
import run_v3 as R  # noqa: E402

QDIR = os.path.join(HERE, "adjudication", "questions")
ADIR = os.path.join(HERE, "adjudication", "answers")
OUT = os.path.join(HERE, "inventory_adjudicated")
MOVE = {"migrated"}


import re


def _neutral(why):
    """Strip markers that reveal which reader wrote a reason (inventory 'Rule 8:', tool reason codes, claim prefixes)."""
    w = re.sub(r"^\s*(?:migrated|kept|flagged|handoff|needs_human|not_a_site)\s+(?:R_[A-Z_]+)?\s*:?\s*", "", why or "")
    w = re.sub(r"\bR_[A-Z_]+\b", "", w)
    w = re.sub(r"\b[Rr]ule\s*\d+\b\s*[:.,]?\s*", "", w)
    return re.sub(r"\s+", " ", w).strip()


def _moves(outcome):
    return outcome in MOVE


def collect(round2=False):
    """round2: only disagreements not already asked in round 1 (e.g. after a scorer fix credits new moves)."""
    asked = set()
    if round2:
        for f in glob.glob(os.path.join(QDIR, "*.json")):
            for q in json.load(open(f))["questions"]:
                asked.add((os.path.basename(f)[:-5], q["file"], int(q["line"])))
    qdir = QDIR + "_round2" if round2 else QDIR
    os.makedirs(qdir, exist_ok=True)
    for name in R.repos():
        inv = R.inventory(name)
        qs = {}
        for arm in R.ARMS:
            dst = os.path.join(R.TRIALS, arm, name)
            if not os.path.exists(os.path.join(R.RUNS, arm, name, "result.json")):
                continue
            measured = R.measure(dst, name)
            reported = R.reported_outcomes(arm, dst, name, measured)
            pairs, missed, extra = R.HA.match(inv, reported)
            for i, p in pairs:
                if _moves(p.get("outcome")) != _moves(i["expected"]):
                    k = "%s:%s" % (i["file"], i["line"])
                    q = qs.setdefault(k, {"file": i["file"], "line": i["line"], "surface": i.get("surface"), "readings": []})
                    q["readings"].append({"outcome": i["expected"], "why": i.get("why", "")[:500]})
                    q["readings"].append({"outcome": p.get("outcome"), "why": (p.get("reason") or "")[:500]})
            moved = [s for s in measured if s["outcome"] == "migrated"]
            _, _, extra_moved = R.HA.match(inv, moved)
            for p in extra_moved:
                k = "%s:%s" % (p["file"], p["line"])
                q = qs.setdefault(k, {"file": p["file"], "line": p["line"], "surface": p.get("surface"), "readings": [],
                                      "not_in_inventory": True})
                q["readings"].append({"outcome": "migrated", "why": (p.get("reason") or "")[:500]})
                q["readings"].append({"outcome": "not a production LLM site / not inventoried", "why": "absent from the inventory"})
        out = []
        rnd = random.Random(int(hashlib.sha256(name.encode()).hexdigest()[:8], 16))
        for k, q in sorted(qs.items()):
            if round2 and any(n == name and f == q["file"] and abs(l - int(q["line"])) <= 3 for n, f, l in asked):
                continue
            seen, rs = set(), []
            for r in q["readings"]:
                key = (r["outcome"], r["why"][:120])
                if key not in seen:
                    seen.add(key)
                    rs.append(r)
            for r in rs:
                r["why"] = _neutral(r["why"])
            rnd.shuffle(rs)
            q["readings"] = rs
            q["id"] = "Q%d" % (len(out) + 1)
            out.append(q)
        commit = R.repos()[name]["commit"]
        json.dump({"repo": name, "commit": commit, "pristine": os.path.join(R.RE.WORK, "pristine", name), "questions": out},
                  open(os.path.join(qdir, name + ".json"), "w"), indent=1)
        print("%-30s %d question(s)" % (name, len(out)))


def apply():
    os.makedirs(OUT, exist_ok=True)
    for name in R.repos():
        d = json.load(open(os.path.join(HERE, "inventory", name + ".json")))
        for ap, qp in ((os.path.join(ADIR, name + ".json"), os.path.join(QDIR, name + ".json")),
                       (os.path.join(ADIR + "_round2", name + ".json"), os.path.join(QDIR + "_round2", name + ".json"))):
            _apply_one(d, ap, qp)
        json.dump(d, open(os.path.join(OUT, name + ".json"), "w"), indent=1)
    print("wrote", OUT)


def _apply_one(d, ap, qp):
    if True:
        if os.path.exists(ap) and os.path.exists(qp):
            qs = {q["id"]: q for q in json.load(open(qp))["questions"]}
            for a in json.load(open(ap)).get("answers", []):
                q = qs.get(a["id"])
                if not q:
                    continue
                hit = [s for s in d["sites"] if s["file"] == q["file"] and abs(int(s["line"]) - int(q["line"])) <= 3]
                if hit:
                    hit[0]["expected_inventory"] = hit[0]["expected"]
                    hit[0]["expected"] = a["expected"]
                    hit[0]["adjudication"] = a.get("why", "")[:500]
                elif a["expected"] != "nonprod" and a.get("is_site", True):
                    d["sites"].append({"file": q["file"], "line": q["line"], "prod": True, "surface": q.get("surface") or "chat",
                                       "expected": a["expected"], "why": "added by adjudication: " + a.get("why", "")[:400],
                                       "adjudication": "added"})


if __name__ == "__main__":
    {"collect": collect, "collect2": lambda: collect(round2=True), "apply": apply}[sys.argv[1]]()
