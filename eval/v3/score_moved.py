#!/usr/bin/env python3
"""Scorer v5: every ground-truth site in exactly one bucket; moved = real Router traffic (the migrate-by-default stance).

Every movable site (movable(): mechanical, from the inventory fields) gets one bucket per run: moved_verified,
moved_unverified, surfaced_for_decision, correctly_kept, claimed_not_routing or missed; a verified move of a site that is not
movable is `wrong`. one_approval_away = moved_verified + surfaced_for_decision. The headline `moved` (sites_moved_movable)
stays verified Router traffic: the arm claims the site AND
  (a) dynamic: the harness re-ran the agent's recorded probes on the final code against router-sim right after the trial and
      saw a valid request from the site or from the client it uses (reverify.json; ablation_b2.reverify); or
  (b) static: the product's route check (router_migrate.route_check.check_sites, what the agent sees as `check --all`) sends
      a representative request built from the call's static kwargs through the real vendored runtime with the trial tree's
      compiled policy and wrap options, and router-sim accepts it (a Router rejection falls back to the provider in
      production, so it is not Router traffic).
Claims come from the arm's own report (raw_decisions: floor plan, ledger claims, MIGRATION_REPORT.json or MIGRATION.json), a
decision naming the site or the client construction its call uses; with no such decision the code is the report (a call it
sends through the runtime is claimed). Report lines are resolved against the pristine source both ways (current lines mapped
back through the trial's diff hunks, or pristine lines: resolve_line). Destructive edits (removed provider-key references,
deleted files, deleted classes/functions) come from each run's diff.patch.

Ground truth (no LLM adjudication): movable = prod AND an LLM generation call AND not a hard limit: Router has no endpoint
(embeddings, images, audio/tts/transcription, files, moderation, legacy completions, model lists), provider-side state actually
used, a local/self-hosted model, or the app's own proxy/gateway.

  .venv/bin/python eval/v3/score_moved.py classify                 # per-repo movable table + every not-movable site
  .venv/bin/python eval/v3/score_moved.py score [--arms A,C] [--only repo,..] [--jobs 4]
                                                                    # runs/<arm>/<repo>/sites_moved.json, then report
  .venv/bin/python eval/v3/score_moved.py report                   # eval/v3/SITES_MOVED.md + sites_moved.json
  .venv/bin/python eval/v3/score_moved.py results                  # eval/RESULTS.md from both sets' report JSON
RM_EVAL_SET=heldout scores eval/heldout/runs_agents (tables in eval/heldout/SITES_MOVED_HELDOUT.md); RM_EVAL_SET=dev scores
eval/v3/runs_dev (no inventory: moved = valid Router traffic only); RM_EVAL_SET=heldout2 scores eval/heldout2/runs.

Invalid runs are written (sites_moved.json with `invalid`) but never counted: an infrastructure failure (the transcript ends
in an API error, or the agent produced no output) or an agent arm with a decision channel that decided nothing. A run that
never ran, was interrupted before scoring, or has no trial tree is `not_run` (run_status). $ comes from the agent transcript
(result total_cost_usd, or summed per-message usage at list prices when it timed out); minutes from agent.json for every arm.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_v3 as R  # noqa: E402

HA = R.HA
OUT_NAME = "sites_moved.json"

# ------------------------------------------------------------------------------------------------ ground truth
NO_ENDPOINT_SURFACES = {"embeddings", "images", "audio", "files", "moderations", "moderation", "completions", "models", "batches",
                        "fine_tuning", "uploads", "vector_stores", "speech", "transcriptions"}
_NO_ENDPOINT_WHY = re.compile(r"router has no \w+(?:/\w+)? endpoint|legacy /v1/completions|legacy completions", re.I)
_STATE_WHY = re.compile(r"previous_response_id|store=true|\bconversation=|vector[_ ]store|file_search|provider-side (?:state|files)|"
                        r"assistants api|threads api", re.I)
_LOCAL_ENDPOINT = re.compile(r"ollama|localhost|127\.0\.0\.1|0\.0\.0\.0|lm ?studio|on-device|\bvllm\b|\blocal\b", re.I)
_LOCAL_WHY = re.compile(r"self-hosted|on-device|gemini nano|local (?:\w+ ){0,2}server", re.I)  # phrases, not provider lists
_PROXY_ENDPOINT = re.compile(r"app's own|same-origin|-backend\b|proxy", re.I)
_PROXY_WHY = re.compile(r"app's own|own (?:fastapi |backend )?(?:/v1 )?proxy|vendor's own proxy", re.I)
_NEGATED = re.compile(r"\b(?:no|not|without|never)\b[^.;:]{0,30}$", re.I)


def _hit(rx, text):
    """First match of rx in text that is not negated just before it ('no store/previous_response_id')."""
    for m in rx.finditer(text or ""):
        if not _NEGATED.search(text[max(0, m.start() - 40):m.start()]):
            return m.group(0)
    return None


OPERATOR_KEY_SOURCES = ("config", "env", "literal")


def has_operator_key(sites):
    """Does this app hold a key of its own anywhere (env, config, literal)? If not, every key is an end user's."""
    return any(x.get("prod") and x.get("key_source") in OPERATOR_KEY_SOURCES for x in sites)


def movable(site, operator_key=True):
    """(movable, reason, hint) from inventory fields only. reason says why a site is NOT movable ('' when movable); hint names
    the trade-off a move would take (informational: model swap, other vendor, per-user key, ...)."""
    surface = (site.get("surface") or "").split(".")[0]
    endpoint = str(site.get("endpoint") or "")
    why = str(site.get("why") or "")
    sdk = str(site.get("sdk") or "")
    if not site.get("prod"):
        return False, "non-production", ""
    if surface in NO_ENDPOINT_SURFACES:
        return False, "no Router endpoint: %s" % surface, ""
    if _hit(_NO_ENDPOINT_WHY, why):
        return False, "no Router endpoint: %s" % _hit(_NO_ENDPOINT_WHY, why), ""
    if site.get("stateful_responses") or _hit(_STATE_WHY, why):
        return False, "provider-side state used: %s" % (_hit(_STATE_WHY, why) or "stateful Responses"), ""
    proxy = _hit(_PROXY_ENDPOINT, endpoint) or _hit(_PROXY_WHY, why)
    if proxy:
        return False, "the app's own proxy/gateway: %s" % proxy, ""
    local = _hit(_LOCAL_ENDPOINT, endpoint) or _hit(_LOCAL_WHY, why)
    if local:
        return False, "local/self-hosted model: %s" % local, ""
    if site.get("key_source") == "per-user" and not operator_key:
        return False, "end user's own key and the app has no operator key (routing only the operator's key moves nothing)", ""
    hints = []
    if endpoint.startswith("other:"):
        hints.append("other vendor (%s): unlock host / model swap" % endpoint[6:40])
    elif endpoint in ("azure", "bedrock"):
        hints.append("%s-hosted: model map" % endpoint)
    elif endpoint == "runtime-user-defined":
        hints.append("endpoint chosen at runtime")
    if site.get("key_source") == "per-user" or re.search(r"per-session|byok|each end user|user's (?:own )?key", why, re.I):
        hints.append("per-user key")
    if site.get("beta"):
        hints.append("beta flags")
    if sdk.startswith("other:") or sdk == "raw-http":
        hints.append("%s call" % sdk)
    return True, "", "; ".join(hints)


def inventory_all(name):
    """All inventory sites (prod and not), adjudicated when present. None for repos without an inventory (dev set)."""
    if R.INVENTORY_DIR is None:
        return None
    for sub in ("inventory_adjudicated", "inventory"):
        p = os.path.join(R.INVENTORY_DIR, sub, name + ".json")
        if os.path.exists(p):
            return json.load(open(p))["sites"]
    return None


def cmd_classify(a=None):
    rows, nots = [], []
    for f in sorted(glob.glob(os.path.join(R.INVENTORY_DIR or HERE, "inventory", "*.json")), key=lambda p: os.path.basename(p).lower()):
        name = os.path.basename(f)[:-5]
        inv = inventory_all(name)
        prod = [s for s in inv if s.get("prod")]
        mv = [(s, movable(s, has_operator_key(prod))) for s in prod]
        old = sum(1 for s in prod if s.get("expected") == "migrated")
        rows.append((name, len(prod), sum(1 for _, m in mv if m[0]), old))
        nots += [(name, s, m[1]) for s, m in mv if not m[0]]
    print("| repo | prod sites | movable (new GT) | old GT should-move |\n|---|---|---|---|")
    for r in rows:
        print("| %s | %d | %d | %d |" % r)
    print("| **total** | %d | %d | %d |" % tuple(sum(r[i] for r in rows) for i in (1, 2, 3)))
    print("\nNot movable:")
    for name, s, why in nots:
        print("  %-30s %s:%s [%s, %s] %s" % (name, s["file"], s["line"], s.get("surface"), s.get("endpoint"), why))
    return rows, nots


# ------------------------------------------------------------------------------------------------ route check
# The route check itself is the product's (router_migrate/route_check.py: the real runtime's plan_request + router-sim);
# these names are re-exported for older callers.
from router_migrate.route_check import (LC_KW, _LOCK, _clients, _framework_kwargs, endpoint_host, load_policy,  # noqa: E402,F401
                                        route_check, wrap_opts)


