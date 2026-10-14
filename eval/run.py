#!/usr/bin/env python3
"""One entrypoint for agent eval runs: every (arm, repo) trial of a set, resumable, safe to run detached.

  nohup .venv/bin/python eval/run.py --set heldout2 --arms D3min,D3free,B --repos all --jobs 6 > eval/heldout2/run.log 2>&1 &
  .venv/bin/python eval/run.py --set v3 --arms D3min --repos llmcord,openui --dry-run      # what would run, nothing spent

Sets: v3 (eval/v3, results in eval/v3/runs), dev (eval/repos.json, results in eval/v3/runs_dev), heldout2 (eval/heldout2:
REPOS.json + recipes/ + inventory/, prepared under eval/work/heldout2, results in eval/heldout2/runs).

Arms are run labels: D3min / D3free (the D3 harness: `router-migrate run <dst> --agent claude --arm min|free`), B2_r<k>,
C_r<k>, Cnf_r<k>, D_r<k> (eval/v3/ablation_b2.py), and A / B / B_prime / C (eval/v3/run_v3.py). Any label may carry a
replicate suffix (_r<k>). Repos run in parallel (--jobs); one repo's arms run one after another (recipes share an env).

Per run, <runs>/<arm>/<repo>/status.json says where it is ({status: running|done|invalid|failed, attempts: [...]}) and
<runs>/DONE.txt gets one line when it finishes. Resume is idempotent: a run whose result.json exists and is valid is
skipped; a run marked running by a live process on this host is left alone.

Infrastructure failures are never scored: a run whose agent transcript ends in an API error ("API Error", "Connection
lost"), whose agent produced no output, whose harness crashed, or whose agent made no per-site decisions is invalid
(score_moved marks it). It is retried up to --retries times from a fresh copy (the invalid attempt's files are kept under
<runs>/<arm>/_attempts/<repo>.<k>/); if every attempt is invalid the run is marked invalid and left out of every table
(the reports count invalid runs separately).
"""
import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V3 = os.path.join(ROOT, "eval", "v3")
SETS = ("v3", "dev", "heldout", "heldout2")
ABLATION_BASES = ("B2", "Cnf", "D", "D3min", "D3free")
_DONE_LOCK = threading.Lock()
R = B = SM = None  # eval/v3 modules, imported once RM_EVAL_SET is set


def log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_modules(eval_set):
    global R, B, SM
    os.environ["RM_EVAL_SET"] = eval_set  # read by run_v3 at import: picks REPOS.json, recipes, work dir and runs dir
    sys.path.insert(0, V3)
    import run_v3
    import ablation_b2
    import score_moved
    R, B, SM = run_v3, ablation_b2, score_moved


# ------------------------------------------------------------------------------------------------ status
def out_dir(arm, repo):
    return os.path.join(R.RUNS, arm, repo)


