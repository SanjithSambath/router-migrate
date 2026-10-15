"""State files: validation of the agent's report and apply results, item status, requests, the migration key.
Contracts: router_migrate/schemas/V7_CONTRACTS.md (v6 reports still load: `normalize` maps them onto the v7 kinds)."""
import hashlib
import json
import os
import re
import time

STATE = ".router-migrate"
KINDS = ("change", "recommendation", "stays", "note")
MIGRATION_KINDS = ("change",)
V6_KINDS = {"move": "change", "trade-off": "change", "port": "change", "improvement": "recommendation",
            "needs-decision": "recommendation"}
ACTORS = ("code", "router-setting", "product-call")  # who carries out a recommendation: an apply session, the human on app.router.com, the human's call first
CHECKS = ("tests", "typecheck", "router-live", "simulator", "side-by-side", "price", "other")
RESULTS = ("pass", "fail", "info", "not-run")
LATENCY = ("background", "interactive", "user_waiting")
ACTIONS = ("apply", "dismiss", "postpone", "undo", "done")  # done: the human switched a router-setting on
LEVELS = ("quick-win", "change", "rework")  # how big an improvement is: config/one-liner, a contained change, a redesign
IMPROVE_PREFIX = "router-migrate/improve/"
MIGRATION_BRANCH = "router-migrate/migration"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def sp(root, name):
    d = os.path.join(root, STATE)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def load(root, name, default=None):
    p = os.path.join(root, STATE, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p))
    except ValueError:
        return default