def policy_from_ledger(led):
    """Fallback when the tree is gone: the policy half of the ledger's live trade-offs (as agent.compile_tradeoffs writes it)."""
    from router_migrate import agent as AG
    globs = {}
    for t in (led or {}).get("tradeoffs") or []:
        if t.get("undone") or not t.get("feature"):
            continue
        lv, e = t.get("levers") or {}, globs.setdefault(AG.glob_for(t["feature"]), {})
        if lv.get("drop_params"):
            e["drop_params"] = sorted(set(e.get("drop_params") or []) | set(lv["drop_params"]))
        if lv.get("model_map"):
            e["model_map"] = dict(e.get("model_map") or {}, **lv["model_map"])
    globs = {g: e for g, e in globs.items() if e}
    return {"version": 1, "globs": globs} if globs else None


PRODUCTION_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "production_config.json")


def production_config(name):
    """{file: {host, model}} for config-driven clients in this repo's shipped setup (eval/v3/production_config.json)."""
    try:
        return json.load(open(PRODUCTION_CONFIG)).get(name) or {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------------------------------------ per trial
def _base(label):
    return label.split("_r")[0]


def _tradeoffs(led):
    return [t for t in (led or {}).get("tradeoffs") or [] if not t.get("undone")]


def tree_matches_patch(dst, out_dir):
    """Is the trial tree still the arm's final tree? (its diff against the base commit == the saved diff.patch)"""
    saved = os.path.join(out_dir, "diff.patch")
    if not os.path.exists(saved) or not os.path.isdir(os.path.join(dst, ".git")):
        return None
    # the same command score_trial writes diff.patch with
    cmd = ("git add -A >/dev/null 2>&1; git diff --no-color --cached $(git rev-list --max-parents=0 HEAD) -- . ':(exclude).router-migrate' "
           "':(exclude,glob)**/router_runtime.py' ':(exclude,glob)**/routerRuntime.ts' ':(exclude,glob)**/router_policy.py'")
    cur = subprocess.run(cmd, cwd=dst, shell=True, capture_output=True, text=True).stdout
    return cur == open(saved).read()


def reconstruct(name, out_dir):
    """Trial tree gone: pristine source + the saved diff.patch (runtime/policy files are not in the patch: the runtime is
    re-rendered beside any wrapped file's import by the route check itself, the policy comes from the ledger)."""
    pristine = os.path.join(R.RE.WORK, "pristine", name)
    patch = os.path.join(out_dir, "diff.patch")
    if not os.path.isdir(pristine) or not os.path.exists(patch):
        return None
    dst = tempfile.mkdtemp(prefix="rm-recon-%s-" % name)
    shutil.rmtree(dst)
    R.sh("git clone -q %s %s && git -C %s checkout -q %s" % (pristine, dst, dst, R.repos()[name]["commit"]))
    code, o = R.sh("git apply --whitespace=nowarn %s" % patch, cwd=dst)
    if code != 0:
        return None
    R.PATCH_FOR[dst] = patch  # lines map back to pristine through the saved diff (the clone's root commit is not the base)
    return dst


# ------------------------------------------------------------------------------------------------ decisions (the arm's report)
# Every arm's per-site decisions, from its own channel, in one shape: {file, line, line_end, outcome, bucket, why, pristine}.
#   A            the floor plan (plan.json: pristine lines)                 B / B_prime   none: the code is the report
#   C, C_r<k>    the floor plan overridden by ledger claims                 B2_r<k>       A's floor plan + MIGRATION_REPORT.json
#   Cnf, D       ledger claims (no floor plan to start from)                D3min/D3free  MIGRATION.json (ledger claims without one)
# A plan's own `flagged` / `handoff` is the floor's report to a human, so it counts as surfaced for the floor arms; a no-floor
# arm's planner verdicts are the tool's, not the agent's, and are never read as its decisions.
BUCKET_OF = {"migrated": "migrated", "kept": "kept", "cannot_route": "kept", "not_a_site": "kept",
             "needs_decision": "surfaced", "flagged": "surfaced", "needs_human": "surfaced", "handoff": "surfaced"}
_LOC = re.compile(r"^\s*`?(?P<f>[^`:\s][^`:]*?):(?P<a>\d+)(?:\s*[-–]\s*(?P<b>\d+))?")


def parse_loc(loc):
    """'FILE:LINE', '`FILE:LINE`' or 'FILE:A-B (comment)' -> (file, a, b); None when it names no line."""
    m = _LOC.match(str(loc or ""))
    if not m:
        return None
    a = int(m.group("a"))
    return m.group("f").strip(), a, max(a, int(m.group("b") or a))


def channel(arm):
    b = _base(arm)
    if arm in ("B", "B_prime"):
        return None
    return {"A": "plan", "C": "plan+claims", "B2": "plan+report", "Cnf": "claims", "D": "claims", "D3min": "migration",
            "D3free": "migration"}.get(b, "claims")


def _ledger(dst, out_dir):
    for p in (os.path.join(dst or "", ".router-migrate", "ledger.json"), os.path.join(out_dir or "", "ledger.json")):
        if os.path.exists(p):
            try:
                return json.load(open(p))
            except ValueError:
                return None
    return None


def _dec(f, a, b, outcome, why, pristine, source, kind=None):
    return {"file": f, "line": int(a), "line_end": int(b if b is not None else a), "outcome": outcome, "bucket": BUCKET_OF[outcome],
            "why": str(why or ""), "pristine": pristine, "source": source, "kind": kind}


def raw_decisions(arm, dst, name, out_dir):
    """The arm's decisions with the lines as the arm wrote them (pristine=True: already pristine lines; None: the arm's own
    convention, resolved by resolve_decisions)."""
    ch = channel(arm)
    out = []
    if ch is None:
        return out
    if ch.startswith("plan"):
        plan_p = os.path.join(R.TRIALS, "A", name, ".router-migrate", "plan.json") if ch == "plan+report" else \
            os.path.join(dst or "", ".router-migrate", "plan.json")
        if os.path.exists(plan_p):
            for s in json.load(open(plan_p)).get("sites") or []:
                if s.get("prod") and s.get("outcome") in BUCKET_OF:
                    out.append(_dec(s["file"], s["line"], s["line"], s["outcome"], s.get("reason"), True, "plan", s.get("kind")))
    if ch in ("plan+claims", "claims") or ch == "migration":
        entries = None
        if ch == "migration":
            for p in (os.path.join(dst or "", ".router-migrate", "MIGRATION.json"), os.path.join(out_dir or "", "MIGRATION.json")):
                if os.path.exists(p):
                    try:
                        d = json.load(open(p))
                    except ValueError:
                        break
                    sites = d.get("sites") if isinstance(d, dict) else d
                    entries = []
                    for s in sites if isinstance(sites, list) else []:
                        loc = parse_loc((s or {}).get("site")) if isinstance(s, dict) else None
                        if loc and s.get("outcome") in BUCKET_OF:
                            entries.append(_dec(loc[0], loc[1], loc[2], s["outcome"], s.get("why"), None, "MIGRATION.json"))
                    break
        if entries is None:
            entries = []
            for c in ((_ledger(dst, out_dir) or {}).get("claims") or {}).values():
                if isinstance(c, dict) and c.get("file") and str(c.get("line", "")).isdigit() and c.get("outcome") in BUCKET_OF:
                    oc = c["reason_code"] if c.get("reason_code") in BUCKET_OF else c["outcome"]
                    entries.append(_dec(c["file"], c["line"], c["line"], oc, c.get("why"), None, "ledger claim"))
        out += entries
    if ch == "plan+report":
        import ablation_b2 as B
        sites, _ = B._report_entries(dst) if dst and os.path.isdir(dst) else (None, None)
        for s in sites or []:
            if isinstance(s, dict) and s.get("file") and isinstance(s.get("line"), int) and s.get("outcome") in BUCKET_OF:
                out.append(_dec(s["file"], s["line"], s["line"], s["outcome"], s.get("why"), True if s.get("_pristine") else None,
                                "MIGRATION_REPORT.json"))
    return out


def resolve_line(pm, f, ln, anchors):
    """A line an arm wrote -> the pristine line. Agents name lines both ways (D3 on claude-engineer: main.py:671 after its
    import block, main.py:1227 before it), so both readings are tried: the line as it is now (mapped back through the trial's
    own diff) and the line as it was; the one closer to a known site of that file (scanner call or client, inventory site)
    wins, the current reading on a tie (the documented convention). Returns (pristine line, reading)."""
    cur, pri = pm(f, ln), int(ln)
    if cur == pri:
        return cur, "same"
    dist = lambda x: min((abs(x - a) for a in anchors), default=10 ** 9)
    return (cur, "current") if dist(cur) <= dist(pri) else (pri, "pristine")


def resolve_decisions(decs, pm, anchors):
    """Pristine lines for every decision (anchors: {file: [pristine lines of known sites]}), then a later channel's decision
    overrides an earlier channel's at the same place (a claim overrides the floor plan's verdict on that site); entries of
    one report never override each other (neighbouring branches are separate decisions)."""
    out = []
    for d in decs:
        d = dict(d)
        if d["pristine"] is not True:
            a, how = resolve_line(pm, d["file"], d["line"], anchors.get(d["file"], ()))
            b = a + (d["line_end"] - d["line"])
            d.update(line_raw=d["line"], line=a, line_end=b, reading=how)
        out = [x for x in out if x["source"] == d["source"] or not (x["file"] == d["file"] and x["line"] - 3 <= d["line"] <= x["line_end"] + 3)]
        out.append(d)
    return out


# ------------------------------------------------------------------------------------------------ scopes (pristine tree)
_TS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")


class Scopes:
    """(class, function) enclosing a pristine line: Python via ast, TS/JS via brace depth (classes) and recommend's
    enclosing_function. '<module>' at top level, None for no class."""

    def __init__(self, root):
        self.root, self.cache = root, {}

    def __call__(self, f, line):
        if f not in self.cache:
            self.cache[f] = self._load(f)
        cls_spans, fn = self.cache[f]
        line = int(line)
        cls = None
        for a, b, n in cls_spans:
            if a <= line <= b:
                cls = n
        return cls, (fn(line) if fn else "<module>")

    def _load(self, f):
        import ast
        path = os.path.join(self.root or "", f)
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return [], None
        from router_migrate.recommend import enclosing_function
        fn = lambda n: enclosing_function(self.root, {"file": f, "line": n})
        spans = []
        if f.endswith(".py"):
            try:
                tree = ast.parse(src)
            except SyntaxError:
                return [], fn
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    spans.append((node.lineno, getattr(node, "end_lineno", node.lineno), node.name))
            spans.sort()
            return spans, fn
        depth, stack = 0, []
        for i, l in enumerate(src.splitlines(), 1):
            m = _TS_CLASS.match(l)
            if m:
                stack.append([m.group(1), i, depth])
            depth += l.count("{") - l.count("}")
            while stack and depth <= stack[-1][2] and i > stack[-1][1]:
                n, a, _ = stack.pop()
                spans.append((a, i, n))
        spans += [(a, 10 ** 9, n) for n, a, _ in stack]
        spans.sort()
        return spans, fn


# ------------------------------------------------------------------------------------------------ site buckets
BUCKETS = ("moved_verified", "moved_unverified", "surfaced_for_decision", "correctly_kept", "claimed_not_routing", "missed")


def _near(d, f, line, tol=3):
    return d["file"] == f and d["line"] - tol <= int(line) <= d.get("line_end", d["line"]) + tol


def _ids(s):
    return {x for x in ((s or {}).get("via_client"), (s or {}).get("client_id")) if x}


class Coverage:
    """Which decision covers a site, strongest first:
      direct    the decision names the site (its line, or a range containing it; within 3 lines: a call spans lines)
      client    the decision names the client construction the site's call uses (the scanner's call -> client link; for a
                call the scanner can't see, a client in the same class, or the only client in the file)
      function  a keep / needs-decision on no site at all (e.g. a branch or a helper line) covers the sites of its enclosing
                function
      group     a keep / needs-decision on no site covers the undecided sites of a provider table: a file with 3+ sites
    Only direct and client coverage can claim a move; function and group coverage only surface or keep (never not_a_site)."""

    def __init__(self, decisions, inv_prod, static, clients, scope):
        self.decs, self.inv, self.static, self.clients, self.scope = decisions, inv_prod, static, clients, scope
        self.per_file = {}
        for i in inv_prod:
            self.per_file[i["file"]] = self.per_file.get(i["file"], 0) + 1
        for d in decisions:
            d["_clients"] = {c["id"] for c in clients if _near(d, c["file"], c["line"])}
            d["_on_site"] = any(_near(d, x["file"], x["line"]) for x in list(inv_prod) + list(static)) or bool(d["_clients"])

    def _sc(self, f, line):
        try:
            return self.scope(f, line) if self.scope else (None, None)
        except Exception:
            return (None, None)

    def clients_for(self, f, line, s):
        if s is not None and _ids(s):
            return _ids(s)
        same = [c for c in self.clients if c["file"] == f]
        if not same:
            return set()
        if len(same) == 1:
            return {same[0]["id"]}
        cls = self._sc(f, line)[0]
        return {c["id"] for c in same if cls and self._sc(f, c["line"])[0] == cls}

    def cover(self, f, lines, s=None, decided=None):
        """(decision, how) or (None, None). lines: the site's pristine line(s) (inventory line and its scanner call's)."""
        direct = [d for d in self.decs if any(_near(d, f, ln) for ln in lines)]
        if direct:
            dist = lambda d: min(0 if d["line"] <= ln <= d["line_end"] else min(abs(d["line"] - ln), abs(d["line_end"] - ln)) for ln in lines)
            return sorted(direct, key=lambda d: (dist(d), d["line_end"] - d["line"]))[0], "direct"
        linked = bool(s is not None and _ids(s))
        ids = self.clients_for(f, lines[0], s)
        # a claim (or a probe) credits only the calls the scanner links to the client; the same-class / only-client fallback
        # for calls the scanner can't see only carries a keep or a needs-decision (an embeddings call beside a chat client in
        # one file is not served by it)
        via = [d for d in self.decs if d["_clients"] & ids and (linked or d["bucket"] != "migrated")]
        if via:
            return (next((d for d in via if d["bucket"] == "migrated"), via[0])), "client"
        # a move is only ever claimed for a named site or client: a claim on one branch of a function says nothing about
        # the others. not_a_site is a statement about one line. So scope and group coverage only surface or keep.
        loose = [d for d in self.decs if d["file"] == f and not d["_on_site"] and d["bucket"] != "migrated" and d["outcome"] != "not_a_site"]
        fn = self._sc(f, lines[0])[1]
        if fn and fn != "<module>":
            infn = [d for d in loose if self._sc(f, d["line"])[1] == fn]
            if infn:
                return (next((d for d in infn if d["bucket"] == "surfaced"), infn[0])), "function"
        if self.per_file.get(f, 0) >= 3 and loose:
            return (next((d for d in loose if d["bucket"] == "surfaced"), loose[0])), "group"
        return None, None


def _through(s):
    """The code sends this call through the runtime (or straight to Router): what the route check simulates."""
    return s.get("outcome") == "migrated" or bool(s.get("_wrapped")) or bool(s.get("_native")) or \
        (s.get("kind") == "module_client" and s.get("installed"))


def claimed_by(d, how, s, has_channel=True):
    """Does the arm claim this site as moved? A decision naming the site or its client says so (migrated) or not (anything
    else). With no such decision, the code is the report: a call the code sends through the runtime is claimed (for B /
    B_prime, which have no report, that is the only rule). Function / group decisions never claim a move."""
    if has_channel and d is not None and how in ("direct", "client"):
        return d["bucket"] == "migrated"
    return bool(s is not None and _through(s))


KEEP_NEEDS_TRADEOFF = True  # a keep is correct on a movable site only when moving it takes a trade-off (movable()'s hint)


def classify(inv, static, clients, decisions, checks, dyn, scope=None, has_channel=True, operator_key=True):
    """Bucket every ground-truth site. inv: inventory sites (all); static: scanner calls (pristine lines) with id / via_client
    / client_id / outcome (+ _wrapped / _native); clients: scanner clients (pristine lines); decisions: resolved decisions;
    checks: {static id: {"routes", "reason"}} (the route check); dyn: re-verified dynamic evidence (pristine lines).
    Returns {"sites": [...], "counts": {...}, "unmatched": [...], "file_level": n}."""
    prod = [i for i in inv if i.get("prod")]
    cov = Coverage(decisions, prod, static, clients, scope)
    pairs, _, extra = HA.match(prod, static)
    np_pairs, _, extra = HA.match([i for i in inv if not i.get("prod")], extra)  # a moved test/script call is a wrong move too
    s_of = {id(i): s for i, s in pairs + np_pairs}
    gen = {"chat", "responses", "messages"}
    dyn_at = lambda f, lines, ids, surface: next((x for x in dyn if any(x["file"] == f and abs(int(x["line"]) - ln) <= 3 for ln in lines) or
                                                  (HA.fam(surface) in gen and any(_near(dict(file=c["file"], line=c["line"]), x["file"], x["line"])
                                                                                  for c in clients if c["id"] in ids))), None)
    rows = []
    for i in inv:
        mv, why_not, hint = movable(i, operator_key)
        s = s_of.get(id(i))
        lines = [int(i["line"])] + ([int(s["line"])] if s else [])
        d, how = cov.cover(i["file"], lines, s)
        claimed = claimed_by(d, how, s, has_channel)
        ck = checks.get(s["id"]) if s else None
        routes = bool(ck and ck.get("routes"))
        dx = dyn_at(i["file"], lines, _ids(s), i.get("surface")) if claimed else None  # a probe at a client: its scanner-linked calls
        visible = s is not None and (_through(s) or bool(_ids(s)) or s.get("kind") == "module_client")
        row = {"file": i["file"], "line": i["line"], "surface": i.get("surface"), "prod": bool(i.get("prod")), "movable": mv,
               "why_not_movable": why_not, "hint": hint, "expected_old": i.get("expected"),
               "scanner": ("%s:%s" % (s["file"], s.get("line_now", s["line"]))) if s else None,
               "decision": ({"outcome": d["outcome"], "how": how, "at": "%s:%s" % (d["file"], d.get("line_raw", d["line"])),
                             "why": d["why"][:200]} if d else None)}
        if claimed and (routes or dx):
            bucket, ev = "moved_verified", ("static: " + ck["reason"]) if routes else "dynamic: re-verified probe" + (
                " (at the client)" if not any(abs(int(dx["line"]) - ln) <= 3 for ln in lines) else "")
        elif claimed and visible:
            bucket, ev = "claimed_not_routing", (ck or {}).get("reason") or "not route-checked"
        elif claimed:
            bucket, ev = "moved_unverified", "reported migrated; the scanner sees no client for it and no probe re-verified it"
        elif d is not None and d["bucket"] == "surfaced":
            bucket, ev = "surfaced_for_decision", d["outcome"]
        elif d is not None and d["bucket"] == "kept" and d["why"].strip() and (hint or not KEEP_NEEDS_TRADEOFF):
            bucket, ev = "correctly_kept", hint
        elif d is not None and d["bucket"] == "kept":
            bucket, ev = "missed", "kept, but moving it takes no trade-off" if d["why"].strip() else "kept with no reason"
        else:
            bucket, ev = "missed", "no decision"
        if not mv:  # not ground-truth movable: only a real move matters (a wrong move); anything else is fine
            bucket, ev = ("wrong", ev) if bucket == "moved_verified" else ("not_movable", why_not)
        if not i.get("prod") and bucket == "not_movable":
            continue
        row.update(bucket=bucket, evidence=ev, _i=i, _s=s)
        rows.append(row)
    # an arm that consolidates or moves calls breaks line matching: a claimed, routing scanner call that pairs with no
    # inventory site takes a leftover site of the same file and call family (nearest line; movable first). Every arm.
    file_level = 0
    openai_gen = {"chat", "responses"}
    for s in extra:
        d, how = cov.cover(s["file"], [int(s["line"])], s)
        claimed = claimed_by(d, how, s, has_channel)
        ck = checks.get(s["id"]) or {}
        if not (claimed and ck.get("routes")):
            continue
        cands = [r for r in rows if r["file"] == s["file"] and r["_s"] is None and r["bucket"] not in ("moved_verified", "wrong") and
                 (HA.fam(r["surface"]) == HA.fam(s.get("surface")) or {HA.fam(r["surface"]), HA.fam(s.get("surface"))} <= openai_gen)]
        if not cands:
            continue
        r = sorted(cands, key=lambda r: (not r["movable"], abs(int(r["line"]) - int(s["line"]))))[0]
        r["_s"] = s
        r.update(bucket="moved_verified" if r["movable"] else "wrong", evidence="static (file-level match): " + ck["reason"],
                 scanner="%s:%s" % (s["file"], s.get("line_now", s["line"])))
        file_level += 1
    paired = {id(r["_s"]) for r in rows if r["_s"] is not None}
    unmatched = []
    for s in static:
        if id(s) in paired:
            continue
        d, how = cov.cover(s["file"], [int(s["line"])], s)
        claimed = claimed_by(d, how, s, has_channel)
        if claimed and (checks.get(s["id"]) or {}).get("routes"):
            unmatched.append({"file": s["file"], "line": s["line"], "surface": s.get("surface"), "evidence": "static: valid Router request"})
    counts = {b: sum(1 for r in rows if r["movable"] and r["bucket"] == b) for b in BUCKETS}
    counts["wrong"] = sum(1 for r in rows if r["bucket"] == "wrong")
    counts["movable_total"] = sum(1 for r in rows if r["movable"])
    counts["one_approval_away"] = counts["moved_verified"] + counts["surfaced_for_decision"]
    for r in rows:
        r.pop("_i", None)
        r.pop("_s", None)
    return {"sites": rows, "counts": counts, "unmatched": unmatched, "file_level": file_level}


# ------------------------------------------------------------------------------------------------ destructive edits
_KEY_ENV = re.compile(r"\b([A-Z][A-Z0-9_]*_API_KEY|OPENAI_ORG_ID|OPENAI_ORGANIZATION)\b")
_KEY_FIELD = re.compile(r"\b([A-Za-z_]*api_key[A-Za-z_]*|[A-Za-z]*ApiKey|apiKey)\b")
_PY_DEF = re.compile(r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
_TS_DEF = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?(function\*?|class)\s+([A-Za-z_$][\w$]*)")


def destructive_edits(patch):
    """What the arm's diff (diff.patch) removed that kept traffic or the app still needs: provider-key references
    (*_API_KEY env names, api_key / apiKey config fields: removed more often than re-added anywhere in the diff, so a wrap
    that keeps `api_key=` is not counted), deleted files, and deleted classes/functions (not re-defined anywhere in the diff).
    count = distinct key names + deleted files + deleted classes/functions.
    Returns {"count", "keys_removed", "files_deleted", "defs_deleted", "examples"}."""
    files_deleted, cur, deleted_file = [], None, False
    rem_k, add_k, rem_d, add_d = {}, {}, {}, set()
    for l in (patch or "").splitlines():
        if l.startswith("diff --git "):
            cur, deleted_file = l.split(" b/", 1)[-1] if " b/" in l else None, False
            continue
        if l.startswith("deleted file mode"):
            deleted_file = True
            if cur:
                files_deleted.append(cur)
            continue
        if l.startswith(("+++", "---", "index ", "@@", "new file mode", "similarity", "rename ")) or not l or l[0] not in "+-":
            continue
        body = l[1:]
        keys = [k for k in _KEY_ENV.findall(body) if not k.startswith("RAMP_ROUTER")] + _KEY_FIELD.findall(body)
        tgt = rem_k if l[0] == "-" else add_k
        for k in keys:
            tgt.setdefault(k, []).append(cur)
        m = (_PY_DEF if (cur or "").endswith(".py") else _TS_DEF).match(body)
        if m and not (m.group(2).startswith("__") and m.group(2).endswith("__")):  # a dunder goes with its class
            name = "%s %s" % ("class" if m.group(1) == "class" else "def", m.group(2))
            if l[0] == "-":
                rem_d.setdefault(name, []).append(cur)
            else:
                add_d.add(name)
    keys_removed = []
    for k, where in sorted(rem_k.items()):
        n = len(where) - len(add_k.get(k, []))
        if n > 0:
            keys_removed.append({"name": k, "n": n, "files": sorted({w for w in where if w})[:4]})
    defs_deleted = [{"name": k, "files": sorted({w for w in v if w})[:3]} for k, v in sorted(rem_d.items()) if k not in add_d]
    count = len(keys_removed) + len(files_deleted) + len(defs_deleted)  # distinct names: a key removed from 5 places counts once
    ex = ["deleted %s" % f for f in files_deleted[:3]] + ["removed %s (%s)" % (d["name"], ", ".join(d["files"])) for d in defs_deleted[:3]] + \
        ["removed %s x%d (%s)" % (k["name"], k["n"], ", ".join(k["files"])) for k in keys_removed[:4]]
    return {"count": count, "keys_removed": keys_removed, "files_deleted": files_deleted, "defs_deleted": defs_deleted, "examples": ex}


# ------------------------------------------------------------------------------------------------ cost and time
def run_minutes(out_dir, res, led, ts):
    """(minutes, source): the harness's wall clock for the trial (agent.json, written by every arm's trial), else result.json,
    else the ledger's agent minutes, else the transcript's. The same order for every arm."""
    meta_p = os.path.join(out_dir, "agent.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    for v, src in ((meta.get("minutes"), "agent.json"), ((res or {}).get("minutes"), "result.json"),
                   (((led or {}).get("cost") or {}).get("agent_minutes"), "ledger cost"), ((ts or {}).get("agent_minutes"), "transcript")):
        if isinstance(v, (int, float)):
            return float(v), src
    return None, None


# ------------------------------------------------------------------------------------------------ one trial
def score_one(arm, name, dst=None, measured=None, reported=None, broken=None):
    """sites_moved.json for one trial: every ground-truth site in exactly one bucket (moved_verified = valid Router traffic),
    wrong moves, destructive edits, $ and minutes. `reported` is accepted for older callers and not used: decisions are read
    from the arm's own channel (raw_decisions) so every arm is read the same way."""
    import ablation_b2 as B  # installs the B2/Cnf/D reported-outcome hooks on run_v3
    out_dir = os.path.join(R.RUNS, arm, name)
    dst = dst or os.path.join(R.TRIALS, arm, name)
    fallback = None
    if not os.path.isdir(dst):
        rec = reconstruct(name, out_dir)
        fallback = "trial tree missing: rebuilt from pristine + diff.patch" if rec else "trial tree missing and not rebuildable"
        dst = rec
    led = _ledger(dst if not fallback else None, out_dir)
    res_p = os.path.join(out_dir, "result.json")
    res = json.load(open(res_p)) if os.path.exists(res_p) else {}
    meta_p = os.path.join(out_dir, "agent.json")
    agent = (json.load(open(meta_p)) if os.path.exists(meta_p) else {}).get("agent")
    ts = R.transcript_stats(R.transcript_path(out_dir)) if agent is not None else None
    usd = ts["usd"] if ts and ts.get("usd") is not None else (res.get("usd") or 0.0)
    minutes, min_src = run_minutes(out_dir, res, led, ts)
    patch_p = os.path.join(out_dir, "diff.patch")
    row = {"arm": arm, "repo": name, "set": R.EVAL_SET, "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "scorer": "v5", "fallback": fallback,
           "broken": res.get("broken") if broken is None else broken, "usd": usd, "cost_source": (ts or {}).get("usd_source"),
           "minutes": minutes, "minutes_source": min_src, "agent_minutes": (ts or {}).get("agent_minutes"),
           "tests_before": res.get("tests_before"), "tests_after": res.get("tests_after"),
           "destructive": destructive_edits(open(patch_p).read()) if os.path.exists(patch_p) else None,
           "tradeoffs": len(_tradeoffs(led)), "tradeoff_detail": [{"site": t.get("site"), "levers": t.get("levers"), "loses": t.get("loses")}
                                                                for t in _tradeoffs(led)]}
    if dst is None:
        row.update(error=fallback, not_run="trial tree missing and not rebuildable from diff.patch")
        os.makedirs(out_dir, exist_ok=True)
        json.dump(row, open(os.path.join(out_dir, OUT_NAME), "w"), indent=1, default=str)
        print("%-10s %-30s NOT RUN: %s" % (arm, name, row["not_run"]), flush=True)
        return row
    # not a measurement of the arm: an infrastructure failure (API error / no output), or an agent with a decision channel
    # that decided nothing (never scored from the rules' proposals or the code as it stands: that would credit the tool)
    n_dec = B.decisions(arm, dst, name, out_dir) if agent is not None else None
    row["decisions"] = n_dec
    row["invalid"] = R.infra_failure(out_dir, agent) or (
        "the agent made no per-site decisions (%s)" % ("agent exit %s" % agent.get("exit")) if n_dec == 0 else None)
    if row["invalid"]:
        os.makedirs(out_dir, exist_ok=True)
        json.dump(row, open(os.path.join(out_dir, OUT_NAME), "w"), indent=1, default=str)
        print("%-10s %-30s INVALID: %s" % (arm, name, row["invalid"]), flush=True)
        if fallback and dst and dst.startswith(tempfile.gettempdir()):
            shutil.rmtree(dst, ignore_errors=True)
        return row
    row["tree_matches_saved_patch"] = None if fallback else tree_matches_patch(dst, out_dir)
    static = B._orig_measure(dst, name) if measured is None or dst not in R.CLIENTS else \
        [s for s in measured if not str(s.get("reason", "")).startswith("dynamic:")]
    clients = R.CLIENTS.get(dst) or []
    pm = R.PristineMap(dst, name)
    inv = inventory_all(name)
    anchors = {}
    for x in static + clients + [i for i in inv or [] if i.get("prod")]:
        anchors.setdefault(x["file"], []).append(int(x["line"]))
    decisions = resolve_decisions(raw_decisions(arm, dst, name, out_dir), pm, anchors)
    # (a) dynamic evidence: the harness re-runs the agent's recorded probes (cached per run in reverify.json)
    # The record the harness made right after the trial wins: re-running the probes later is not reproducible (a repo's
    # shared env has its editable install re-pointed at whichever trial copy ran last, and the cache key moves once
    # score_trial `git add`s the tree), so a fresh re-run happens only for a run that has no record.
    dyn, dyn_src = [], None
    rv_p = os.path.join(out_dir, "reverify.json")
    if led and (led.get("probes") or {}) and os.path.exists(rv_p):
        rv = json.load(open(rv_p))
        when = "after the trial" if str(rv.get("at") or "") <= str(res.get("scored_at") or "") else "at an earlier rescoring"
        dyn, dyn_src = rv.get("sites") or [], "harness re-ran the agent's probes %s (reverify.json, %s)" % (when, rv.get("at"))
    elif led and (led.get("probes") or {}) and not fallback:
        try:
            dyn, dyn_src = B.reverify(dst, name), "harness re-ran the agent's probes at rescoring (no record from the trial)"
        except Exception as e:
            dyn_src = "reverify failed: %s" % str(e)[:200]
    elif led and led.get("confirmations"):
        dyn = [{"file": k.rsplit(":", 1)[0], "line": int(k.rsplit(":", 1)[1]), "line_now": int(k.rsplit(":", 1)[1])} for k in led["confirmations"]]
        dyn_src = "ledger confirmations (not re-verified)"
    dyn = [dict(x, line=resolve_line(pm, x["file"], int(x.get("line_now") or x["line"]), anchors.get(x["file"], ()))[0]) for x in dyn]
    # (b) the route check: every scanner call of the code as it stands, through the real runtime + the tree's policy
    policy, pol_src = load_policy(dst)
    if policy is None and fallback and led:
        policy, pol_src = policy_from_ledger(led), "ledger trade-offs"
    cl = {c["id"]: c for c in clients}
    for s in static:
        c = cl.get(s.get("via_client") or "") or cl.get(s.get("client_id") or "")
        s["_wrapped"] = bool(c and c.get("wrapped"))
        try:
            from router_migrate import plan as planmod
            s["_native"] = bool(c and (planmod.client_target(c) == "router" or planmod._router_by_assignment(dst, c)))
        except Exception:
            s["_native"] = False
    hosts, models = {}, {}
    if inv is not None:  # where each call's requests really go (a config-driven base URL / ambiguous client the scanner can't resolve)
        for i, p in HA.match([i for i in inv if i.get("prod")], static)[0]:
            if endpoint_host(i.get("endpoint")):
                hosts[p["id"]] = endpoint_host(i.get("endpoint"))
    for s in static:  # config-driven clients: the shipped setup's host and model
        pc = production_config(name).get(s["file"])
        if pc and s["id"] not in hosts:
            hosts[s["id"]], models[s["id"]] = pc.get("host"), pc.get("model")
    checks = {}
    if static:
        from router_migrate.route_check import check_sites
        for r in check_sites(dst, static, policy, hosts, models):
            checks[r["id"]] = {"routes": r["routes"], "reason": r.get("reason"), "model": r.get("model"), "host": r.get("host"),
                               "kwargs": r.get("kwargs"), "path": r.get("path")}
    scopes = Scopes(os.path.join(R.RE.WORK, "pristine", name))
    row.update({"policy": pol_src, "dynamic_source": dyn_src, "dynamic_sites": len(dyn),
                "routed_static": sum(1 for s in static if s.get("outcome") == "migrated"),
                "through_runtime": sum(1 for s in static if _through(s)), "route_checked": len(checks),
                "decisions_resolved": [{"at": "%s:%s" % (d["file"], d.get("line_raw", d["line"])), "pristine_line": d["line"],
                                        "reading": d.get("reading", "pristine"), "outcome": d["outcome"], "source": d["source"]}
                                       for d in decisions],
                "claimed_migrated": sum(1 for d in decisions if d["bucket"] == "migrated") if channel(arm) else
                sum(1 for s in static if _through(s))})
    has_ch = channel(arm) is not None
    if inv is not None:
        cls = classify(inv, static, clients, decisions, checks, dyn, scopes, has_ch, has_operator_key(inv))
        c = cls["counts"]
        sites = cls["sites"]
        row.update({"movable_total": c["movable_total"], "buckets": {b: c[b] for b in BUCKETS}, "one_approval_away": c["one_approval_away"],
                    "sites_moved_movable": c["moved_verified"], "sites_moved_wrong": c["wrong"],
                    "sites_moved": c["moved_verified"] + c["wrong"] + len(cls["unmatched"]),
                    "sites_moved_unmatched": len(cls["unmatched"]), "file_level_matches": cls["file_level"],
                    "old_gt_total": sum(1 for i in inv if i.get("prod") and i.get("expected") == "migrated"),
                    "sites_moved_old_gt": sum(1 for r in sites if r["bucket"] in ("moved_verified", "wrong") and r["expected_old"] == "migrated"),
                    "site_buckets": sites,
                    "moved": [{"file": r["file"], "line": r["line"], "surface": r["surface"], "evidence": r["evidence"]} for r in sites
                              if r["bucket"] in ("moved_verified", "wrong")] + cls["unmatched"],
                    "wrong_detail": [{"file": r["file"], "line": r["line"], "why_not_movable": r["why_not_movable"], "evidence": r["evidence"]}
                                     for r in sites if r["bucket"] == "wrong"],
                    "unmatched_detail": cls["unmatched"],
                    "wrapped_but_no_router_traffic": [{"file": r["file"], "line": r["line"], "surface": r["surface"], "why": r["evidence"]}
                                                      for r in sites if r["bucket"] == "claimed_not_routing"],
                    "movable_missed": [{"file": r["file"], "line": r["line"], "hint": r["hint"], "why": r["evidence"]}
                                       for r in sites if r["movable"] and r["bucket"] == "missed"]})
    else:  # no inventory (RM_EVAL_SET=dev): moved = claimed calls with valid Router traffic or re-verified probes
        cov = Coverage(decisions, [], static, clients, scopes)
        moved, lost = [], []
        for s in static:
            d, how = cov.cover(s["file"], [int(s["line"])], s)
            claimed = claimed_by(d, how, s, has_ch)
            if not claimed:
                continue
            ck = checks.get(s["id"]) or {}
            dx = next((x for x in dyn if x["file"] == s["file"] and abs(int(x["line"]) - int(s["line"])) <= 3), None)
            if ck.get("routes") or dx:
                moved.append({"file": s["file"], "line": s["line"], "surface": s.get("surface"),
                              "evidence": ("static: " + ck["reason"]) if ck.get("routes") else "dynamic: " + str(dyn_src)})
            else:
                lost.append({"file": s["file"], "line": s["line"], "surface": s.get("surface"), "why": ck.get("reason") or "not checked"})
        row.update({"sites_moved": len(moved), "moved": moved, "wrapped_but_no_router_traffic": lost, "movable_total": None,
                    "sites_moved_movable": None, "sites_moved_wrong": None, "old_gt_total": None, "sites_moved_old_gt": None})
    os.makedirs(out_dir, exist_ok=True)
    json.dump(row, open(os.path.join(out_dir, OUT_NAME), "w"), indent=1, default=str)
    if fallback and dst and dst.startswith(tempfile.gettempdir()):
        shutil.rmtree(dst, ignore_errors=True)
    b = row.get("buckets") or {}
    print("%-10s %-30s moved=%s/%s unverified=%s surfaced=%s kept=%s not-routing=%s missed=%s wrong=%s destructive=%s broken=%s" % (
        arm, name, row.get("sites_moved_movable", row.get("sites_moved")), row.get("movable_total"), b.get("moved_unverified"),
        b.get("surfaced_for_decision"), b.get("correctly_kept"), b.get("claimed_not_routing"), b.get("missed"), row.get("sites_moved_wrong"),
        (row.get("destructive") or {}).get("count"), row["broken"]), flush=True)
    return row


def scored_runs(arms=None, only=None):
    out = []
    for f in sorted(glob.glob(os.path.join(R.RUNS, "*", "*", "result.json"))):
        arm, name = f.split(os.sep)[-3], f.split(os.sep)[-2]
        if arm == "baseline" or (arms and arm not in arms) or (only and name not in only):
            continue
        out.append((arm, name))
    return out


def cmd_score(a):
    todo = scored_runs(a.arms.split(",") if a.arms else None, a.only.split(",") if a.only else None)
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(lambda x: R._safe(score_one, *x), todo))
    report()


# ------------------------------------------------------------------------------------------------ coverage
def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def run_status(arm, name):
    """(status, why): scored | invalid | running | not_run. not_run covers a run that never started, one interrupted
    before it was scored (no result.json, its runner gone), and one whose trial tree is missing and can't be rebuilt."""
    d = os.path.join(R.RUNS, arm, name)
    sm_p, res_p = os.path.join(d, OUT_NAME), os.path.join(d, "result.json")
    if not os.path.isdir(d):
        return "not_run", "no run"
    st = {}
    try:
        st = json.load(open(os.path.join(d, "status.json")))
    except (OSError, ValueError):
        pass
    if not os.path.exists(res_p):
        if st.get("status") == "running" and _pid_alive(st.get("pid")):
            return "running", "in progress (pid %s)" % st.get("pid")
        meta = {}
        try:
            meta = json.load(open(os.path.join(d, "agent.json")))
        except (OSError, ValueError):
            pass
        ag = meta.get("agent") or {}
        ex = ag.get("exit") if ag.get("exit") is not None else ag.get("run_exit")
        return "not_run", "interrupted before scoring (no result.json%s)" % ((", agent exit %s" % ex) if ex is not None else "")
    try:
        sm = json.load(open(sm_p))
    except (OSError, ValueError):
        return "not_run", "not scored"
    if sm.get("not_run"):
        return "not_run", sm["not_run"]
    if sm.get("invalid") or json.load(open(res_p)).get("invalid"):
        return "invalid", sm.get("invalid") or json.load(open(res_p)).get("invalid")
    return "scored", None


def attempted_repos():
    """The repos of this set that any arm ran (the held-out set is partial: its tables cover only these)."""
    return sorted({os.path.basename(p) for p in glob.glob(os.path.join(R.RUNS, "*", "*")) if os.path.isdir(p)
                   and not os.path.basename(p).startswith("_") and os.path.basename(os.path.dirname(p)) != "baseline"} & set(R.repos()),
                  key=str.lower)


# ------------------------------------------------------------------------------------------------ report
ARM_ORDER = ["A", "B", "B_prime", "C", "B2_r1", "Cnf_r1", "D_r1", "D_r2", "D3min", "D3free"]
COLS = ("moved_verified", "moved_unverified", "surfaced_for_decision", "correctly_kept", "claimed_not_routing", "missed")


def _arm_key(a):
    return (ARM_ORDER.index(a) if a in ARM_ORDER else len(ARM_ORDER), a)


def load_rows(include_invalid=False):
    rows = {}
    for f in sorted(glob.glob(os.path.join(R.RUNS, "*", "*", OUT_NAME))):
        r = json.load(open(f))
        if (r.get("invalid") or r.get("not_run")) and not include_invalid:
            continue
        if not os.path.exists(os.path.join(os.path.dirname(f), "result.json")):
            continue
        rows.setdefault(r["arm"], []).append(r)
    return rows


def invalid_rows():
    return {a: [r for r in rs if r.get("invalid")] for a, rs in load_rows(include_invalid=True).items() if any(r.get("invalid") for r in rs)}


OUT_DIR = HERE if R.EVAL_SET in ("v3", "dev") else R.RE.SETDIR  # heldout / heldout2 write their tables beside their REPOS.json


def summarize(rs):
    """Per-arm totals over its scored runs."""
    t = {"runs": len(rs), "movable": sum(r.get("movable_total") or 0 for r in rs),
         "sites_moved": sum(r.get("sites_moved") or 0 for r in rs), "wrong": sum(r.get("sites_moved_wrong") or 0 for r in rs),
         "unmatched": sum(r.get("sites_moved_unmatched") or 0 for r in rs),
         "one_approval_away": sum(r.get("one_approval_away") or 0 for r in rs),
         "destructive": sum((r.get("destructive") or {}).get("count") or 0 for r in rs),
         "destructive_runs": sum(1 for r in rs if (r.get("destructive") or {}).get("count")),
         "broken": sum(1 for r in rs if r.get("broken")), "tradeoffs": sum(r.get("tradeoffs") or 0 for r in rs),
         "usd": round(sum(r.get("usd") or 0 for r in rs), 2), "minutes": round(sum(r.get("minutes") or 0 for r in rs), 1),
         "minutes_missing": sum(1 for r in rs if r.get("minutes") is None),
         "old_gt": sum(r.get("sites_moved_old_gt") or 0 for r in rs), "old_gt_total": sum(r.get("old_gt_total") or 0 for r in rs),
         "fallbacks": sum(1 for r in rs if r.get("fallback")), "stale_trees": sum(1 for r in rs if r.get("tree_matches_saved_patch") is False)}
    for b in COLS:
        t[b] = sum((r.get("buckets") or {}).get(b) or 0 for r in rs)
    return t


def report():
    rows = load_rows()
    v3 = R.INVENTORY_DIR is not None  # a set with a ground-truth inventory
    repos = attempted_repos() if R.EVAL_SET == "heldout" else sorted(R.repos(), key=str.lower)
    arms = sorted({a for a in os.listdir(R.RUNS) if os.path.isdir(os.path.join(R.RUNS, a)) and a != "baseline"}, key=_arm_key)
    status = {(a, n): run_status(a, n) for a in arms for n in repos}
    cls = {}
    if v3:
        for n in repos:
            inv = inventory_all(n) or []
            cls[n] = [(i, movable(i, has_operator_key(inv))) for i in inv if i.get("prod")]
    movable_total = sum(1 for n in cls for _, m in cls[n] if m[0])
    summary = {}
    for arm in arms:
        rs = [r for r in rows.get(arm, []) if r["repo"] in repos]
        t = summarize(rs)
        t["invalid"] = sum(1 for n in repos if status[(arm, n)][0] == "invalid")
        t["not_run"] = sum(1 for n in repos if status[(arm, n)][0] == "not_run")
        t["running"] = sum(1 for n in repos if status[(arm, n)][0] == "running")
        t["usd_invalid"] = round(sum(r.get("usd") or 0 for r in load_rows(True).get(arm, []) if r.get("invalid")), 2)
        summary[arm] = t
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "set": R.EVAL_SET, "scorer": "v5", "repos": repos,
           "movable_total": movable_total, "arms": summary,
           "status": {a: {n: {"status": status[(a, n)][0], "why": status[(a, n)][1]} for n in repos} for a in arms},
           "per_repo": {a: {r["repo"]: {k: r.get(k) for k in ("movable_total", "buckets", "one_approval_away", "sites_moved_movable",
                                                               "sites_moved_wrong", "sites_moved_unmatched", "broken", "usd", "minutes")}
                            | {"destructive": (r.get("destructive") or {}).get("count"),
                               "destructive_examples": (r.get("destructive") or {}).get("examples")}
                            for r in rows.get(a, []) if r["repo"] in repos} for a in arms},
           "movable_per_repo": {n: sum(1 for _, m in cls.get(n, []) if m[0]) for n in repos}}
    name = "sites_moved.json" if R.EVAL_SET == "v3" else "sites_moved_%s.json" % R.EVAL_SET
    json.dump(out, open(os.path.join(OUT_DIR, name), "w"), indent=1, default=str)
    L = ["# Sites moved: every ground-truth site in one bucket (%s set, scorer v5)" % R.EVAL_SET, "",
         "Generated by `eval/v3/score_moved.py` (`RM_EVAL_SET=%s ... score` then `report`); the method and the cross-set summary are "
         "in eval/RESULTS.md." % R.EVAL_SET, ""]
    if v3:
        L += ["Ground truth: **%d movable** production LLM generation sites across %d repos (`movable()` in score_moved.py)." % (
            movable_total, len(repos)), "",
              "| arm | runs | moved (verified) | moved unverified | one approval away | surfaced | correctly kept | claimed, not routing | "
              "missed | wrong | destructive edits | broken repos | $ | minutes |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm in arms:
            t = summary[arm]
            L.append("| %s | %s | %d / %d | %d | %d | %d | %d | %d | %d | %d | %d (%d runs) | %d/%d | %.2f | %s |" % (
                arm, _runs_cell(t), t["moved_verified"], t["movable"], t["moved_unverified"], t["one_approval_away"],
                t["surfaced_for_decision"], t["correctly_kept"], t["claimed_not_routing"], t["missed"], t["wrong"], t["destructive"],
                t["destructive_runs"], t["broken"], t["runs"], t["usd"], _min_cell(t)))
    else:
        L += ["No ground-truth inventory for this set: moved = claimed calls with valid Router traffic.", "",
              "| arm | runs | sites moved | broken repos | $ | minutes |", "|---|---|---|---|---|---|"]
        for arm in arms:
            t = summary[arm]
            L.append("| %s | %s | %d | %d/%d | %.2f | %s |" % (arm, _runs_cell(t), t["sites_moved"], t["broken"], t["runs"], t["usd"], _min_cell(t)))
    out["table_md"] = [l for l in L if l.startswith("|")]
    gaps = [(a, n, status[(a, n)]) for a in arms for n in repos if status[(a, n)][0] != "scored"]
    if gaps:
        L += ["", "Runs not in the counts above: " + "; ".join("%s %s: %s (%s)" % (a, n, s, why) for a, n, (s, why) in gaps) + "."]
    L += ["", "## Per repo", "", "Cell: moved verified / movable; then u = unverified, s = surfaced, k = correctly kept, n = claimed but "
          "not routing, m = missed, w = wrong, d = destructive edits, b = broken. `-` not run, `inv` invalid.", "",
          "| repo | movable | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    for n in repos:
        cells = []
        for arm in arms:
            st = status[(arm, n)][0]
            r = next((x for x in rows.get(arm, []) if x["repo"] == n), None)
            if st != "scored" or r is None:
                cells.append({"invalid": "inv", "running": "running"}.get(st, "-"))
                continue
            cells.append(_cell(r))
        L.append("| %s | %d | %s |" % (n, sum(1 for _, m in cls.get(n, []) if m[0]), " | ".join(cells)))
    L += ["", "## Site buckets (every movable site, every scored run)", "", "| arm | repo | site | bucket | evidence / reason | decision |",
          "|---|---|---|---|---|---|"]
    for arm in arms:
        for r in sorted(rows.get(arm, []), key=lambda r: r["repo"].lower()):
            for x in r.get("site_buckets") or []:
                if x["movable"] or x["bucket"] == "wrong":
                    dd = x.get("decision") or {}
                    L.append("| %s | %s | `%s:%s` | %s | %s | %s |" % (arm, r["repo"], x["file"], x["line"], x["bucket"],
                                                                     _md(x.get("evidence"))[:140],
                                                                     ("%s (%s, %s)" % (dd.get("outcome"), dd.get("how"), dd.get("at"))) if dd else ""))
    L += ["", "## Destructive edits (from each run's diff.patch)", "", "| arm | repo | count | examples |", "|---|---|---|---|"]
    for arm in arms:
        for r in sorted(rows.get(arm, []), key=lambda r: r["repo"].lower()):
            de = r.get("destructive") or {}
            if de.get("count"):
                L.append("| %s | %s | %d | %s |" % (arm, r["repo"], de["count"], _md("; ".join(de.get("examples") or []))[:300]))
    if v3:
        L += ["", "## Ground truth: not movable (every production site the classifier excludes)", "",
              "| repo | site | surface | endpoint | reason |", "|---|---|---|---|---|"]
        for n in repos:
            for i, m in cls[n]:
                if not m[0]:
                    L.append("| %s | `%s:%s` | %s | %s | %s |" % (n, i["file"], i["line"], i.get("surface"), _md(i.get("endpoint"))[:50], m[1]))
        L += ["", "### Movable sites whose move takes a trade-off (a reasoned keep of these is `correctly_kept`)", "",
              "| repo | site | trade-off |", "|---|---|---|"]
        for n in repos:
            for i, m in cls[n]:
                if m[0] and m[2]:
                    L.append("| %s | `%s:%s` | %s |" % (n, i["file"], i["line"], m[2]))
    fn = "SITES_MOVED.md" if R.EVAL_SET == "v3" else "SITES_MOVED_%s.md" % R.EVAL_SET.upper()
    open(os.path.join(OUT_DIR, fn), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:12 + len(summary)]))
    return out