def read_json(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def read_status(arm, repo):
    return read_json(os.path.join(out_dir(arm, repo), "status.json")) or {}


def write_status(arm, repo, st):
    d = out_dir(arm, repo)
    os.makedirs(d, exist_ok=True)
    st = dict(st, arm=arm, repo=repo, set=R.EVAL_SET, updated=time.strftime("%Y-%m-%dT%H:%M:%S"))
    tmp = os.path.join(d, ".status.json.%d.tmp" % os.getpid())
    json.dump(st, open(tmp, "w"), indent=1, default=str)
    os.replace(tmp, os.path.join(d, "status.json"))
    return st


def done_line(arm, repo, status, detail=""):
    with _DONE_LOCK:
        os.makedirs(R.RUNS, exist_ok=True)
        with open(os.path.join(R.RUNS, "DONE.txt"), "a") as f:
            f.write("%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), arm, repo, status, detail.replace("\n", " ")[:300]))


def owned_elsewhere(st):
    """A status another live runner on this host is working on."""
    if st.get("status") != "running" or st.get("host") != socket.gethostname() or st.get("pid") == os.getpid():
        return False
    try:
        os.kill(int(st.get("pid")), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False  # stale: that runner is gone


def verdict(arm, repo):
    """(valid, why) for the run's saved result: valid when result.json exists, parses, and neither it nor sites_moved.json
    marks the run invalid."""
    d = out_dir(arm, repo)
    res = read_json(os.path.join(d, "result.json"))
    if res is None:
        return False, "no result.json" if not os.path.exists(os.path.join(d, "result.json")) else "result.json unreadable"
    sm = read_json(os.path.join(d, "sites_moved.json")) or {}
    why = res.get("invalid") or sm.get("invalid")
    return (False, why) if why else (True, None)


def summary(arm, repo):
    sm = read_json(os.path.join(out_dir(arm, repo), "sites_moved.json")) or {}
    b = sm.get("buckets") or {}
    return "moved=%s/%s one-approval-away=%s surfaced=%s kept=%s not-routing=%s missed=%s wrong=%s destructive=%s broken=%s $%.2f %smin" % (
        sm.get("sites_moved_movable", sm.get("sites_moved")), sm.get("movable_total"), sm.get("one_approval_away"),
        b.get("surfaced_for_decision"), b.get("correctly_kept"), b.get("claimed_not_routing"), b.get("missed"), sm.get("sites_moved_wrong"),
        (sm.get("destructive") or {}).get("count"), sm.get("broken"), sm.get("usd") or 0, sm.get("minutes"))


# ------------------------------------------------------------------------------------------------ dispatch
def base(label):
    return label.split("_r")[0]


def dispatch(arm, repo):
    """Run one trial (agent + scoring). Existing harness arms go to ablation_b2.trial; D3min / D3free are its D3 hook
    (`router-migrate run <dst> --agent claude --arm min|free`); the original v3 arms go to run_v3.trial."""
    b = base(arm)
    if b in ABLATION_BASES or (b == "C" and "_r" in arm):
        return B.trial(arm, repo)
    if arm in R.ARMS:
        return R.trial(arm, repo)
    raise SystemExit("unknown arm %r: use D3min, D3free, B2_r<k>, C_r<k>, Cnf_r<k>, D_r<k>, A, B, B_prime or C" % arm)


def archive(arm, repo, k):
    """Keep an invalid attempt's files (minus status.json) under <runs>/<arm>/_attempts/<repo>.<k>/."""
    d = out_dir(arm, repo)
    if not os.path.isdir(d) or not [f for f in os.listdir(d) if f != "status.json"]:
        return None
    dst = os.path.join(R.RUNS, arm, "_attempts", "%s.%d" % (repo, k))
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for f in os.listdir(d):
        if f != "status.json":
            shutil.move(os.path.join(d, f), os.path.join(dst, f))
    return dst


def mark_invalid_row(arm, repo, why):
    """A harness crash leaves no scored row: write a sites_moved.json that says invalid, so the tables count it."""
    d = out_dir(arm, repo)
    p = os.path.join(d, "sites_moved.json")
    if os.path.exists(p):
        row = read_json(p) or {}
        if row.get("invalid"):
            return
    ts = R.transcript_stats(R.transcript_path(d))
    row = {"arm": arm, "repo": repo, "set": R.EVAL_SET, "invalid": why, "usd": (ts or {}).get("usd"), "cost_source": (ts or {}).get("usd_source"),
           "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(d, exist_ok=True)
    json.dump(row, open(p, "w"), indent=1, default=str)


def run_one(arm, repo, retries, dry_run=False):
    ok, why = verdict(arm, repo)
    st = read_status(arm, repo)
    if ok:
        if st.get("status") != "done" and not dry_run:
            write_status(arm, repo, dict(st, status="done", note="result.json already present and valid"))
        log("skip %s %s (done: %s)" % (arm, repo, summary(arm, repo)))
        return "done"
    if owned_elsewhere(st):
        log("skip %s %s (running under pid %s)" % (arm, repo, st.get("pid")))
        return "running"
    attempts = list(st.get("attempts") or [])
    if st.get("status") == "invalid" and len(attempts) > retries:
        log("skip %s %s (invalid after %d attempts: %s)" % (arm, repo, len(attempts), st.get("why")))
        return "invalid"
    if dry_run:
        log("would run %s %s (attempt %d; %s)" % (arm, repo, len(attempts) + 1, why))
        return "pending"
    while len(attempts) <= retries:
        k = len(attempts) + 1
        if k > 1 or os.path.exists(os.path.join(out_dir(arm, repo), "result.json")):
            archive(arm, repo, k - 1)
        st = write_status(arm, repo, {"status": "running", "attempt": k, "attempts": attempts, "pid": os.getpid(),
                                      "host": socket.gethostname(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
        log("start %s %s (attempt %d)" % (arm, repo, k))
        t0 = time.time()
        err = None
        try:
            dispatch(arm, repo)
        except BaseException as e:  # a harness crash (or the trial's own SystemExit) is an invalid attempt, never a score
            if isinstance(e, KeyboardInterrupt):
                write_status(arm, repo, dict(st, status="failed", why="interrupted"))
                raise
            err = "harness error: %s: %s" % (type(e).__name__, str(e)[:300])
            traceback.print_exc()
        ok, why = (False, err) if err else verdict(arm, repo)
        attempts.append({"attempt": k, "valid": ok, "why": why, "minutes": round((time.time() - t0) / 60, 1),
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if ok:
            write_status(arm, repo, {"status": "done", "attempts": attempts, "summary": summary(arm, repo)})
            done_line(arm, repo, "done", summary(arm, repo))
            log("done %s %s: %s" % (arm, repo, summary(arm, repo)))
            return "done"
        log("INVALID %s %s attempt %d: %s%s" % (arm, repo, k, why, "; retrying from a fresh copy" if len(attempts) <= retries else ""))
    mark_invalid_row(arm, repo, why)
    write_status(arm, repo, {"status": "invalid", "why": why, "attempts": attempts})
    done_line(arm, repo, "invalid", why or "")
    return "invalid"


def run_repo(repo, arms, retries, dry_run):
    out = {}
    for arm in arms:
        try:
            out[arm] = run_one(arm, repo, retries, dry_run)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # never let one run stop the others
            traceback.print_exc()
            out[arm] = "failed: %s" % e
            try:
                write_status(arm, repo, dict(read_status(arm, repo), status="failed", why=str(e)[:300]))
            except Exception:
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", required=True, choices=SETS)
    ap.add_argument("--arms", required=True, help="comma-separated run labels, e.g. D3min,D3free,B")
    ap.add_argument("--repos", default="all", help="comma-separated repo names, or all")
    ap.add_argument("--jobs", type=int, default=6, help="repos in parallel")
    ap.add_argument("--retries", type=int, default=2, help="re-runs of an invalid run (fresh copy each time)")
    ap.add_argument("--dry-run", action="store_true", help="say what would run; run nothing")
    ap.add_argument("--no-report", action="store_true", help="skip the report tables at the end")
    a = ap.parse_args(argv)
    load_modules(a.set)
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    known = R.repos()
    names = sorted(known, key=str.lower) if a.repos == "all" else [x.strip() for x in a.repos.split(",") if x.strip()]
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit("unknown repo(s) in set %s: %s (have: %s)" % (a.set, ", ".join(unknown), ", ".join(sorted(known))))
    for arm in arms:
        b = base(arm)
        if not (b in ABLATION_BASES or (b == "C" and "_r" in arm) or arm in R.ARMS):
            raise SystemExit("unknown arm %r" % arm)
    log("set %s: %d repo(s) x %d arm(s) -> %s (jobs %d, retries %d%s)" % (a.set, len(names), len(arms), R.RUNS, a.jobs, a.retries,
                                                                       ", dry run" if a.dry_run else ""))
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        results = dict(zip(names, ex.map(lambda n: run_repo(n, arms, a.retries, a.dry_run), names)))
    counts = {}
    for n, r in results.items():
        for arm, s in r.items():
            counts[s.split(":")[0]] = counts.get(s.split(":")[0], 0) + 1
    log("finished: %s" % ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    if not a.dry_run and not a.no_report:
        # the set's sites-moved tables, the ablation tables, then eval/RESULTS.md from every set's latest report
        for name, fn in (("sites moved", SM.report), ("ablation", B.report), ("results", SM.results_md)):
            try:
                fn()
            except Exception as e:
                log("%s report failed: %s" % (name, e))
    return 0 if not any(s.startswith("failed") for r in results.values() for s in r.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