def save(root, name, obj):
    p = sp(root, name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------------ the migration key
def migration_key():
    """The migration key, or None. Never print, log or store it."""
    k = os.environ.get("ROUTER_MIGRATE_KEY")
    if k and k.strip():
        return k.strip()
    p = os.environ.get("ROUTER_MIGRATE_KEY_FILE") or os.path.expanduser("~/.config/router-migrate/key")
    try:
        k = open(p).read().strip()
        return k or None
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------ validation
def _s(v):
    return isinstance(v, str) and v.strip() != ""


def normalize(doc):
    """A report in the v7 shape. v6 kinds map onto v7 (move/trade-off/port -> change; improvement -> recommendation by
    code; needs-decision -> recommendation, a product call); recommendations without a rank are ranked in report order.
    The original v6 kind is kept as `v6_kind`. Never raises; validation reports what is wrong."""
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return doc
    doc = dict(doc)
    out, n = [], 0
    for it in doc["items"]:
        if not isinstance(it, dict):
            out.append(it)
            continue
        it = dict(it)
        k = it.get("kind")
        if k in V6_KINDS:
            it["v6_kind"] = k
            it["kind"] = V6_KINDS[k]
            if it["kind"] == "recommendation":
                it.setdefault("actor", "code" if k == "improvement" else "product-call")
                if doc.get("version") == 6:
                    it.setdefault("level", "change")
        if it.get("kind") == "recommendation":
            n += 1
            it.setdefault("rank", n)
        out.append(it)
    doc["items"] = out
    return doc


def validate_report(doc):
    """Errors in a MIGRATION.json document (v7, or v6 mapped by `normalize`), each naming the field; [] when valid."""
    e = []
    if not isinstance(doc, dict):
        return ["MIGRATION.json must be a JSON object"]
    if doc.get("version") not in (6, 7):
        e.append("version: must be 7")
    doc = normalize(doc)
    for k in ("summary", "reviewed"):
        if not _s(doc.get(k)):
            e.append("%s: a non-empty string is required" % k)
    items, calls = doc.get("items"), doc.get("calls")
    if not isinstance(items, list):
        e.append("items: must be a list")
        items = []
    if not isinstance(calls, list):
        e.append("calls: must be a list")
        calls = []
    ids = [i.get("id") for i in items if isinstance(i, dict)]
    seen = set()
    for n, it in enumerate(items):
        w = "items[%d]" % n
        if not isinstance(it, dict):
            e.append("%s: must be an object" % w)
            continue
        iid = it.get("id")
        if not isinstance(iid, str) or not ID_RE.match(iid):
            e.append("%s.id: 1-40 characters of letters, digits, '_', '.', '-'" % w)
        elif iid in seen:
            e.append("%s.id: %r is used twice" % (w, iid))
        else:
            seen.add(iid)
            w = "items[%s]" % iid
        kind = it.get("kind")
        if kind not in KINDS:
            e.append("%s.kind: one of %s" % (w, ", ".join(KINDS)))
        if not _s(it.get("title")):
            e.append("%s.title: required" % w)
        elif len(it["title"]) > 80:
            e.append("%s.title: at most 80 characters" % w)
        if not _s(it.get("summary")):
            e.append("%s.summary: required (one sentence)" % w)
        if it.get("v6_kind") in ("trade-off", "port") and (not _s(it.get("loses")) or it["loses"].strip().lower() == "nothing"):
            e.append("%s.loses: say what this %s gives up" % (w, it["v6_kind"]))
        if kind == "change" and doc.get("version") == 7 and not _s(it.get("commit")):
            e.append("%s.commit: the commit on %s that carries this change" % (w, MIGRATION_BRANCH))
        if kind == "recommendation":
            b = it.get("branch")
            if b is not None and not (isinstance(b, str) and b.startswith(IMPROVE_PREFIX)):
                e.append("%s.branch: a built recommendation's branch is named %s<name> (or null: built after approval)" % (w, IMPROVE_PREFIX))
            for k in ("gains", "loses", "why"):
                if not _s(it.get(k)):
                    e.append("%s.%s: required for a recommendation" % (w, k))
            if it.get("level") not in LEVELS:
                e.append("%s.level: required for a recommendation (one of %s)" % (w, ", ".join(LEVELS)))
            if it.get("actor") not in ACTORS:
                e.append("%s.actor: who carries it out, one of %s" % (w, ", ".join(ACTORS)))
            elif it["actor"] == "router-setting":
                if not _s(it.get("where")):
                    e.append("%s.where: where on app.router.com the human switches it (e.g. Routing > Shadow models)" % w)
                if b:
                    e.append("%s.branch: a router-setting has no branch (code that goes with it is a separate recommendation)" % w)
            if not isinstance(it.get("rank"), int) or isinstance(it.get("rank"), bool) or it["rank"] < 1:
                e.append("%s.rank: a positive integer (1 = most valuable)" % w)
        elif it.get("branch"):
            e.append("%s.branch: only recommendations have a branch" % w)
        if it.get("level") is not None and it.get("level") not in LEVELS:
            e.append("%s.level: one of %s" % (w, ", ".join(LEVELS)))
        ms = it.get("measured")
        if ms is not None:
            if not isinstance(ms, list):
                e.append("%s.measured: a list of {metric, before, after, n, how}" % w)
            else:
                for m, x in enumerate(ms):
                    if not (isinstance(x, dict) and _s(x.get("metric")) and _s(x.get("how"))
                            and all(x.get(k) is None or isinstance(x.get(k), (str, int, float)) for k in ("before", "after"))
                            and (x.get("before") is not None or x.get("after") is not None)
                            and isinstance(x.get("n"), int) and not isinstance(x.get("n"), bool) and x["n"] >= 1):
                        e.append("%s.measured[%d]: {metric: string, before, after (one may be null), n: samples >= 1, how: what ran}" % (w, m))
        for k in ("gains", "loses", "price", "why"):
            if it.get(k) is not None and not isinstance(it.get(k), str):
                e.append("%s.%s: must be a string" % (w, k))
        if it.get("commit") is not None and not isinstance(it.get("commit"), str):
            e.append("%s.commit: a commit id string or null" % w)
        if it.get("calls") is not None and not (isinstance(it["calls"], list) and all(isinstance(c, str) for c in it["calls"])):
            e.append("%s.calls: a list of FILE:LINE strings" % w)
        ev = it.get("evidence")
        if ev is not None:
            if not isinstance(ev, list):
                e.append("%s.evidence: must be a list" % w)
            else:
                for m, x in enumerate(ev):
                    if not isinstance(x, dict) or x.get("check") not in CHECKS or x.get("result") not in RESULTS \
                            or not isinstance(x.get("detail", ""), str):
                        e.append("%s.evidence[%d]: {check: %s, result: %s, detail: string}" % (w, m, "|".join(CHECKS), "|".join(RESULTS)))
        for k in ("depends_on", "excludes"):
            v = it.get(k)
            if v is None:
                continue
            if not isinstance(v, list):
                e.append("%s.%s: must be a list of item ids" % (w, k))
            else:
                bad = [x for x in v if x not in ids]
                if bad:
                    e.append("%s.%s: unknown item id(s) %s" % (w, k, ", ".join(map(str, bad))))
    for n, c in enumerate(calls):
        w = "calls[%d]" % n
        if not isinstance(c, dict) or not _s(c.get("site")):
            e.append("%s.site: FILE:LINE required" % w)
            continue
        if c.get("latency") is not None and c.get("latency") not in LATENCY:
            e.append("%s.latency: one of %s, or null" % (w, ", ".join(LATENCY)))
        if c.get("now") is not None and c.get("now") not in ("router", "provider", "unknown"):
            e.append("%s.now: router, provider or unknown" % w)
        if c.get("item") is not None and c.get("item") not in ids:
            e.append("%s.item: unknown item id %r" % (w, c.get("item")))
    return e


def read_report(root):
    """(doc, errors). doc is None when the file is missing or not JSON."""
    p = os.path.join(root, STATE, "MIGRATION.json")
    if not os.path.exists(p):
        return None, ["no .router-migrate/MIGRATION.json: write it before finishing (see the report format in the instructions)"]
    try:
        doc = json.load(open(p))
    except ValueError as ex:
        return None, ["MIGRATION.json is not valid JSON: %s" % ex]
    return normalize(doc), validate_report(doc)


def validate_apply(doc, requested_ids):
    """Errors in an APPLY.json document; every requested item must have a result."""
    e = []
    if not isinstance(doc, dict):
        return ["APPLY.json must be a JSON object"]
    if not _s(doc.get("summary")):
        e.append("summary: required")
    res = doc.get("results")
    if not isinstance(res, list):
        return e + ["results: must be a list"]
    got = set()
    for n, r in enumerate(res):
        w = "results[%d]" % n
        if not isinstance(r, dict):
            e.append("%s: must be an object" % w)
            continue
        if r.get("action") not in ("apply", "undo"):
            e.append("%s.action: apply or undo" % w)
        if r.get("status") not in ("done", "declined", "failed"):
            e.append("%s.status: done, declined or failed" % w)
        if r.get("status") in ("declined", "failed") and not _s(r.get("note")):
            e.append("%s.note: say why" % w)
        if not isinstance(r.get("commits", []), list):
            e.append("%s.commits: a list of commit ids" % w)
        got.add(r.get("item"))
    missing = [i for i in requested_ids if i not in got]
    if missing:
        e.append("results: no result for requested item(s) %s" % ", ".join(missing))
    return e


# ------------------------------------------------------------------------------------------------ requests and status
def item_etag(item):
    keep = {k: item.get(k) for k in ("kind", "title", "summary", "commit", "branch", "gains", "loses")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def record_request(root, item_id, action, reason=None, until=None, by="ui"):
    """Record what the human wants for one item. Raises ValueError for an unknown item or an action that doesn't fit."""
    doc, _ = read_report(root)
    items = {i.get("id"): i for i in (doc or {}).get("items") or [] if isinstance(i, dict)}
    if item_id not in items:
        raise ValueError("unknown item %r" % item_id)
    if action not in ACTIONS:
        raise ValueError("action must be one of %s" % ", ".join(ACTIONS))
    it = items[item_id]  # read_report normalized it to v7
    st = item_status(it, load(root, "requests.json", {"requests": {}}), load(root, "apply_log.json", {"sessions": []}),
                     load(root, "validation.json", {}))
    setting = it.get("actor") == "router-setting"
    if action == "apply" and (it.get("kind") != "recommendation" or setting):
        raise ValueError("only recommendations carried out in code can be applied; %s is %s" % (
            item_id, "a setting you switch on app.router.com (mark it done instead)" if setting else "a " + str(it.get("kind"))))
    if action == "done" and not setting:
        raise ValueError("only a router-setting is marked done; %s is carried out by an apply session" % item_id)
    if action == "undo" and (setting or st not in ("in code", "applied", "undo requested")):
        raise ValueError("%s is not in the code (status: %s)" % (item_id, st))
    if action in ("dismiss", "postpone") and it.get("kind") != "recommendation":
        raise ValueError("%s can't be %sed" % (item_id, action.rstrip("e")))
    if action == "postpone" and not until:
        raise ValueError("postpone needs a date")
    rq = load(root, "requests.json", {"requests": {}})
    rq["requests"][item_id] = {"action": action, "reason": reason, "until": until, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "by": by, "etag": item_etag(it), "session": None}
    save(root, "requests.json", rq)
    return rq["requests"][item_id]


def clear_request(root, item_id):
    rq = load(root, "requests.json", {"requests": {}})
    rq["requests"].pop(item_id, None)
    save(root, "requests.json", rq)


def take_requests(root, ids, session):
    """Mark requests as taken by an apply session that is starting (the dashboard then shows them "in session")."""
    rq = load(root, "requests.json", {"requests": {}})
    for i in ids:
        if i in rq["requests"]:
            rq["requests"][i].update(session=session, state="running")
    save(root, "requests.json", rq)


def finish_requests(root, ids, session, state="done"):
    rq = load(root, "requests.json", {"requests": {}})
    for i in ids:
        r = rq["requests"].get(i)
        if r and r.get("session") == session:
            r["state"] = state
    save(root, "requests.json", rq)


def pending_requests(root):
    """Requests an apply session should act on: apply and undo not yet taken by a session."""
    rq = (load(root, "requests.json", {"requests": {}}) or {}).get("requests") or {}
    return {i: r for i, r in rq.items() if r.get("action") in ("apply", "undo") and not r.get("session")}


def _last_result(item_id, apply_log):
    last = None
    for s in (apply_log or {}).get("sessions") or []:
        for r in s.get("results") or []:
            if r.get("item") == item_id:
                last = r
    return last


def item_status(item, requests, apply_log, validation):
    iid, kind = item.get("id"), item.get("kind")
    kind = V6_KINDS.get(kind, kind)  # a raw v6 item
    req = ((requests or {}).get("requests") or {}).get(iid)
    last = _last_result(iid, apply_log)
    done = [r for s in (apply_log or {}).get("sessions") or [] for r in s.get("results") or []
            if r.get("item") == iid and r.get("status") == "done"]
    applied = bool(done) and done[-1].get("action") == "apply"
    undone = bool(done) and done[-1].get("action") == "undo"
    if item.get("actor") == "router-setting":  # never goes to an apply session: the human switches it on
        return {"done": "done", "dismiss": "dismissed", "postpone": "postponed"}.get((req or {}).get("action"), "your step")
    if req and not req.get("session"):
        return {"apply": "approved", "undo": "undo requested", "dismiss": "dismissed", "postpone": "postponed",
                "done": "done"}[req["action"]]
    if req and req.get("state") == "running":
        return "in session"
    if last and last.get("status") in ("declined", "failed"):
        return "declined"
    if undone:
        return "undone"
    if kind in MIGRATION_KINDS:
        return "in code"
    if kind == "recommendation":
        if applied:
            return "applied"
        b = ((validation or {}).get("branches") or {}).get(item.get("branch") or "")
        if b and b.get("ok") is False:
            return "check failed"
        if req and req.get("action") in ("dismiss", "postpone"):
            return {"dismiss": "dismissed", "postpone": "postponed"}[req["action"]]
        return "proposed"
    if req and req.get("action") in ("dismiss", "postpone"):
        return {"dismiss": "dismissed", "postpone": "postponed"}[req["action"]]
    return "info"


def apply_blocked(validation):
    """Why Apply is off, or None."""
    m = (validation or {}).get("migration") or {}
    if not m:
        return "Run `router-migrate finish` first: the migration hasn't been checked."
    if (m.get("tests") or {}).get("status") == "running":
        return "The full test suite is still running. Apply turns on when it passes."
    if m.get("ok") is False:
        gates = ", ".join(sorted({f.get("gate", "?") for f in m.get("failures") or []}))
        return "The migration's check failed (%s). Fix it and run `router-migrate finish` again; undo requests are still allowed." % gates
    return None