def _md(x):
    return str(x if x is not None else "").replace("|", "/").replace("\n", " ")


def _runs_cell(t):
    s = "%d" % t["runs"]
    extra = ["%d invalid" % t["invalid"]] if t.get("invalid") else []
    extra += ["%d not run" % t["not_run"]] if t.get("not_run") else []
    extra += ["%d running" % t["running"]] if t.get("running") else []
    return s + (" (%s)" % ", ".join(extra) if extra else "")


def _min_cell(t):
    return "%.0f" % t["minutes"] + (" (%d missing)" % t["minutes_missing"] if t.get("minutes_missing") else "")


def _cell(r):
    b = r.get("buckets") or {}
    tags = [("u", b.get("moved_unverified")), ("s", b.get("surfaced_for_decision")), ("k", b.get("correctly_kept")),
            ("n", b.get("claimed_not_routing")), ("m", b.get("missed")), ("w", r.get("sites_moved_wrong")),
            ("d", (r.get("destructive") or {}).get("count"))]
    return "%s/%s %s%s" % (r.get("sites_moved_movable"), r.get("movable_total"), " ".join("%s%d" % (k, v) for k, v in tags if v),
                           " b" if r.get("broken") else "")


def _tests(t):
    if not t:
        return "?"
    if t.get("passed") is not None:
        return "%s/%s" % (t.get("passed"), (t.get("passed") or 0) + (t.get("failed") or 0) + (t.get("errors") or 0))
    return "exit %s" % t.get("exit")


