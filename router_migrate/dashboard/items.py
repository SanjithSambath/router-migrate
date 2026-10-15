"""The dashboard: one item model, rendered uniformly (docs/plans/v7-flagship.md, "Dashboard"). v6 reports load through
report_v6.normalize (their original kind is kept as `v6_kind`).

- `bundle(root)`: everything the page shows, built only from the `.router-migrate/` state files in
  router_migrate/schemas/V7_CONTRACTS.md plus git (diffs). Never raises on partial state.
- `render_report(root)`: the frozen REPORT.html (same pages, actions off, bundle inlined, no network).
- `serve(root, open_browser=True, port=0)`: the live dashboard (127.0.0.1, session token, JSON API), built on the v5
  server's hardening (Host/Origin checks, token, body limits). Endpoints:
    GET  /?t=<token>            the page
    GET  /api/bundle            bundle(root) plus the last apply hand-off
    POST /api/request           {item, action, reason?, until?} -> report_v6.record_request (400 with its message)
    POST /api/request/clear     {item}: withdraw a request no session has taken yet
    POST /api/apply             {} -> router_migrate.harness.launch_apply(root); 409 while Apply is blocked (undo-only
                                lists are always allowed), 501 when launch_apply isn't available
"""
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

from .. import report_v6 as R
from . import server as v5server
from .render import safe_json

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v6.html")
PLACEHOLDER = "/*__RM_BOOT__*/"
MODES = ("frozen", "live")
DIFF_LINES = 400
DIFF_LINE_CHARS = 400
GIT_TIMEOUT = 10
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

CHECK_OF_GATE = {"tests": "tests", "tests-edited": "tests", "typecheck": "typecheck", "router": "simulator", "sim": "simulator"}
# the check's gates in plain words: a label for a check of its own, and what failed (for "Apply is off because ...")
GATE_LABEL = {"parse": "Syntax", "policy": "Router policy", "report": "Report file"}
GATE_FAILED = {"tests": "tests fail", "tests-edited": "an existing test file was edited", "typecheck": "there are new type errors",
               "router": "Router's rules reject some requests", "sim": "Router's rules reject some requests",
               "parse": "a file no longer parses", "policy": "the Router policy is out of date", "report": "the report file has errors"}
SIM = "a local copy of Router's rules"
WAITING = ("proposed", "check failed", "declined", "your step")


# ------------------------------------------------------------------------------------------------ small helpers
def _d(v):
    return v if isinstance(v, dict) else {}


def _l(v):
    return v if isinstance(v, list) else []


def _s(v):
    return v if isinstance(v, str) else ""


def _load(root, name, default):
    v = R.load(root, name, None)
    return v if isinstance(v, type(default)) else default


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _count(n, word):
    return "%s %s" % ("{:,}".format(n), word if n == 1 else word + "s")


def cap(t):
    return t[:1].upper() + t[1:]


def _plain(t):
    """The check's own wording, with its internal tool name spelled out."""
    return re.sub(r"\brouter-sim\b", SIM, t)


def _flag(f):
    """A flag from the check (deleted files, dependency files, ...) with its detail in plain words."""
    f = _d(f)
    kind, files = _s(f.get("kind")), [x for x in _l(f.get("files")) if isinstance(x, str)]
    det = _s(f.get("detail"))
    if kind in ("deleted-file", "dependencies", "runtime-edit") and files:
        det = ""  # the label says what happened; the files say where
    elif kind == "provider-key":
        names = det.split(": ", 1)[1] if ": " in det else det
        det = ("%s no longer referenced. Keep the provider key set where the app runs: calls Router can't serve still use it."
               % (names + (" is" if "," not in names else " are"))) if names else det
    return dict(f, detail=_plain(det), files=files)


def blocked_text(validation):
    """Why Apply is off, in plain words, or None (same conditions as report_v6.apply_blocked)."""
    if not R.apply_blocked(validation):
        return None
    m = _d(_d(validation).get("migration"))
    if not m:
        return "Apply is off until the migration has been checked. Run `router-migrate finish` first."
    if _d(m.get("tests")).get("status") == "running":
        return "The full test suite is still running. Apply turns on when it passes."
    why = []
    for f in _l(m.get("failures")):
        g = _s(_d(f).get("gate"))
        w = GATE_FAILED.get(g, g or "something failed")
        if w not in why:
            why.append(w)
    return ("Apply is off because the migration's check failed%s. Fix it and run `router-migrate finish` again. "
            "Undos are still allowed." % ((": " + "; ".join(why)) if why else ""))


