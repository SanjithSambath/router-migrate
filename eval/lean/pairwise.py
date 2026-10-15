"""Pairwise blind judge: absolute 1-10 scores bunch at 8, so each arm is compared head-to-head with a reference arm on
the same repo. The reviewer sees two engineers' work (order randomised, arms hidden) and picks the better one.

    .venv/bin/python eval/lean/pairwise.py --round r2 --ref lean [--parallel 6]

Writes eval/lean/runs/<round>/pairs/<arm>__<ref>__<repo>.json and eval/lean/runs/<round>/PAIRS.md."""
import argparse
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import subprocess

import judge

HERE = os.path.dirname(os.path.abspath(__file__))

PROMPT = """You are a CTO choosing between two engineers' work on the same task: moving one application's LLM traffic
onto Ramp's Router (an LLM gateway: one key, many vendors' models; serves /v1/responses and /v1/messages; Flex tier,
model fallbacks, prompt cache keys and provider_timeout are Router-only fields on /v1/responses; no server-side
state), keeping behaviour intact, and then deciding what to change about the system on Router. Each engineer's report,
the outside check's result and their diffs follow. Judge the substance, not length or polish: which work would you
rather ship and act on? Weigh traffic really moved, behaviour preserved, honesty about losses, and above all the depth
and correctness of the system-level judgment (are the improvements the ones an expert would make for THIS app, ranked
well, with real Router leverage, and are the claims right?).

Answer with ONLY a JSON object: {"winner": "A" | "B" | "tie", "margin": "slight" | "clear" | "large",
"why": "two sentences", "A_best": "...", "B_best": "...", "A_worst": "...", "B_worst": "..."}"""


def pair(rnd, arm, ref, repo):
    out_dir = os.path.join(HERE, "runs", rnd, "pairs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s__%s__%s.json" % (arm, ref, repo))
    if os.path.exists(out) and "winner" in json.load(open(out)):
        return json.load(open(out))
    da, db = (os.path.join(HERE, "runs", rnd, x, repo) for x in (arm, ref))
    swap = int(hashlib.sha1(out.encode()).hexdigest(), 16) % 2  # which side the arm sits on
    first, second = (db, da) if swap else (da, db)
    cut = lambda s: s[:40000]
    prompt = PROMPT + "\n\n# ENGINEER A\n" + cut(judge.bundle(first)) + "\n\n# ENGINEER B\n" + cut(judge.bundle(second))
    j = {}
    for _ in range(3):
        r = subprocess.run(["claude", "-p", prompt, "--output-format", "json"], capture_output=True, text=True, timeout=1800)
        try:
            res = json.loads(r.stdout).get("result") or ""
            m = re.search(r"\{.*\}", res, re.S)
            j = json.loads(m.group(0)) if m else {"error": res[:500]}
        except ValueError:
            j = {"error": (r.stdout + r.stderr)[-500:]}
        if j.get("winner") in ("A", "B", "tie"):
            break
    if j.get("winner") in ("A", "B"):
        arm_side = "B" if swap else "A"
        j["result"] = "win" if j["winner"] == arm_side else "loss"
    elif j.get("winner") == "tie":
        j["result"] = "tie"
    j.update(arm=arm, ref=ref, repo=repo, arm_was=("B" if swap else "A"))
    json.dump(j, open(out, "w"), indent=1)
    return j


def valid(rnd, arm, repo):
    p = os.path.join(HERE, "runs", rnd, arm, repo, "run.json")
    return os.path.exists(p) and not json.load(open(p)).get("invalid")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--parallel", type=int, default=6)
    a = ap.parse_args()
    arms = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "runs", a.round, "*"))
                  if os.path.isdir(p) and os.path.basename(p) not in ("pairs", a.ref))
    jobs = [(arm, repo) for arm in arms for repo in sorted(os.listdir(os.path.join(HERE, "runs", a.round, arm)))
            if valid(a.round, arm, repo) and valid(a.round, a.ref, repo)]
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        res = list(ex.map(lambda j: pair(a.round, j[0], a.ref, j[1]), jobs))
    w = {"slight": 1, "clear": 2, "large": 3}
    L = ["# Round %s: each arm vs %s, head-to-head (blind, order randomised)" % (a.round, a.ref), "",
         "| arm | repo | result | margin | why |", "|---|---|---|---|---|"]
    for r in res:
        L.append("| %s | %s | %s | %s | %s |" % (r["arm"], r["repo"], r.get("result", "error"), r.get("margin", "-"),
                                                (r.get("why") or r.get("error", ""))[:300].replace("|", "/").replace("\n", " ")))
    L += ["", "| arm | wins | ties | losses | net margin (slight 1, clear 2, large 3) |", "|---|---|---|---|---|"]
    for arm in arms:
        rs = [r for r in res if r["arm"] == arm]
        net = sum((w.get(r.get("margin"), 0) if r.get("result") == "win" else -w.get(r.get("margin"), 0) if r.get("result") == "loss" else 0) for r in rs)
        L.append("| %s | %d | %d | %d | %+d |" % (arm, sum(r.get("result") == "win" for r in rs), sum(r.get("result") == "tie" for r in rs),
                                               sum(r.get("result") == "loss" for r in rs), net))
    open(os.path.join(HERE, "runs", a.round, "PAIRS.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