# ------------------------------------------------------------------------------------------------ eval/RESULTS.md (both sets)
PRE_V5 = "c4ade05"  # the last commit scored by the pre-v5 scorer (the "before" column of the scorer change table)
SETS_FOR_RESULTS = (("dev", "eval/v3/runs", "eval/v3/sites_moved.json", "eval/v3/SITES_MOVED.md"),
                    ("held-out", "eval/heldout/runs_agents", "eval/heldout/sites_moved_heldout.json", "eval/heldout/SITES_MOVED_HELDOUT.md"))


def _git(*args):
    return subprocess.run(["git"] + list(args), cwd=R.ROOT, capture_output=True, text=True)


def before_v5(runs_rel):
    """Per arm, the pre-v5 scorer's totals over its valid runs (the sites_moved.json files saved at PRE_V5)."""
    out = {}
    for p in _git("ls-tree", "-r", "--name-only", PRE_V5, runs_rel).stdout.split():
        if not p.endswith("/" + OUT_NAME):
            continue
        try:
            r = json.loads(_git("show", "%s:%s" % (PRE_V5, p)).stdout)
        except ValueError:
            continue
        if r.get("invalid"):
            continue
        t = out.setdefault(r["arm"], {"runs": 0, "moved": 0, "movable": 0, "wrong": 0, "usd": 0.0, "minutes": 0.0, "repos": {}})
        t["runs"] += 1
        t["moved"] += r.get("sites_moved_movable") or 0
        t["movable"] += r.get("movable_total") or 0
        t["wrong"] += r.get("sites_moved_wrong") or 0
        t["usd"] += r.get("usd") or 0
        t["minutes"] += r.get("minutes") or 0
        t["repos"][r["repo"]] = r.get("sites_moved_movable")
    return out