# ------------------------------------------------------------------------------------------------ git
def _git(root, *args):
    """(ok, stdout or error text). Never raises."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", LC_ALL="C")
    try:
        p = subprocess.run(["git", "-C", root, "-c", "core.quotepath=off", "-c", "color.ui=false"] + list(args),
                           capture_output=True, timeout=GIT_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "%s: %s" % (type(e).__name__, e)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        return False, (err[-1] if err else "git exited %d" % p.returncode)
    return True, out


def _refs(root):
    """{branch name: sha} for router-migrate/* branches (one git call), or None outside a git repo."""
    ok, out = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads/router-migrate")
    if not ok:
        return None
    refs = {}
    for line in out.splitlines():
        name, _, sha = line.partition("\0")
        if name.startswith("refs/heads/") and sha:
            refs[name[len("refs/heads/"):]] = sha
    return refs


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _parse_files(text):
    files = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git a/(.*) b/(.*)$", line)
            if m:
                files.append(m.group(2))
    return files


def _shape(ok, text, ref, how):
    if not ok:
        return {"ref": ref, "how": how, "error": text, "text": "", "files": [], "lines": 0, "truncated": False}
    lines = text.splitlines()
    files = _parse_files(text)
    kept = [ln if len(ln) <= DIFF_LINE_CHARS else ln[:DIFF_LINE_CHARS] + " …" for ln in lines[:DIFF_LINES]]
    return {"ref": ref, "how": how, "error": None, "text": "\n".join(kept), "files": files, "lines": len(lines),
            "truncated": len(lines) > DIFF_LINES}


def _commit_diff(root, commit):
    if not COMMIT_RE.match(commit or ""):
        return _shape(False, "%r isn't a commit id" % commit, commit, "git show")
    key = (os.path.abspath(root), "c", commit)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    ok, out = _git(root, "show", "--stat", "--patch", "--no-ext-diff", "--format=medium", commit)
    d = _shape(ok, out, commit, "git show --stat --patch %s" % commit)
    if ok:
        with _CACHE_LOCK:
            _CACHE[key] = d
    return d


def _branch_diff(root, branch, refs, base):
    how = "git diff %s...%s" % (base, branch)
    if not (REF_RE.match(branch or "") and ".." not in branch and branch.startswith(R.IMPROVE_PREFIX)):
        return _shape(False, "%r isn't a recommendation branch" % branch, branch, how)
    if refs is None:
        return _shape(False, "this folder isn't a git repository", branch, how)
    if branch not in refs:
        return _shape(False, "branch %s was not found in this repository" % branch, branch, how)
    if base not in refs:
        return _shape(False, "branch %s was not found in this repository" % base, branch, how)
    key = (os.path.abspath(root), "b", refs[base], refs[branch])
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    ok, out = _git(root, "diff", "--patch-with-stat", "--no-ext-diff", "%s...%s" % (refs[base], refs[branch]))
    d = _shape(ok, out, branch, how)
    if ok:
        with _CACHE_LOCK:
            _CACHE[key] = d
    return d


# ------------------------------------------------------------------------------------------------ evidence from the check
def _tests_entry(t, where):
    t = _d(t)
    st = t.get("status")
    if not st:
        return None
    c, b = _d(t.get("counts")), _d(t.get("baseline"))

    def counts(x):
        bits = []
        for k in ("passed", "failed", "errors"):
            if _num(x.get(k)) is not None and (k == "passed" or x.get(k)):
                bits.append("%s %s" % ("{:,}".format(x[k]), k))
        return ", ".join(bits)
    detail = "Tests %s on %s" % (st, where)
    if counts(c):
        detail = "%s on %s" % (counts(c), where)
        if counts(b):
            same = (_num(c.get("passed")) or 0) >= (_num(b.get("passed")) or 0) and \
                (_num(c.get("failed")) or 0) + (_num(c.get("errors")) or 0) <= (_num(b.get("failed")) or 0) + (_num(b.get("errors")) or 0)
            detail += (", the same as before (%s)" if same else ". Before: %s") % counts(b)
    result = {"passed": "pass", "failed": "fail", "running": "info", "skipped": "not-run"}.get(st, "info")
    if st == "running":
        detail = "Tests are still running on %s" % where
    if st == "skipped":
        detail = "Tests were skipped on %s" % where
    return {"check": "tests", "result": result, "detail": detail, "source": "check"}


def _typecheck_entry(t, where):
    t = _d(t)
    e, b = _num(t.get("errors")), _num(t.get("baseline"))
    if e is None:
        return None
    ok = e <= (b or 0)
    return {"check": "typecheck", "result": "pass" if ok else "fail", "source": "check",
            "detail": "%d type %s on %s%s" % (e, "error" if e == 1 else "errors", where, "" if b is None else " (before: %d)" % b)}


def _router_entry(r, where):
    r = _d(r)
    n, v = _num(r.get("requests")), _num(r.get("valid"))
    if not n:
        return None
    bad = _l(r.get("invalid"))
    detail = "%d of %d requests from %s were accepted by %s" % (v or 0, n, where, SIM)
    if bad:
        detail += ". Rejected: " + "; ".join(str(x.get("detail") if isinstance(x, dict) else x) for x in bad[:3])
    return {"check": "simulator", "result": "pass" if not bad and v == n else "fail", "detail": detail, "source": "check"}


def _live_entry(lv):
    """The agent's live Router checks, as recorded by the pass-through (not self-reported)."""
    lv = _d(lv)
    n = lv.get("requests") or 0
    if not n:
        return None
    feats = sorted(_d(lv.get("by_feature")))
    named = [f for f in feats if not f.startswith("(")][:6]  # "(unlabelled)": requests without a feature label
    ok = lv.get("ok") or 0
    return {"check": "router-live", "label": None, "result": "pass" if ok == n else "info", "source": "check",
            "detail": "%s sent to real Router during the run; %s%s" % (
                _count(n, "request"), "all succeeded" if ok == n else "%d succeeded" % ok,
                (" (labelled %s%s)" % (", ".join(named), "; some had no label" if len(named) < len(feats) else "")) if named else "")}


def check_entries(v, where):
    """The outside check's result for one ref (the migration or a branch), as evidence entries labelled 'check'."""
    v = _d(v)
    out = [x for x in (_tests_entry(v.get("tests"), where), _typecheck_entry(v.get("typecheck"), where),
                       _router_entry(v.get("router"), where), _live_entry(v.get("live"))) if x]
    for f in _l(v.get("failures")):
        f = _d(f)
        gate, det = _s(f.get("gate")) or "check", _plain(_s(f.get("detail")))
        chk = CHECK_OF_GATE.get(gate)
        same = [x for x in out if x["check"] == chk] if chk else []
        if same:
            same[0]["result"] = "fail"
            if det and det not in same[0]["detail"]:
                same[0]["detail"] += "; " + det
        else:
            out.append({"check": chk or "other", "label": None if chk else GATE_LABEL.get(gate, gate.capitalize()), "result": "fail",
                        "detail": det or "Failed on %s: %s" % (where, GATE_FAILED.get(gate, gate)), "source": "check"})
    if v.get("ok") is False and not any(x["result"] == "fail" for x in out):
        out.append({"check": "other", "label": "Independent check", "result": "fail",
                    "detail": "router-migrate's own check failed on %s" % where, "source": "check"})
    return out


def _routes(m):
    rt = _d(_d(m).get("routes"))
    out = {}
    for k in ("verified", "unverified", "not_routing"):
        for s in _l(rt.get(k)):
            if isinstance(s, str):
                out[s] = {"route": k, "reason": None}
    for s in _l(rt.get("stays")):
        if isinstance(s, dict) and isinstance(s.get("site"), str):
            out[s["site"]] = {"route": "stays", "reason": _s(s.get("reason")) or None}
        elif isinstance(s, str):
            out[s] = {"route": "stays", "reason": None}
    return out


def _route_entry(sites, routes):
    seen = [(s, routes[s]) for s in sites if s in routes]
    if not seen:
        return None
    by = {}
    for s, r in seen:
        by.setdefault(r["route"], []).append((s, r))
    bits, result = [], "pass"
    if by.get("verified"):
        bits.append("%s confirmed going through Router" % _count(len(by["verified"]), "call"))
    if by.get("unverified"):
        bits.append("set up to use Router, but can't be confirmed without running the app: " + ", ".join(s for s, _ in by["unverified"]))
        result = "info"
    if by.get("stays"):
        bits.append("; ".join("%s stays with its provider%s" % (s, ": " + r["reason"] if r["reason"] else "") for s, r in by["stays"]))
        result = "info" if result == "pass" else result
    if by.get("not_routing"):
        bits.append("said to be moved, but still going to the provider: " + ", ".join(s for s, _ in by["not_routing"]))
        result = "fail"
    return {"check": "other", "label": "Routing", "result": result, "detail": cap("; ".join(bits)), "source": "check"}


def _setting(item):
    return item.get("kind") == "recommendation" and item.get("actor") == "router-setting"


def _model_change(item):
    if _setting(item):
        return False
    if any(_d(e).get("check") == "side-by-side" for e in _l(item.get("evidence"))):
        return True
    return item.get("kind") in ("recommendation", "change") and bool(
        re.search(r"different (model|vendor)|answers will differ|model (answers|writes)", _s(item.get("loses")), re.I))


# ------------------------------------------------------------------------------------------------ actions
def actions_for(item, status, req, last):
    """What the human can do with this item now. Requests already taken by a session can't be withdrawn.
    A router-setting never goes to an apply session: the human marks it done (or dismisses or postpones it)."""
    kind = item.get("kind")
    if _setting(item):
        if status == "done":
            return []
        if req and not req.get("session"):
            return ["withdraw"]
        return ["done", "dismiss", "postpone"] if status == "your step" else []
    if req and not req.get("session"):
        return ["withdraw"]
    if kind == "recommendation":  # carried out in code (actor code or product-call)
        if status in ("proposed", "check failed", "undone"):
            return ["approve", "dismiss", "postpone"]
        if status == "applied":
            return ["undo"]
        if status == "declined":
            return ["undo"] if _d(last).get("action") == "undo" else ["approve", "dismiss", "postpone"]
        return []
    if kind in R.MIGRATION_KINDS:
        return ["undo"] if status in ("in code", "declined") else []
    return []


def _value(x):
    return x if isinstance(x, (str, int, float)) and not isinstance(x, bool) else None


def _measured(item):
    """The agent's measured numbers (metric, before, after, n, how), cleaned for the page."""
    return [{"metric": m["metric"], "before": _value(m.get("before")), "after": _value(m.get("after")), "n": _num(m.get("n")),
             "how": _s(m.get("how"))} for m in _l(item.get("measured")) if isinstance(m, dict) and _s(m.get("metric"))]


# ------------------------------------------------------------------------------------------------ the bundle
def bundle(root):
    root = os.path.abspath(root)
    doc, errors = R.read_report(root)
    doc = doc if isinstance(doc, dict) else {}
    if not os.path.exists(os.path.join(root, R.STATE, "MIGRATION.json")):
        errors = []  # not written yet: the page says so; it isn't a format error
    requests = _load(root, "requests.json", {})
    requests = {"requests": _d(requests.get("requests"))}
    apply_log = _load(root, "apply_log.json", {})
    apply_log = {"sessions": [s for s in _l(apply_log.get("sessions")) if isinstance(s, dict)]}
    validation = _load(root, "validation.json", {})
    ledger = _load(root, "ledger.json", {})
    mig = _d(validation.get("migration"))
    branches = _d(validation.get("branches"))
    routes = _routes(mig)
    live = bool(ledger.get("live"))
    mig_ref = _s(mig.get("ref")) or R.MIGRATION_BRANCH
    if not (REF_RE.match(mig_ref) and ".." not in mig_ref):
        mig_ref = R.MIGRATION_BRANCH
    refs = _refs(root)

    raw_items = [i for i in _l(doc.get("items")) if isinstance(i, dict) and isinstance(i.get("id"), str)]
    raw_calls = [c for c in _l(doc.get("calls")) if isinstance(c, dict) and isinstance(c.get("site"), str)]
    finished = {s.get("id") for s in apply_log["sessions"]}
    running = {}
    for iid, rq in requests["requests"].items():
        if isinstance(rq, dict) and rq.get("session") and rq["session"] not in finished:
            running.setdefault(rq["session"], []).append(iid)

    # migration flags belong to the item whose commit touched the flagged files; the rest stay on the overview
    mig_flags = [_flag(f) for f in _l(mig.get("flags"))]
    claimed = set()
    items = []
    for it in raw_items:
        iid, kind = it["id"], it.get("kind")
        try:
            status = R.item_status(it, requests, apply_log, validation)
        except Exception:  # a malformed request or session must not take the page down
            status = "info"
        req = _d(requests["requests"].get(iid)) or None
        etag = R.item_etag(it)
        results = []
        for s in apply_log["sessions"]:
            for r in _l(s.get("results")):
                if isinstance(r, dict) and r.get("item") == iid:
                    results.append(dict(r, session=s.get("id"), finished=s.get("finished")))
        last = results[-1] if results else None
        calls = [c for c in _l(it.get("calls")) if isinstance(c, str)]
        checked = []
        for e in _l(it.get("evidence")):
            if isinstance(e, dict):
                checked.append({"check": e.get("check") if e.get("check") in R.CHECKS else "other", "label": None,
                                "result": e.get("result") if e.get("result") in R.RESULTS else "info",
                                "detail": _s(e.get("detail")), "source": "agent",
                                "samples": _l(e.get("samples")) or None})
        flags = []
        diff = None
        setting = _setting(it)
        measured = _measured(it)
        if kind == "recommendation" and not setting:
            b = _s(it.get("branch"))
            bv = _d(branches.get(b))
            checked += check_entries(bv, "this branch") if bv else [{"check": "other", "label": "Independent check", "result": "not-run", "source": "check",
                                                          "detail": ("router-migrate's own check hasn't run on branch %s yet" % b) if b else
                                                          "Not built yet. The agent builds it after you approve it and press Apply, and the check runs then."}]
            flags = [_flag(f) for f in _l(bv.get("flags"))]
            diff = _branch_diff(root, b, refs, mig_ref) if b else None
        elif kind in R.MIGRATION_KINDS:
            if mig:
                checked += check_entries(mig, "the migration branch")
            if it.get("commit"):
                diff = _commit_diff(root, _s(it.get("commit")))
                touched = set(diff.get("files") or [])
                for n, f in enumerate(mig_flags):
                    if touched & set(f["files"]):
                        flags.append(f)
                        claimed.add(n)
        elif it.get("commit"):
            diff = _commit_diff(root, _s(it.get("commit")))
        # where calls go is the migration's to say: it covers a change's (or a stay's, or a product call's) calls
        re_ = _route_entry(calls, routes) if kind in R.MIGRATION_KINDS + ("stays",) or it.get("actor") == "product-call" else None
        if re_:
            checked.append(re_)
        model_change = _model_change(it)
        if (kind in R.MIGRATION_KINDS or (kind == "recommendation" and not setting)) and not measured \
                and not any(c["check"] == "router-live" for c in checked):
            checked.append({"check": "router-live", "result": "not-run", "source": "check", "label": None,
                            "detail": "The agent didn't run this on real Router" if live else
                            "Not run on real Router: there was no Router key, so it was only tested against %s" % SIM})
        if model_change and not any(c["check"] == "side-by-side" for c in checked):
            checked.append({"check": "side-by-side", "result": "not-run", "source": "check", "label": None,
                            "detail": "Answers will change, and no before and after answers were compared" +
                            ("" if live else " (there was no Router key to run them)")})
        due = status == "postponed" and bool(req) and _s(req.get("until")) and req["until"] <= time.strftime("%Y-%m-%d")
        # a request a session has taken but not finished: item_status can't see it yet, so the card says it's in progress
        in_session = req.get("session") if req and req.get("session") and req["session"] not in finished else None
        items.append({
            "id": iid, "kind": kind, "title": _s(it.get("title")), "summary": _s(it.get("summary")), "calls": calls,
            "v6_kind": _s(it.get("v6_kind")) or None, "rank": _num(it.get("rank")) if kind == "recommendation" else None,
            "level": _s(it.get("level")) or None, "actor": _s(it.get("actor")) or None if kind == "recommendation" else None,
            "where": _s(it.get("where")) or None, "measured": measured,
            "built": bool(_s(it.get("branch"))) if kind == "recommendation" and not setting else None,
            "gains": it.get("gains"), "loses": it.get("loses"), "price": it.get("price"), "why": it.get("why"),
            "commit": it.get("commit"), "branch": it.get("branch"),
            "depends_on": [x for x in _l(it.get("depends_on")) if isinstance(x, str)],
            "excludes": [x for x in _l(it.get("excludes")) if isinstance(x, str)],
            "status": status, "etag": etag, "request": req,
            "stale": bool(req and req.get("etag") and req.get("etag") != etag and not req.get("session")),
            "due": bool(due), "checked": checked, "flags": flags, "diff": diff, "results": results,
            "model_change": model_change, "in_session": in_session,
            "actions": [] if in_session else actions_for(it, status, req, last),
            "waiting": not in_session and (status in WAITING or bool(due)),
        })
    by_id = {i["id"]: i for i in items}
    unclaimed = [f for n, f in enumerate(mig_flags) if n not in claimed]

    calls = []
    for c in raw_calls:
        rt = routes.get(c["site"]) or {}
        it = by_id.get(c.get("item")) or {}
        now = c.get("now") if c.get("now") in ("router", "provider", "unknown") else "unknown"
        route = rt.get("route") or {"router": "unchecked", "provider": "provider", "unknown": "unknown"}[now]
        calls.append({"site": c["site"], "feature": c.get("feature"), "model": c.get("model"), "task": c.get("task"),
                      "latency": c.get("latency"), "now": now, "route": route, "reason": rt.get("reason"),
                      "item": it.get("id"), "item_title": it.get("title"), "item_kind": it.get("kind"), "item_status": it.get("status")})
    n_ver = sum(1 for c in calls if c["route"] == "verified")
    n_unv = sum(1 for c in calls if c["route"] in ("unverified", "unchecked"))
    n_nr = sum(1 for c in calls if c["route"] == "not_routing")
    n_stay = len(calls) - n_ver - n_unv - n_nr

    pend = {i: r for i, r in requests["requests"].items()
            if isinstance(r, dict) and r.get("action") in ("apply", "undo") and not r.get("session")}
    pending = [{"item": i, "action": r.get("action"), "reason": r.get("reason"), "until": r.get("until"), "at": r.get("at"),
                "by": r.get("by"), "stale": bool((by_id.get(i) or {}).get("stale")), "title": (by_id.get(i) or {}).get("title"),
                "kind": (by_id.get(i) or {}).get("kind"), "known": i in by_id} for i, r in pend.items()]
    blocked = blocked_text(validation)
    waiting = [i["id"] for i in items if i["waiting"] and not (i["request"] and not i["request"].get("session") and not i["due"])]

    tests = _d(mig.get("tests"))
    behaviour = {
        "checked": bool(mig), "ok": mig.get("ok") if mig else None, "at": validation.get("at"), "ref": mig_ref,
        "tests": tests or None, "typecheck": _d(mig.get("typecheck")) or None, "router": _d(mig.get("router")) or None,
        "failures": [_d(f) for f in _l(mig.get("failures"))], "flags": mig_flags, "unclaimed_flags": unclaimed,
        "entries": check_entries(mig, "the migration branch") if mig else [],
    }
    history = []
    for s in apply_log["sessions"]:
        history.append({"id": s.get("id"), "started": s.get("started"), "finished": s.get("finished"),
                        "requests": _l(s.get("requests")), "summary": s.get("summary"),
                        "results": [dict(r, title=(by_id.get(r.get("item")) or {}).get("title"), kind=(by_id.get(r.get("item")) or {}).get("kind"))
                                    for r in _l(s.get("results")) if isinstance(r, dict)],
                        "check": _d(s.get("check")) or None,
                        "check_entries": check_entries(s.get("check"), "the branch after this session") if _d(s.get("check")) else []})
    recs = sorted((i for i in items if i["kind"] == "recommendation"), key=lambda i: (i["rank"] is None, i["rank"] or 0))
    return {
        "version": 7,
        "repo": os.path.basename(root),
        "root": root,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool_version": "router-migrate %s" % __import__("router_migrate").__version__,
        "live": live,
        "base": ledger.get("base"),
        "migration_ref": mig_ref,
        "has_report": bool(raw_items or raw_calls or doc.get("summary")),
        "report_errors": errors,
        "summary": doc.get("summary") if isinstance(doc.get("summary"), str) else None,
        "reviewed": doc.get("reviewed") if isinstance(doc.get("reviewed"), str) else None,
        "headline": {
            "traffic": {"total": len(calls), "on_router": n_ver + n_unv, "verified": n_ver, "unverified": n_unv,
                        "not_routing": n_nr, "stays": n_stay},
            "behaviour": behaviour,
            "waiting": {"count": len(waiting), "items": waiting, "ready": len(pending), "running": sorted(running)},
        },
        "calls": calls,
        "items": items,
        "history": history,
        "running": [{"session": k, "items": v} for k, v in sorted(running.items())],
        "apply_blocked": blocked,
        "pending": pending,
        "cost": _d(ledger.get("cost")),
        "recommendations": [i["id"] for i in recs],  # every recommendation, in rank order
        "setup": {"settings": [i["id"] for i in recs if i["actor"] == "router-setting"]},  # this app's Router settings
    }


# ------------------------------------------------------------------------------------------------ the page
def page_template():
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def render_page(b, mode="frozen"):
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    page = page_template()
    if page.count(PLACEHOLDER) != 1:
        raise RuntimeError("v6.html must contain the boot placeholder exactly once")
    boot = "window.__RM__ = %s; window.__RM_MODE__ = %s;" % (safe_json(b), safe_json(mode))
    return page.replace(PLACEHOLDER, boot, 1)


def render_report(root):
    """The frozen REPORT.html: the same pages with the bundle inlined, actions off, a 'Frozen report' marker."""
    return render_page(bundle(root), "frozen")


def write_report(root, path=None):
    path = path or os.path.join(root, R.STATE, "REPORT.html")
    html = render_report(root)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------------------------------------ the server
HttpError = v5server.HttpError


def _validate_request(p):
    if not isinstance(p, dict):
        raise HttpError(400, "body must be a JSON object")
    extra = set(p) - {"item", "action", "reason", "until"}
    if extra:
        raise HttpError(400, "unknown field(s): %s" % ", ".join(sorted(extra)))
    item, action = p.get("item"), p.get("action")
    if not isinstance(item, str) or not R.ID_RE.match(item):
        raise HttpError(400, "item must be an item id")
    if action not in R.ACTIONS:
        raise HttpError(400, "action must be one of %s" % ", ".join(R.ACTIONS))
    reason, until = p.get("reason"), p.get("until")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
        raise HttpError(400, "reason must be a string (at most 2000 characters)")
    if until is not None and (not isinstance(until, str) or not DATE_RE.match(until)):
        raise HttpError(400, "until must be YYYY-MM-DD")
    return item, action, (reason or "").strip() or None, until or None


def _tail(path, n=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except (OSError, TypeError, ValueError):
        return None


class _App(object):
    def __init__(self, root, token):
        self.root, self.token = root, token
        self.allowed_hosts, self.allowed_origins = set(), set()
        self.launch = None
        self.lock = threading.Lock()

    def bind_port(self, port):
        self.allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        self.allowed_origins = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}

    def get_bundle(self):
        b = bundle(self.root)
        if self.launch:
            ln = dict(self.launch)
            if ln.get("log"):
                ln["tail"] = _tail(ln["log"])
            b["launch"] = ln
        return b

    def apply(self):
        with self.lock:
            validation = _load(self.root, "validation.json", {})
            pend = R.pending_requests(self.root)
            if not pend:
                raise HttpError(409, "Nothing to apply yet. Approve a recommendation or mark a change for undo first.")
            sessions = {s.get("id") for s in _l(_load(self.root, "apply_log.json", {}).get("sessions")) if isinstance(s, dict)}
            taken = sorted({r.get("session") for r in _d(_load(self.root, "requests.json", {}).get("requests")).values()
                            if isinstance(r, dict) and r.get("session") and r.get("session") not in sessions})
            if taken:
                raise HttpError(409, "The agent is still applying your last batch (session %s). Wait for it to finish." % ", ".join(taken))
            blocked = blocked_text(validation)
            if blocked and not all(r.get("action") == "undo" for r in pend.values()):
                raise HttpError(409, blocked)
            try:
                from .. import harness
                launch = getattr(harness, "launch_apply", None)
            except ImportError:
                launch = None
            if not callable(launch):
                raise HttpError(501, "This version can't start Apply from the page. Run `/router-migrate apply` in Claude Code "
                                     "(or `router-migrate apply` in a terminal) to carry out your decisions.")
            result = launch(self.root)
            self.launch = dict(result if isinstance(result, dict) else {"result": result},
                               requests=sorted(pend), at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            return result


def _make_handler(app):
    Base = v5server._make_handler(app)  # Host/Origin checks, token, JSON body reading, error responses

    class Handler(Base):
        def _send(self, status, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in v5server.SECURITY_HEADERS.items():
                self.send_header(k, v)
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            try:
                self._check_host_origin()
                u = urlsplit(self.path)
                if u.path == "/":
                    self._check_token((parse_qs(u.query).get("t") or [""])[0])
                    self._send(200, render_page(app.get_bundle(), "live"), "text/html; charset=utf-8")
                elif u.path.startswith("/api/"):
                    self._check_token(self.headers.get("X-RM-Token"))
                    if u.path != "/api/bundle":
                        raise HttpError(405 if u.path in ("/api/request", "/api/request/clear", "/api/apply") else 404, "not found")
                    self._json(200, app.get_bundle())
                else:
                    raise HttpError(404, "not found")
            except HttpError as e:
                self._error(e)
            except Exception as e:
                self._error(HttpError(500, "%s: %s" % (type(e).__name__, e)))

        def do_POST(self):
            try:
                self._check_host_origin()
                u = urlsplit(self.path)
                if not u.path.startswith("/api/"):
                    raise HttpError(405 if u.path == "/" else 404, "not found")
                self._check_token(self.headers.get("X-RM-Token"))
                if u.path not in ("/api/request", "/api/request/clear", "/api/apply"):
                    raise HttpError(405 if u.path == "/api/bundle" else 404, "not found")
                payload = self._read_json()
                if u.path == "/api/request":
                    item, action, reason, until = _validate_request(payload)
                    try:
                        rec = R.record_request(app.root, item, action, reason=reason, until=until, by="ui")
                    except ValueError as e:
                        raise HttpError(400, str(e))
                    self._json(200, {"ok": True, "item": item, "request": rec})
                elif u.path == "/api/request/clear":
                    if not isinstance(payload, dict) or set(payload) != {"item"} or not isinstance(payload["item"], str):
                        raise HttpError(400, "body must be {\"item\": \"<id>\"}")
                    rq = _d(_d(_load(app.root, "requests.json", {})).get("requests")).get(payload["item"])
                    if not rq:
                        raise HttpError(400, "There's no decision to cancel for %r" % payload["item"])
                    if _d(rq).get("session"):
                        raise HttpError(409, "The agent is already working on this (session %s), so it can't be cancelled" % rq["session"])
                    R.clear_request(app.root, payload["item"])
                    self._json(200, {"ok": True, "item": payload["item"]})
                else:
                    if not isinstance(payload, dict) or payload:
                        raise HttpError(400, "apply takes an empty JSON object")
                    self._json(200, {"ok": True, "result": app.apply()})
            except HttpError as e:
                self._error(e)
            except Exception as e:
                self._error(HttpError(500, "%s: %s" % (type(e).__name__, e)))

    return Handler


def serve(root, open_browser=True, port=0):
    """Start the live v6 dashboard on 127.0.0.1. Returns (server, url); the url carries the session token.
    Stop with server.shutdown()."""
    import secrets
    root = os.path.abspath(root)
    app = _App(root, secrets.token_urlsafe(32))
    httpd = v5server.DashboardServer(("127.0.0.1", int(port)), _make_handler(app))
    real = httpd.server_address[1]
    app.bind_port(real)
    url = "http://127.0.0.1:%d/?t=%s" % (real, app.token)
    httpd.token, httpd.url, httpd.port, httpd.app = app.token, url, real, app
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, name="router-migrate-dashboard-v6", daemon=True)
    t.start()
    httpd.thread = t
    _orig = httpd.shutdown

    def shutdown():
        _orig()
        httpd.server_close()

    httpd.shutdown = shutdown
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return httpd, url


if __name__ == "__main__":  # python -m router_migrate.dashboard.items <repo>: dump the bundle
    import sys
    print(json.dumps(bundle(sys.argv[1] if len(sys.argv) > 1 else "."), indent=1, default=str)[:20000])