def _arm_table(d):
    L = ["| arm | runs scored | moved (verified) / movable | moved unverified | one approval away | surfaced | correctly kept | "
         "claimed, not routing | missed | wrong | destructive edits (runs) | broken repos | $ | minutes |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in sorted(d["arms"], key=_arm_key):
        t = d["arms"][a]
        L.append("| %s | %s | **%d** / %d | %d | %d | %d | %d | %d | %d | %d | %d (%d) | %d/%d | %.2f | %s |" % (
            a, _runs_cell(t), t["moved_verified"], t["movable"], t["moved_unverified"], t["one_approval_away"], t["surfaced_for_decision"],
            t["correctly_kept"], t["claimed_not_routing"], t["missed"], t["wrong"], t["destructive"], t["destructive_runs"], t["broken"],
            t["runs"], t["usd"], _min_cell(t)))
    return L


def _repo_table(d):
    arms = sorted(d["arms"], key=_arm_key)
    L = ["| repo | movable | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    for n in d["repos"]:
        cells = []
        for a in arms:
            st = d["status"][a][n]["status"]
            r = d["per_repo"][a].get(n)
            cells.append(_cell(r) if st == "scored" and r else {"invalid": "inv", "running": "running"}.get(st, "-"))
        L.append("| %s | %d | %s |" % (n, d["movable_per_repo"].get(n, 0), " | ".join(cells)))
    return L


METHOD = """## Method

**Ground truth.** Each repo's blind inventory (`eval/v3/inventory*/`, `eval/heldout/inventory/`) lists every LLM call site.
`movable()` in `eval/v3/score_moved.py` classifies each one mechanically from the inventory's fields (no LLM judgment): a site
is movable when it is production code, an LLM generation call, and not a hard limit (no Router endpoint: embeddings, images,
audio, files, moderation, legacy completions, model lists; provider-side state actually used; a local/self-hosted model; the
app's own proxy; an end user's own key in an app with no operator key). Movable sites whose move takes a trade-off (another
vendor's endpoint, a per-user key, Azure/Bedrock hosting, beta flags, a raw-HTTP or other-SDK call) stay movable, with the
trade-off recorded as a hint.

**Moved** (the headline traffic metric) = the arm claims the site AND it sends a valid Router request: the scanner's call is
sent, as a representative request built from its static kwargs, through the real vendored runtime with the trial tree's
compiled policy (model_map / drop_params / drop_betas apply) to router-sim and accepted (the product's own route check,
`router_migrate.route_check.check_sites`, i.e. what the agent sees as `check --all`); or the harness re-ran the agent's own
recorded probes on the final code right after the trial and saw a valid request from that site or from the client it uses.
Trial-tree lines map back to the pristine source through the trial's own diff hunks; a line in an arm's report is read both as
a current and as a pristine line, and the reading that lands on a known site wins.

**Claims.** A decision in the arm's report that names the site, or the client construction its call uses, says whether the
arm claims it (migrated) or not (anything else). With no such decision the code is the report: a call the code sends through
the runtime is claimed. Arms without a report (B, B_prime) are judged by their code only. A and the floor arms (C, B2) report
through the floor plan plus the agent's claims / MIGRATION_REPORT.json; Cnf and D through ledger claims; D3 through
MIGRATION.json.

**Buckets.** Every movable site gets exactly one:

| bucket | meaning |
|---|---|
| moved_verified | claimed and verified as above (the `moved` count) |
| moved_unverified | claimed, but the scanner sees no client for the call and no probe re-verified it (hand-built / dynamic clients) |
| surfaced_for_decision | the report hands it to a human: needs_decision / flagged / needs_human / handoff (a floor arm's plan flags count) |
| correctly_kept | kept / cannot_route with a reason, on a site whose move takes a trade-off (port, vendor or model swap, per-user key, beta flags) |
| claimed_not_routing | claimed, the static check sees the call, and the route check says it stays on the provider |
| missed | none of the above: no decision, or a keep of a site that moves with no trade-off |

`wrong` counts verified moves of sites that are not movable (hard limit, non-production). `one_approval_away` = moved +
surfaced: what ships once a human answers the surfaced questions. A decision covers a site when it names the site (within 3
lines of the mapped line, or inside a reported range: a call spans lines), names the client construction the call uses (the
scanner's call-to-client link; for a call the scanner can't see, a client in the same class or the only client in the file),
or, for keeps and needs-decision only, sits on no site in the same function, or is a group decision on no site in a file of 3+
sites (a provider table). A move is only ever claimed for a named site or client.

**Destructive edits** per run, from its diff.patch: provider-key references removed and not re-added anywhere in the diff
(`*_API_KEY` names, `api_key` / `apiKey` config fields; distinct names), deleted files, and deleted classes/functions (not
re-defined anywhere in the diff).

**$** is the agent transcript's cost (result total_cost_usd, else per-message usage at list prices). **Minutes** is the
harness's wall clock for the trial (agent.json, written by every arm), else result.json, else the ledger's agent minutes.
**Broken** = a tests/typecheck regression against the baseline, or the independent check (import smoke + replay), at trial
time.

**Runs counted.** Invalid runs (an infrastructure failure, or an agent with a decision channel that decided nothing) and
not-run runs (never started, interrupted before scoring, or no trial tree) are listed under each table and never counted.
"""

CHANGES = """What changed in the scorer (every change applies to every arm):

1. **Line mapping.** Report lines are resolved both ways (as current lines mapped back through the trial's diff hunks, and as
   pristine lines); the reading that lands on a known site wins. Before, every report line was read as a current line, so a
   report mixing conventions (D3 on claude-engineer: `main.py:671` after its import block, `main.py:1227` before it) lost the
   sites named in the other convention. Range entries (`client.py:283-288 (DeepSeek branch)`) now parse, and entries of one
   report no longer override their neighbours (only a later channel overrides an earlier one: claims over the floor plan).
2. **Route check on everything the code sends through the runtime.** Before, only calls the measure-only planner marked
   `migrated` were route-checked, so a wrapped client whose model only routes under the tree's `model_map` (claude-engineer's
   dated claude-3-5-sonnet ids; gpt-cli's runtime-chosen Anthropic model) never reached the check. Now every scanner call goes
   through the product's own route check with the policy and the client's wrap options (drop_betas) applied.
3. **Client-level credit.** A decision or a re-verified probe anchored at a client construction covers every call that uses
   that client (gpt-cli `anthropic.py:24` -> the calls at :86 and :109).
4. **Probe evidence is the record made right after the trial** (reverify.json). Re-running probes later is not reproducible:
   a repo's shared env has its editable install re-pointed at whichever trial copy ran last.
5. **Buckets, destructive edits and one_approval_away** are new; `moved` keeps its meaning (verified Router traffic).
6. **Minutes and $** are read the same way for every arm (agent.json first). Before, D3 runs showed no minutes (the scorer read
   result.json before the trial had written it) and one D_r2 run (obsidian-copilot) showed $0 and no minutes.
7. **Coverage.** Runs that never ran, were interrupted before scoring, or have no trial tree are `not_run` and listed.
"""

NOTES = """## Notes and limits

- **k = 1.** One trial per arm per repo. D_r1 and D_r2 are two replicates of the same arm (D_r1 ran 3 repos). Agent runs vary
  from run to run; differences of a few sites between arms are within that noise. Read the per-repo cells, not just totals.
- **The held-out set is partial.** Only the repos listed were attempted; several runs were interrupted (the runner was killed)
  and are `not_run`; B has one scored held-out repo. Arms are compared on different repo subsets there: compare per repo, with
  each arm's own denominator, not totals.
- **The ground truth is mechanical and generous.** Another vendor's endpoint (Gemini, DeepSeek, Cohere, OpenRouter, Groq, ...)
  counts as movable with a trade-off, so no arm reaches the total; a reasoned keep of such a site is `correctly_kept`, not a
  miss. BYOK plugins (obsidian-copilot, obsidian-textgenerator-plugin, page-assist) count as movable where the app also holds
  an operator key; moving an end user's own key is a product decision, so most arms surface or keep those.
- **Static check limits.** The route check sends one representative request per call (literal kwargs, the literal model else a
  catalog default); non-literal kwargs are invisible to it. Calls through dynamically built clients the scanner can't see are
  `moved_unverified` unless a probe re-verified them.
- **Report lines are the arm's.** When an arm's report names lines that match neither the current nor the pristine file (an
  intermediate state), the nearest site wins; each per-site row in the SITES_MOVED files says which decision covered it and
  how (`direct`, `client`, `function`, `group`), so any attribution can be checked by hand.
- **Destructive edits** count removals in the diff, README/config examples included; they flag a run for review, they are not
  proof of breakage (broken repos are measured separately).
"""


def results_md(path=None):
    """eval/RESULTS.md from both sets' report JSON (run `RM_EVAL_SET=v3 ... report` and `RM_EVAL_SET=heldout ... report` first)."""
    data = {}
    for label, runs_rel, js, md in SETS_FOR_RESULTS:
        p = os.path.join(R.ROOT, js)
        if os.path.exists(p):
            data[label] = (json.load(open(p)), runs_rel, md)
    L = ["# Router migration eval: results (scorer v5)", "",
         "Generated by `eval/v3/score_moved.py results` from the saved runs (rescored with `RM_EVAL_SET=v3|heldout "
         "eval/v3/score_moved.py score`); no agent was re-run. Per-site rows: %s." % ", ".join("`%s`" % v[2] for v in data.values()), "",
         METHOD]
    for label, (d, runs_rel, md) in data.items():
        L += ["## %s set: %d repo%s (%s)" % (label.capitalize(), len(d["repos"]), "s" if len(d["repos"]) != 1 else "",
                                            "eval/v3/REPOS.json" if label == "dev" else "eval/heldout, only the repos any arm ran"), "",
              "Movable sites in these repos: **%d**. Each arm's denominator is the movable sites of the repos it was scored on." % d["movable_total"], ""]
        L += _arm_table(d)
        gaps = [(a, n, x) for a in sorted(d["status"], key=_arm_key) for n, x in d["status"][a].items() if x["status"] != "scored"]
        if gaps:
            L += ["", "Not counted: " + "; ".join("%s %s (%s: %s)" % (a, n, x["status"], x["why"]) for a, n, x in gaps) + "."]
        L += ["", "### Per repo (%s)" % label, "",
              "Cell: moved verified / movable; u unverified, s surfaced, k correctly kept, n claimed but not routing, m missed, w wrong, "
              "d destructive edits, b broken repo; `-` not run, `inv` invalid.", ""] + _repo_table(d)
        de = [(a, n, r) for a in sorted(d["per_repo"], key=_arm_key) for n, r in sorted(d["per_repo"][a].items()) if r.get("destructive")]
        if de:
            L += ["", "Destructive edits (%s): " % label + "; ".join(
                "%s %s: %s" % (a, n, _md("; ".join((r.get("destructive_examples") or [])[:3]))) for a, n, r in de) + "."]
        L += [""]
    L += ["## Scorer change: pre-v5 -> v5 (same saved runs)", "",
          "Before = the pre-v5 scorer's sites_moved.json files at commit %s; after = v5. Same runs, same ground truth." % PRE_V5, ""]
    for label, (d, runs_rel, md) in data.items():
        old = before_v5(runs_rel)
        L += ["| %s arm | runs before / after | moved before | moved after | wrong before / after | $ before / after | minutes before / after |" % label,
              "|---|---|---|---|---|---|---|"]
        for a in sorted(d["arms"], key=_arm_key):
            o, t = old.get(a), d["arms"][a]
            L.append("| %s | %s / %d | %s | %d / %d | %s / %d | %s / %.2f | %s / %.0f |" % (
                a, o["runs"] if o else "-", t["runs"], ("%d / %d" % (o["moved"], o["movable"])) if o else "-", t["moved_verified"], t["movable"],
                o["wrong"] if o else "-", t["wrong"], ("%.2f" % o["usd"]) if o else "-", t["usd"], ("%.0f" % o["minutes"]) if o else "-", t["minutes"]))
        L += [""]
    L += [CHANGES, NOTES]
    path = path or os.path.join(R.ROOT, "eval", "RESULTS.md")
    open(path, "w").write("\n".join(L) + "\n")
    print("wrote %s" % path)
    return path


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("classify")
    s = sp.add_parser("score")
    s.add_argument("--arms")
    s.add_argument("--only")
    s.add_argument("--jobs", type=int, default=4)
    o = sp.add_parser("one")
    o.add_argument("arm")
    o.add_argument("name")
    sp.add_parser("report")
    sp.add_parser("results", help="eval/RESULTS.md from both sets' report JSON")
    a = ap.parse_args()
    if a.cmd == "classify":
        cmd_classify(a)
    elif a.cmd == "score":
        cmd_score(a)
    elif a.cmd == "one":
        print(json.dumps({k: v for k, v in score_one(a.arm, a.name).items() if k != "site_buckets"}, indent=1, default=str)[:6000])
    elif a.cmd == "report":
        report()
    elif a.cmd == "results":
        results_md()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
