"""The agent's tools (v7): the commands an agent may run while it migrates and reviews, plus the ledger and git helpers
the harness (harness.py) shares with them. None of them is required; hand edits are as good.

  check --run CMD [FILE:LINE]   run any command against router-sim (cmd_probe): every request it sends, valid or not,
                                and which calls a valid request confirms (its feature or its client names the call)
  wrap   FILE:LINE              the deterministic wrap at a client construction (--native: base_url + key, no runtime,
                                when provably safe; FILE:1: a file's LiteLLM/openai-module install hook)
  unwrap FILE:LINE              its inverse
  adapt  FILE:LINE --LEVER --loses "..."   a trade-off (drop a parameter, map a model, a client option), compiled into
                                router.policy.json; --undo T<n> reverses it

`check --all | FILE:LINE | --final`, `levers`, `policy` and the start/finish/apply flow live in harness.py; `dispatch`
routes every command. Everything is recorded in .router-migrate/ledger.json. Every error says what to run next.
"""
import ast
import json
import os
import re
import subprocess
import tempfile
import time

from . import plan as planmod
from . import scan_py
from .catalog import Catalog

STATE = ".router-migrate"
PROVIDER_KEY_ENV = re.compile(r"\b(OPENAI_API_KEY|ANTHROPIC_API_KEY|AZURE_OPENAI_API_KEY|OPENAI_ORG_ID|OPENAI_ORGANIZATION)\b")
NATIVE_SURFACES = ("responses", "messages", "messages.stream", "messages.count_tokens")
PROBE_TIMEOUT = 300
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock", "bun.lockb")
MAX_LINES = 200


# ------------------------------------------------------------------------------------------------ io helpers
def sp(root, name):
    return os.path.join(root, STATE, name)


def ledger_root(path):
    """The migration root for a command run anywhere inside it: the nearest directory up from `path` holding the ledger
    (an agent that `cd`s into a subdirectory still reaches it), else `path` itself."""
    d = os.path.abspath(path)
    while not os.path.exists(sp(d, "ledger.json")):
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(path)
        d = up
    return d


def load_ledger(root):
    p = sp(root, "ledger.json")
    return json.load(open(p)) if os.path.exists(p) else None


def save_ledger(root, led):
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    tmp = sp(root, "ledger.json.%d.tmp" % os.getpid())  # one temp file per writer, so concurrent writes never interleave
    with open(tmp, "w") as f:
        json.dump(led, f, indent=1, sort_keys=True, default=str)
    os.replace(tmp, sp(root, "ledger.json"))


class ledger_lock:
    """Hold while reading, changing and saving the ledger: checks the agent runs in parallel then can't drop each
    other's records."""
    def __init__(self, root):
        os.makedirs(os.path.join(root, STATE), exist_ok=True)
        self.path = sp(root, "ledger.lock")

    def __enter__(self):
        import fcntl
        self.f = open(self.path, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def out(obj, as_json, text):
    """Concise text by default (capped), JSON with --json. Returns obj's exit code."""
    if as_json:
        print(json.dumps(obj, indent=1, default=str))
    else:
        lines = text.rstrip("\n").splitlines()
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES] + ["... %d more lines (full detail: --json, or .router-migrate/ledger.json)" % (len(lines) - MAX_LINES)]
        print("\n".join(lines))
    return 0 if obj.get("ok", True) else 1


def err(as_json, msg, nxt, code=1):
    obj = {"ok": False, "error": msg, "next": nxt}
    if as_json:
        print(json.dumps(obj, indent=1))
    else:
        print("error: %s\nnext: %s" % (msg, nxt))
    return code


def parse_loc(loc):
    m = re.match(r"^(.+?):(\d+)$", loc or "")
    return (m.group(1).lstrip("./"), int(m.group(2))) if m else (None, None)


def _git(root, *args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["git"] + list(args), cwd=root, capture_output=True, text=True, env=e)


# ------------------------------------------------------------------------------------------------ floor snapshot (git trees)
def snapshot_tree(root):
    """Tree id of the working tree as it is now (tracked + untracked, .gitignore respected, state dir excluded), built in a
    private index so the user's index and history are untouched."""
    # one index per call: the live dashboard snapshots (bundle refresh) while Apply runs the gate, and a shared index let one
    # call rebuild it between the other's `add` and its state-dir removal (the state dir then showed up as out-of-scope edits)
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    fd, idx = tempfile.mkstemp(prefix="snap.", suffix=".index", dir=os.path.join(root, STATE))
    os.close(fd)
    os.remove(idx)  # git wants to create it
    env = {"GIT_INDEX_FILE": idx}
    try:
        # naming an ignored path in a pathspec (even as an exclusion) makes `git add` fail, e.g. repos whose .gitignore is `*`
        # plus an allowlist, or that ignore the state dir: then `add -A` skips it anyway and no exclusion is needed
        ignored = _git(root, "check-ignore", "-q", STATE).returncode == 0
        # start from HEAD so files that are tracked but also match .gitignore (e.g. .env.example) aren't read as deleted
        _git(root, "read-tree", "HEAD", env=env)
        r = _git(root, "add", "-A", "--", ".", *([] if ignored else [":(exclude)%s" % STATE]), env=env)
        if r.returncode:
            raise RuntimeError("git add failed: %s" % r.stderr.strip()[-300:])
        _git(root, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", STATE, env=env)
        t = _git(root, "write-tree", env=env)
        if t.returncode:
            raise RuntimeError("git write-tree failed: %s" % t.stderr.strip()[-300:])
        return t.stdout.strip()
    finally:
        for f in (idx, idx + ".lock"):
            if os.path.exists(f):
                os.remove(f)


def changed_since(root, tree):
    """[(status, path)] between a snapshot tree and the working tree now."""
    now = snapshot_tree(root)
    r = _git(root, "diff", "--name-status", "--no-renames", tree, now)
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def diff_since(root, tree, paths=None):
    now = snapshot_tree(root)
    r = _git(root, "diff", "--no-color", "--no-renames", tree, now, "--", *(paths or []))
    return r.stdout


# ------------------------------------------------------------------------------------------------ facts (tool-only)
def _scan(root):
    from . import cli
    res = scan_py.scan(root)
    sites = list(res["sites"])
    if cli.node_ok():
        try:
            sites += cli.run_ts(["scan", root]).get("sites", [])
        except Exception:
            pass
    return {"sites": sites, "root": root}  # root lets the planner read the repo's model literals, exactly as plan.json does


def current_plan(root):
    """The planner's verdict on the code as it stands (measure_only: router-migrate wraps count only where present)."""
    sc = _scan(root)
    p = planmod.build(sc, Catalog.load(), measure_only=True)
    p["clients"] = {s["id"]: s for s in sc["sites"] if s["kind"] == "client"}
    return p


def trace(s, clients):
    """Three-valued: routes | wont_route | routes_if (routing depends on a runtime value; the condition is stated)."""
    if s["outcome"] != "migrated":
        return "wont_route", None
    if "migrated when the configured client is" in s["reason"]:
        return "routes_if", "the configured client is the OpenAI/Anthropic one (%s)" % s["reason"].split(";")[0].replace("migrated when the configured client is ", "")
    c = clients.get(s.get("via_client") or s.get("client_id") or "") or {}
    bu = c.get("base_url") or s.get("base_url")
    if bu and not (bu.get("static") and bu.get("value")):
        return "routes_if", "base URL `%s` is unset or the vendor's endpoint in production" % (bu.get("value") or "from config")
    return "routes", None


def site_at(sites, rel, line, slack=3):
    """Closest site to file:line (exact first), or None."""
    cands = [s for s in sites if s["file"] == rel and abs(int(s["line"]) - line) <= slack]
    return sorted(cands, key=lambda s: (abs(int(s["line"]) - line), s["kind"] != "call"))[0] if cands else None


def facts_for(root, rel, line, p=None):
    p = p or current_plan(root)
    s = site_at(p["sites"], rel, line)
    if s is None:
        return {"found": False, "file": rel, "line": line, "routes": None, "hard_limit": None,
                "why": "the scanner sees no LLM call at %s:%d (nearest sites in this file: %s)" % (
                    rel, line, ", ".join(str(x["line"]) for x in p["sites"] if x["file"] == rel)[:120] or "none")}
    verdict, cond = trace(s, p.get("clients") or {})
    return {"found": True, "site": s["id"], "file": s["file"], "line": s["line"], "kind": s["kind"], "sdk": s["sdk"], "surface": s["surface"],
            "model": s.get("model"), "base_url": s.get("base_url"), "outcome": s["outcome"], "reason": s["reason"], "stream": s.get("stream"),
            "routes": s["outcome"] == "migrated", "verdict": verdict, "condition": cond, "hard_limit": hard_limit(s),
            "client": s.get("via_client") or s.get("client_id")}


# ------------------------------------------------------------------------------------------------ commands
def hard_limit(s):
    """'no_endpoint' | 'stateful' | None: what Router physically cannot serve for this site, whatever the code does."""
    surf = str(s.get("surface") or "")
    if surf in planmod.NO_ENDPOINT or surf.endswith("embeddings") or surf in ("images", "audio", "files", "assistants", "vector_stores",
                                                                             "completions.legacy", "models"):
        return "no_endpoint"
    if planmod._site_stateful(s) or s.get("uses_provider_files") or (s.get("static_kwargs") or {}).get("store") is True:
        return "stateful"
    return None


def feature_match(site_feat, req_feat):
    """Does a request's metadata.feature (the runtime's '<file relative to the process cwd>:<function>') name this site's
    feature ('<repo-relative file>:<enclosing function>')? Same function (or a lambda/comprehension inside it) and the same file,
    allowing for a process started in a subdirectory."""
    if not site_feat or not req_feat:
        return False
    sf, _, sfn = site_feat.rpartition(":")
    rf, _, rfn = str(req_feat).rpartition(":")
    if sfn != rfn and not rfn.startswith("<"):
        return False
    return sf == rf or sf.endswith("/" + rf) or rf.endswith("/" + sf) or (os.path.basename(sf) == rf)


def native_eligibility(root, p, client_id):
    """(eligible, why) for pointing a client straight at Router (base_url + key, no runtime): every site it serves must use a
    surface Router serves natively, with an exact catalog model, no beta flags, no provider state, an operator key. The proof
    is still dynamic: each native site needs a valid probe or test-run request (there is no runtime fallback)."""
    from .catalog import Catalog as _C
    c = (p.get("clients") or {}).get(client_id)
    if not c:
        return False, "no client construction found for this site"
    if c["lang"] != "py" or c["sdk"] not in ("openai", "anthropic") or c["surface"] not in ("OpenAI", "AsyncOpenAI", "Anthropic", "AsyncAnthropic"):
        return False, "the native codemod covers Python OpenAI/Anthropic SDK constructors; elsewhere edit by hand and prove it with `check --run`"
    if (c.get("api_key") or {}).get("per_tenant"):
        return False, "the key looks per-tenant (%s)" % (c["api_key"].get("why") or "")
    sites = [s for s in p["sites"] if (s.get("via_client") or s.get("client_id")) == client_id and s.get("prod")]
    if not sites:
        return False, "no call site is linked to this client, so what it sends can't be checked"
    cat = _C.load()
    for s in sites:
        where = "%s:%s" % (s["file"], s["line"])
        if s["surface"] not in NATIVE_SURFACES:
            return False, "%s uses %s, which Router does not serve natively (the runtime translates it; keep the runtime)" % (where, s["surface"])
        if hard_limit(s) or s.get("has_beta") or s.get("beta_split"):
            return False, "%s needs provider state or beta flags" % where
        m = s.get("model") or {}
        if not (m.get("static") and m.get("value") and cat.resolve(m["value"])[0] == m["value"]):
            return False, "%s: model %r is not a static exact catalog id (dated, mapped or chosen at runtime)" % (where, m.get("value"))
    return True, "%d site(s), all on Router-native surfaces with exact catalog models; prove each with `check --run` (no runtime fallback)" % len(sites)


def glob_for(feature):
    """Policy glob for a feature, robust to the process's working directory: '**<last two path parts>:<function>'."""
    path, _, fn = feature.rpartition(":")
    tail = "/".join(path.split("/")[-2:])
    if not tail.endswith(".py"):  # TS/JS runs as bundled .js (.next/server/.../route.js): match any extension
        tail = re.sub(r"\.(m|c)?(t|j)sx?$", ".*", tail)
    return "**%s:%s" % (tail, fn)


def probe_env(root, led, base):
    """Environment for a probe: the runtime on, pointed at router-sim; provider keys replaced by dummies. router-sim is also
    every client's HTTP(S) proxy: the runtime sends Router traffic through the client's own proxy mounts (by design), which
    the sim serves, and it refuses every other destination (a provider's HTTPS CONNECT included), so a probe stays offline and
    can't spend money. Clients that ignore proxy settings (raw sockets, aiohttp without trust_env) still meet a dummy key. The
    repo's interpreter (from the test command) comes first on PATH so `python` is the one with the repo's dependencies."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("RAMP_ROUTER")}
    from .verify import SIM_KEY
    e.update({"RAMP_ROUTER_API_KEY": SIM_KEY, "RAMP_ROUTER_BASE_URL": base, "OPENAI_API_KEY": "sk-probe-not-a-real-key",
              "ANTHROPIC_API_KEY": "sk-ant-probe-not-a-real-key", "ROUTER_MIGRATE_FEATURE_TAG": "on", "ROUTER_MIGRATE_DISABLE": "",
              "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "NODE_USE_ENV_PROXY": "1"})
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        e[k] = base
    py = (led.get("baseline") or {}).get("python")
    if py and os.path.exists(py):
        e["PATH"] = os.path.dirname(py) + os.pathsep + e.get("PATH", "")
    return e


def test_env_prefix(led):
    """`export K=V ...;` that the repo's test command starts with (app config the code needs to import), or ''."""
    cmd = ((led.get("baseline") or {}).get("test_cmd") or "").strip()
    return cmd.split(";", 1)[0] + "; " if cmd.startswith("export ") and ";" in cmd else ""


def confirm_from_records(root, led, records, source, locs=(), p=None):
    """Record a dynamic confirmation for every site (claimed, proposed, a hotspot, or named) that sent a valid request to
    router-sim: a request tagged with its feature (through the runtime), or, for a site on a native client (no runtime, so no
    tag), an untagged request on its surface with its exact static model."""
    # A site's file:function can show up as the calling frame (feature) or as where its Router client was built (client):
    # framework calls (LlamaIndex, LangChain) start in library code, so for them only the client names the site.
    feats = [r.get(k) for r in records if r.get("valid") for k in ("feature", "client") if r.get(k)]
    untagged = [r for r in records if r.get("valid") and not r.get("feature")]
    seen = {(c["file"], int(c["line"])) for c in (led.get("claims") or {}).values()} | \
        {(x["file"], int(x["line"])) for x in (led.get("worklist") or []) + (led.get("proposals") or [])} | {(f, int(l)) for f, l in locs}
    hits = []
    for f, ln in sorted(seen):
        key = "%s:%d" % (f, ln)
        sf = _feature(root, f, ln)
        if any(feature_match(sf, rf) for rf in feats):
            led.setdefault("confirmations", {})[key] = source
            hits.append(key)
            continue
        if untagged and p is not None:
            s = site_at(p["sites"], f, ln)
            c = (p.get("clients") or {}).get((s or {}).get("via_client") or (s or {}).get("client_id") or "")
            m = (s or {}).get("model") or {}
            if s and c and planmod.client_target(c) == "router" and m.get("static"):
                path = "/v1/messages" if str(s["surface"]).startswith("messages") else "/v1/responses"
                if any(r.get("path", "").startswith(path) and r.get("model") == m.get("value") for r in untagged):
                    led.setdefault("confirmations", {})[key] = source + " (native)"
                    hits.append(key)
    return hits


def dynamic_confirmed(root, led, file, line, extra_features=()):
    key = "%s:%d" % (file, int(line))
    conf = led.get("confirmations") or {}
    if key in conf:
        return conf[key]
    near = [k for k in conf if k.rsplit(":", 1)[0] == file and abs(int(k.rsplit(":", 1)[1]) - int(line)) <= 3]
    if near:
        return conf[near[0]]
    sf = _feature(root, file, line)
    if any(feature_match(sf, rf) for rf in extra_features):
        return "router-on test run"
    return None


def _event(root, stage, status, counts=None, msg=""):
    """One line in .router-migrate/events.jsonl (the run's timeline)."""
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    with open(sp(root, "events.jsonl"), "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "status": status, "counts": counts or {},
                            "cost_usd": 0.0, "msg": msg}) + "\n")


def _resolve_evidence(root, items):
    ev = []
    for e in items:
        for item in e.split(","):
            item = item.strip()
            if not item:
                continue
            er, el = parse_loc(item)
            if not er or not os.path.exists(os.path.join(root, er)):
                return "evidence %r does not resolve to an existing FILE:LINE" % item
            n = sum(1 for _ in open(os.path.join(root, er), encoding="utf-8", errors="replace"))
            if el < 1 or el > n:
                return "evidence %s: line %d is outside the file (%d lines)" % (er, el, n)
            ev.append("%s:%d" % (er, el))
    return ev


def _locs(spec):
    out = []
    for part in (spec or "").split(","):
        rel, line = parse_loc(part.strip())
        if not rel:
            return None
        out.append((rel, line))
    return out


def _evidence(root, led, items):
    """FILE:LINE lines the agent read, or probe ids (PR3) it ran. Returns a list or an error string."""
    ev, lines = [], []
    for e in items or []:
        for item in e.split(","):
            item = item.strip()
            if re.match(r"^PR\d+$", item):
                if item not in (led.get("probes") or {}):
                    return "probe %s does not exist (`router-migrate check --run CMD` records one)" % item
                ev.append(item)
            elif item:
                lines.append(item)
    r = _resolve_evidence(root, lines)
    return r if isinstance(r, str) else ev + r


def ts_unwrap(root, rel, line, window=6):
    """Undo the TS codemod near a line: `...routerOptions(x)` spreads and `fetch: routerFetch(x)` (restored to `fetch: x`);
    the runtime import goes when nothing uses it any more. Returns what was undone."""
    path = os.path.join(root, rel)
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    lo, hi = max(0, line - 3), min(len(lines), line - 1 + window)
    seg = "\n".join(lines[lo:hi])
    pat = re.compile(r"(,\s*)?(\.\.\.\s*routerOptions|fetch\s*:\s*routerFetch)\s*\(")
    out_, i, done = [], 0, []
    while True:
        m = pat.search(seg, i)
        if not m:
            out_.append(seg[i:])
            break
        j, depth = m.end(), 1
        while j < len(seg) and depth:
            depth += {"(": 1, ")": -1}.get(seg[j], 0)
            j += 1
        inner = seg[m.end():j - 1].strip()
        start, end = m.start(), j
        if m.group(2).startswith("fetch") and inner:
            out_.append(seg[i:start] + (m.group(1) or "") + "fetch: " + inner)
        else:
            if not m.group(1):
                t = re.match(r"\s*,\s*", seg[end:])
                end += t.end() if t else 0
            out_.append(seg[i:start])
        i = end
        done.append(m.group(2).split()[-1].lstrip("."))
    if not done:
        return []
    seg = re.sub(r"\(\{\s*\}\)", "()", "".join(out_))
    text = "\n".join(lines[:lo] + [seg] + lines[hi:])
    body = "\n".join(l for l in text.split("\n") if "routerRuntime" not in l)
    if not re.search(r"\brouter(Options|Fetch)\b", body):
        text = "\n".join(l for l in text.split("\n") if not ("routerRuntime" in l and re.search(r"\b(import|require)\b", l)))
    open(path, "w", encoding="utf-8").write(text)
    return done


def cmd_unwrap(a):
    """Undo router-migrate's edit at a client construction (or a file's install hook at FILE:1): the inverse of `wrap`."""
    from . import apply_py
    root = ledger_root(a.path)
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "point at the wrapped constructor (or FILE:1 for an install hook)")
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate start .` first")
    before = open(os.path.join(root, rel), encoding="utf-8").read()
    if rel.endswith(".py"):
        done = apply_py.unwrap_file(root, rel, line, hooks=(line == 1))
    else:
        done = ts_unwrap(root, rel, line)
    after = open(os.path.join(root, rel), encoding="utf-8").read()
    if not done or before == after:
        return err(a.json, "no router-migrate edit at or near %s:%d" % (rel, line), "point at the wrapped construction (`git diff` shows it)")
    import difflib
    d = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/" + rel, "b/" + rel))
    led = load_ledger(root)
    led["edits"].append({"by": "agent-unwrap", "file": rel, "line": line, "changed": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _remap_lines(led, rel, before, after)
    save_ledger(root, led)
    return out({"ok": True, "file": rel, "undone": done, "diff": d}, a.json, d + "next: the calls this client serves no longer route (`check` to confirm)")


def cmd_probe(a):
    """Run any command with the runtime pointed at router-sim (provider keys dummied, outbound HTTP blocked) and record which
    features sent valid Router requests. A valid request tagged with a site's feature confirms that site dynamically."""
    from .sim.server import serve
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate start .` first")
    locs = _locs(a.loc) if a.loc else []
    if locs is None:
        return err(a.json, "expected FILE:LINE[,FILE:LINE...], got %r" % a.loc, "e.g. `router-migrate check app/llm.py:42 --run \"...\"`")
    srv, sim, base = serve()
    trace = tempfile.NamedTemporaryFile(prefix="rm-trace-", suffix=".jsonl", delete=False).name
    try:
        env = probe_env(root, led, base)
        env["ROUTER_MIGRATE_TRACE"] = trace
        pin = "export RAMP_ROUTER_API_KEY=%s RAMP_ROUTER_BASE_URL=%s OPENAI_API_KEY=%s ANTHROPIC_API_KEY=%s; " % (
            env["RAMP_ROUTER_API_KEY"], env["RAMP_ROUTER_BASE_URL"], env["OPENAI_API_KEY"], env["ANTHROPIC_API_KEY"])
        t = time.time()
        try:
            r = subprocess.run(test_env_prefix(led) + pin + a.probe_cmd, shell=True, cwd=root, env=env, capture_output=True, text=True,
                               timeout=a.timeout)
            o, code = (r.stdout or "") + (r.stderr or ""), r.returncode
        except subprocess.TimeoutExpired:
            o, code = "TIMEOUT after %ss" % a.timeout, 124
        recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
        blocked = {}
        for x in sim.records:
            if x.get("blocked"):
                blocked[x["path"]] = blocked.get(x["path"], 0) + 1
    finally:
        srv.shutdown()
    kept = {}
    try:
        for l in open(trace):
            x = json.loads(l)
            if x.get("to") == "provider":
                k = (x.get("feature"), x.get("client"), x.get("model"), x.get("reason"))
                kept[k] = kept.get(k, 0) + 1
        os.unlink(trace)
    except (OSError, ValueError):
        pass
    kept = [{"feature": k[0], "client": k[1], "model": k[2], "reason": k[3], "n": n} for k, n in kept.items()]
    led = load_ledger(root)
    pid = "PR%d" % (len(led.get("probes") or {}) + 1)
    reqs = [{"path": x.get("path"), "feature": x.get("feature"), "client": x.get("client"), "model": x.get("model"), "valid": x.get("valid"),
             "error": x.get("error")} for x in recs]
    led.setdefault("probes", {})[pid] = {"cmd": a.probe_cmd, "exit": code, "seconds": round(time.time() - t, 1), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "requests": reqs[:50], "blocked": blocked, "kept": kept[:20], "tail": o[-1500:]}
    hits = confirm_from_records(root, led, recs, pid, locs=locs, p=current_plan(root))
    save_ledger(root, led)
    lines = ["%s: exit %s, %d request(s) reached router-sim (%d valid, %d invalid)" % (pid, code, len(reqs), sum(1 for x in reqs if x["valid"]),
                                                                                    sum(1 for x in reqs if not x["valid"]))]
    for x in reqs[:12]:
        lines.append("  %s %s feature=%s client=%s model=%s%s" % ("valid  " if x["valid"] else "INVALID", x["path"], x["feature"], x["client"], x["model"],
                                                                 "" if x["valid"] else " error=%s" % str(x["error"])[:160]))
    for x in kept[:8]:
        lines.append("  provider (runtime kept it): feature=%s client=%s model=%s x%d -- %s" % (x["feature"], x["client"], x["model"], x["n"], x["reason"]))
    if blocked:
        lines.append("blocked (not Router; nothing leaves this machine): %s -- traffic that stays on the provider by design, or a "
                     "client that isn't routed; stub it if the run needs it" % ", ".join("%s x%d" % kv for kv in sorted(blocked.items())))
    for rel, line in locs:
        k = "%s:%d" % (rel, line)
        conf = dynamic_confirmed(root, led, rel, line)
        lines.append("%s: %s" % (k, ("confirmed by %s" % conf) if conf else
                                 "NOT confirmed: no valid request from %s (as the calling function or where its client was built): the "
                                 "command didn't run this call with Router on, or the call doesn't route" % _feature(root, rel, line)))
    if hits:
        lines.append("dynamically confirmed: %s" % ", ".join(hits[:20]))
    lines.append("output tail:\n" + o[-800:])
    return out({"ok": True, "probe": pid, "requests": reqs, "confirmed": hits, "exit": code}, a.json, "\n".join(lines))


def cmd_wrap(a):
    from . import apply_py, cli
    from .runtime import render
    root = ledger_root(a.path)
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "point at the line where the client is constructed, e.g. `OpenAI(`")
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate start .` first")
    before = open(os.path.join(root, rel), encoding="utf-8").read()
    sites = _scan(root)["sites"]
    c = [s for s in sites if s["file"] == rel and s["kind"] in ("client", "module_client") and abs(int(s["line"]) - line) <= 2]
    c = sorted(c, key=lambda s: abs(int(s["line"]) - line))
    cat = Catalog.load()
    native = bool(getattr(a, "native", False))
    if rel.endswith(".py"):
        opts = {"responses": False} if a.stateful else None
        installs = set()
        mods = [x for x in sites if x["file"] == rel and x["kind"] == "module_client" and x["sdk"] in ("litellm", "openai")]
        if not c and mods and not native:
            c = mods[:1]
        if native:
            if not c or c[0]["kind"] != "client":
                return err(a.json, "no OpenAI/Anthropic client construction at %s:%d" % (rel, line), "point at the constructor line")
            ok, why = native_eligibility(root, dict(current_plan(root)), c[0]["id"])
            if not ok:
                return err(a.json, "not provably safe to go native: %s" % why, "keep the runtime (`router-migrate wrap %s:%d`)" % (rel, line))
            targets = {(int(c[0]["line"]), None): ("native_anthropic" if c[0]["sdk"] == "anthropic" else "native_openai", None)}
        elif c and c[0]["kind"] == "module_client":
            installs = {"install_litellm" if c[0]["sdk"] == "litellm" else "install_openai_module"}
            targets = {}
        elif c:
            kind = planmod.PATCHABLE.get(c[0]["sdk"])
            if not kind or kind == "ts":
                return err(a.json, "%s (%s) has no deterministic wrap" % (c[0]["surface"], c[0]["sdk"]),
                           "edit by hand to pass the runtime's client/fetch in (e.g. http_client=router_runtime.http_client()), then prove it with `check --run`")
            targets = {(int(c[0]["line"]), int(c[0]["col"])): (kind, opts)}
        else:
            targets = {(line, None): ("wrap_openai", opts)}
            if re.search(r"\bAsyncAnthropic\(|\bAnthropic\(", before.splitlines()[line - 1] if line <= len(before.splitlines()) else ""):
                targets = {(line, None): ("wrap_anthropic", None)}
        changed, done, skipped, rt_rel = apply_py.patch_file(root, rel, targets, installs)
        if skipped:
            return err(a.json, "; ".join(skipped.values()), "check the line points at the constructor call itself, or edit by hand and `check --run`")
        if not done and not changed:
            return err(a.json, "no OpenAI/Anthropic/LangChain/LlamaIndex client construction found at %s:%d" % (rel, line),
                       "point at the line where the constructor call starts; for anything else edit by hand and prove it with `check --run`")
        if rt_rel and not os.path.exists(os.path.join(root, rt_rel)):
            open(os.path.join(root, rt_rel), "w").write(render("router_runtime.py", cat))
    else:
        if native:
            return err(a.json, "the native codemod is Python-only", "edit by hand (baseURL and apiKey from RAMP_ROUTER_BASE_URL / "
                       "RAMP_ROUTER_API_KEY when the key is set, the originals otherwise), then prove each site with `check --run`")
        if not c:
            return err(a.json, "the scanner sees no client construction at %s:%d" % (rel, line),
                       "point at `new OpenAI(`/`new Anthropic(`/`createOpenAI(`/`createAnthropic(`; elsewhere pass `routerFetch()` by hand and `check --run`")
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
            rf.write(render("routerRuntime.ts", cat))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
            json.dump([c[0]["id"]], sf)
        r = cli.run_ts(["apply", root, "--runtime", rf.name, "--sites", sf.name])
        bad = [e for e in r.get("edits", []) if not e.get("ok")]
        if bad:
            return err(a.json, bad[0].get("reason") or "TS wrap failed", "edit by hand to pass `routerFetch()` in, then prove it with `check --run`")
    after = open(os.path.join(root, rel), encoding="utf-8").read()
    import difflib
    d = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/" + rel, "b/" + rel))
    led = load_ledger(root)
    led["edits"].append({"by": "agent-wrap", "file": rel, "line": line, "native": native, "changed": before != after, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    if before != after:
        _remap_lines(led, rel, before, after)
    save_ledger(root, led)
    msg = (d or "already wrapped: nothing to change\n") + ("next: native clients have no runtime fallback: `check --run` every site it serves"
                                                          if native else "next: `check` the calls this client serves (`check --run CMD` for ones the scanner can't see)")
    return out({"ok": True, "file": rel, "changed": before != after, "diff": d}, a.json, msg)


def _shift(before, after, line):
    """Where `line` of `before` is in `after`: unchanged lines keep their identity, an edited line maps to its replacement."""
    import difflib
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).get_opcodes():
        if i1 < line <= i2:
            return j1 + (line - i1) if tag == "equal" else j1 + 1
    return line


def _remap_lines(led, rel, before, after):
    """Move every ledger line in `rel` (worklist items, claims, facts) through the edit, so ids keep pointing at the same code."""
    import difflib
    old, new = before.splitlines(), after.splitlines()
    m = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                m[i1 + k + 1] = j1 + k + 1
    f = lambda n: m.get(int(n), int(n))
    for it in (led.get("worklist") or []) + (led.get("proposals") or []):
        if it["file"] == rel:
            it["line"] = f(it["line"])
        ed = it.get("edit") or {}
        if ed.get("at", "").startswith(rel + ":"):
            new_at = "%s:%d" % (rel, f(ed["at"].rsplit(":", 1)[1]))
            ed["cmd"] = ed.get("cmd", "").replace(ed["at"], new_at)
            ed["at"] = new_at
    for key in list((led.get("claims") or {}).keys()):
        c = led["claims"][key]
        if c["file"] == rel:
            c["line"] = f(c["line"])
            led["claims"]["%s:%d" % (rel, c["line"])] = led["claims"].pop(key)
    for key in list((led.get("facts") or {}).keys()):
        if key.startswith(rel + ":"):
            led["facts"]["%s:%d" % (rel, f(key.rsplit(":", 1)[1]))] = led["facts"].pop(key)
    for t in led.get("tradeoffs") or []:
        if t["file"] == rel:
            t["line"] = f(t["line"])
            t["site"] = "%s:%d" % (rel, t["line"])
        cl = (t.get("levers") or {}).get("client") or ""
        if cl.startswith(rel + ":"):
            t["levers"]["client"] = "%s:%d" % (rel, f(cl.rsplit(":", 1)[1]))


def enclosing_function(root, site):
    """Name of the innermost function containing the site's line ('<module>' at top level), as the runtime reports it."""
    path = os.path.join(root, site["file"])
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return "<module>"
    line = int(site["line"])
    if site["file"].endswith(".py"):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return "<module>"
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                if node.lineno <= line <= end and (best is None or node.lineno > best.lineno):
                    best = node
        return best.name if best else "<module>"
    lines = src.splitlines()[:line]
    pat = re.compile(r"(?:function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|^\s*(?:public |private |protected |static |async )*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{)")
    for l in reversed(lines):
        m = pat.search(l)
        if m:
            name = next(g for g in m.groups() if g)
            if name not in ("if", "for", "while", "switch", "catch", "return"):
                return name
    return "<module>"


def _feature(root, rel, line):
    """file:function the runtime would tag requests from this line with."""
    return "%s:%s" % (rel, enclosing_function(root, {"file": rel, "line": line}))


def run_cmd(root, cmd, env=None, timeout=1800):
    from .verify import parse_counts
    t = time.time()
    e = dict(os.environ)
    e.pop("RAMP_ROUTER_API_KEY", None)
    e.update(env or {})
    try:
        r = subprocess.run(cmd, cwd=root, shell=True, env=e, capture_output=True, text=True, timeout=timeout)
        o, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        o, code = "TIMEOUT", 124
    res = {"exit": code, "seconds": round(time.time() - t, 1), "ts_errors": len(re.findall(r"error TS\d+", o)), "tail": o[-1500:]}
    res.update(parse_counts(o))
    return res


def _tc(t):
    if not t or t.get("skipped"):
        return (t or {}).get("skipped") or "-"
    return "%s passed, %s failed, %s errors (exit %s)" % (t.get("passed"), t.get("failed"), t.get("errors"), t.get("exit"))


def _last_error(tail):
    """The most telling line of a failed run's output: the last exception or assertion line, else the last line."""
    lines = [l.strip() for l in (tail or "").splitlines() if l.strip()]
    errs = [l for l in lines if re.search(r"(Error|Exception|assert|FAILED|Traceback)", l)]
    return ((errs or lines or ["(no output)"])[-1])[:300]


def _worse(before, after):
    """True when `after` is a regression from `before` (fewer passed, or more failed/errors, or a new non-zero exit)."""
    if not before or before.get("skipped") or before.get("exit") is None:
        return False
    b, x = lambda k: before.get(k) or 0, lambda k: after.get(k) or 0
    if before.get("passed") is not None and (after.get("passed") is None or x("passed") < b("passed")):
        return True
    if x("failed") + x("errors") > b("failed") + b("errors"):
        return True
    return bool(after.get("exit")) and not before.get("exit") and before.get("passed") is None


def provider_key_removals(root, tree):
    """Provider-key references (OPENAI_API_KEY, ANTHROPIC_API_KEY...) the edits removed and did not keep elsewhere: kept traffic
    still needs those keys, so deleting them is never allowed."""
    removed, added = {}, {}
    for l in diff_since(root, tree).splitlines():
        if l.startswith(("---", "+++")):
            continue
        for name in PROVIDER_KEY_ENV.findall(l):
            if l.startswith("-"):
                removed[name] = removed.get(name, 0) + 1
            elif l.startswith("+"):
                added[name] = added.get(name, 0) + 1
    return sorted(n for n, k in removed.items() if k > added.get(n, 0))


POLICY_LEVERS = ("drop_params", "model_map")
CLIENT_LEVERS = ("unlock_hosts", "operator_key_only", "drop_betas")


def compile_tradeoffs(root, led):
    """The policy half of every live trade-off (drop_params, model_map), compiled into router.policy.json and the runtime's
    policy files. Keys set last time (led["tradeoff_policy"]) are re-derived each run, so an undone trade-off disappears;
    keys from approved cards and guardrails are never touched. Returns the files written."""
    from . import policy as pol
    want = {}
    for t in led.get("tradeoffs") or []:
        if t.get("undone"):
            continue
        lv, g = t.get("levers") or {}, glob_for(t["feature"])
        e = want.setdefault(g, {})
        if lv.get("drop_params"):
            e["drop_params"] = sorted(set(e.get("drop_params") or []) | set(lv["drop_params"]))
        if lv.get("model_map"):
            e["model_map"] = dict(e.get("model_map") or {}, **lv["model_map"])
    want = {g: e for g, e in want.items() if e}
    prev = led.get("tradeoff_policy") or {}
    p, perr = pol.load(root)
    if perr or (not want and not prev):
        return []
    p = p or {"version": 1}
    globs = p.setdefault("globs", {})
    for g, keys in prev.items():
        e = globs.get(g)
        for k in keys if e else []:
            e.pop(k, None)
        if e is not None and not e:
            del globs[g]
    for g, e in want.items():
        globs.setdefault(g, {}).update(e)
    if not globs:
        p.pop("globs", None)
    led["tradeoff_policy"] = {g: sorted(e) for g, e in want.items()}
    rts = pol.runtime_paths(root)
    if not rts or not pol.policy_valid(p):
        return []
    open(os.path.join(root, pol.POLICY_FILE), "w").write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    return [pol.POLICY_FILE] + pol.compile_everywhere(root, p, rts)


def _client_of(root, rel, line, p):
    """(client record, construction FILE:LINE) for the site at rel:line, or for a construction at that line."""
    f = facts_for(root, rel, line, p)
    c = (p.get("clients") or {}).get(f.get("client") or "") if f.get("found") else None
    if c is None:  # a wrap's import block moves constructors down: take the nearest one below, or the file's only one
        cs = sorted((x for x in (p.get("clients") or {}).values() if x["file"] == rel), key=lambda x: abs(int(x["line"]) - line))
        near = [x for x in cs if -2 <= int(x["line"]) - line <= 12]
        c = near[0] if near else cs[0] if len(cs) == 1 else None
    return c


def _wrap_with(root, c, opts):
    """(Re-)wrap one Python client construction with these runtime options (adds options to an existing wrap)."""
    from . import apply_py
    from .runtime import render
    kind = planmod.PATCHABLE.get(c["sdk"])
    if c["lang"] != "py":
        return _wrap_ts_with(root, c, opts)
    if kind not in ("wrap_openai", "wrap_anthropic", "wrap_langchain"):
        return None, "client options need a Python OpenAI/Anthropic/LangChain constructor; edit by hand and record it with `adapt --loses`"
    if kind == "wrap_openai" and c["sdk"] == "anthropic":
        kind = "wrap_anthropic"
    changed, done, skipped, rt_rel = apply_py.patch_file(root, c["file"], {(int(c["line"]), None): (kind, opts)}, set())
    if skipped:
        return None, "; ".join(skipped.values())
    if rt_rel and not os.path.exists(os.path.join(root, rt_rel)):
        open(os.path.join(root, rt_rel), "w").write(render("router_runtime.py", Catalog.load()))
    return c["file"], None


def _wrap_ts_with(root, c, opts):
    """(Re-)wrap a TS/JS client with runtime options (routerOptions/routerFetch's second argument): unwrap, then re-apply."""
    from . import cli
    from .runtime import render
    ts = {"unlockHosts": opts.get("unlock_hosts"), "dropBetas": opts.get("drop_betas"), "operatorKeyOnly": opts.get("operator_key_only")}
    ts = {k: v for k, v in ts.items() if v}
    ts_unwrap(root, c["file"], int(c["line"]))
    cur = min((x for x in _scan(root)["sites"] if x["kind"] == "client" and x["file"] == c["file"]),
              key=lambda x: abs(int(x["line"]) - int(c["line"])), default=None)
    if cur is None:
        return None, "the scanner no longer sees the client at %s:%s" % (c["file"], c["line"])
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
        rf.write(render("routerRuntime.ts", Catalog.load()))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
        json.dump([{"id": cur["id"], "opts": ts} if ts else cur["id"]], sf)
    r = cli.run_ts(["apply", root, "--runtime", rf.name, "--sites", sf.name])
    bad = [e for e in r.get("edits", []) if not e.get("ok")]
    if bad:
        return None, bad[0].get("reason") or "TS wrap failed"
    return c["file"], None


def cmd_adapt(a):
    """Take a trade-off so a site can move to Router, and record what it costs. Policy levers (drop params, swap a model)
    leave the code untouched; client levers (unlock another vendor's host, route only the operator's key, drop beta flags)
    re-wrap the client. With no lever it records a hand edit you made. Every trade-off reaches the dashboard with an undo."""
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate start .` first")
    if getattr(a, "undo", None):
        t = next((x for x in led.get("tradeoffs") or [] if x["id"] == a.undo), None)
        if t is None or t.get("undone"):
            return err(a.json, "no live trade-off %s" % a.undo, "live: %s" % (", ".join(x["id"] for x in led.get("tradeoffs") or [] if not x.get("undone")) or "none"))
        files = undo_tradeoff(root, a.undo)
        if not next(x for x in load_ledger(root)["tradeoffs"] if x["id"] == a.undo).get("undone"):
            return err(a.json, "%s was a hand edit" % a.undo, "revert that hunk yourself; the dashboard shows it")
        return out({"ok": True, "undone": a.undo, "files": files}, a.json, "undid %s (%s)" % (a.undo, ", ".join(files) or "policy only"))
    if not a.loc:
        return err(a.json, "expected FILE:LINE (or --undo T<n>)", "the call site (or client construction) the trade-off moves")
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "the call site (or client construction) the trade-off moves")
    if not a.loses:
        return err(a.json, "--loses is required", "say in one line what the application gives up (the human sees it with an undo)")
    p = current_plan(root)
    lv = {}
    if a.drop:
        lv["drop_params"] = sorted({x.strip() for x in a.drop.split(",") if x.strip()})
    if a.model_map:
        mm = {}
        for kv in a.model_map:
            frm, _, to = kv.partition("=")
            if not frm or not to:
                return err(a.json, "--model-map takes OLD=NEW, got %r" % kv, "e.g. --model-map claude-3-5-sonnet-20240620=claude-sonnet-4-5")
            if Catalog.load().resolve(to)[0] != to:
                return err(a.json, "%s is not a Router catalog id" % to, "pick the closest model Router serves (exact catalog id)")
            mm[frm] = to
        lv["model_map"] = mm
    if a.unlock_host:
        lv["unlock_hosts"] = sorted(set(a.unlock_host))
    if a.operator_key_only:
        lv["operator_key_only"] = True
    if a.drop_betas:
        lv["drop_betas"] = sorted({x.strip() for x in a.drop_betas.split(",") if x.strip()})
    files = []
    if any(k in lv for k in CLIENT_LEVERS):
        c = _client_of(root, rel, line, p)
        if c is None:
            return err(a.json, "no client construction found for %s:%d" % (rel, line), "point at the call site or its constructor")
        opts = {k: lv[k] for k in CLIENT_LEVERS if k in lv}
        before = open(os.path.join(root, c["file"]), encoding="utf-8").read()
        f, why = _wrap_with(root, c, opts)
        if f is None:
            return err(a.json, why, "or edit by hand, verify it with `check --run`, and record it with `adapt %s:%d --loses ...`" % (rel, line))
        files.append(f)
        after = open(os.path.join(root, f), encoding="utf-8").read()
        led = load_ledger(root)
        if before != after:
            _remap_lines(led, f, before, after)
            save_ledger(root, led)
            if f == rel:
                line = _shift(before, after, line)
        now = min((x for x in (current_plan(root).get("clients") or {}).values() if x["file"] == f),
                  key=lambda x: abs(int(x["line"]) - _shift(before, after, int(c["line"]))), default=c)
        lv["client"] = "%s:%s" % (f, now["line"])
        if f == rel and _feature(root, rel, line).endswith(":<module>"):  # pointed at a construction the wrap moved
            line = int(now["line"])
    led = load_ledger(root)
    feat = _feature(root, rel, line)
    tid = "T%d" % (len(led.get("tradeoffs") or []) + 1)
    ev = _evidence(root, led, a.evidence) if a.evidence else []
    if isinstance(ev, str):
        return err(a.json, ev, "evidence is FILE:LINE you read or PRn")
    t = {"id": tid, "site": "%s:%d" % (rel, line), "file": rel, "line": line, "feature": feat, "levers": lv, "loses": a.loses,
         "why": a.why or "", "evidence": ev, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "undone": False}
    led.setdefault("tradeoffs", []).append(t)
    files += compile_tradeoffs(root, led)
    if files:
        led["edits"].append({"by": "tradeoff", "id": tid, "files": sorted(set(files)), "at": t["at"]})
    save_ledger(root, led)
    what = ", ".join("%s=%s" % (k, v) for k, v in lv.items() if k != "client") or "hand edit"
    return out({"ok": True, "tradeoff": t, "files": files}, a.json,
               "%s recorded for %s (%s); loses: %s\nnext: `check %s:%d` (or `check --run CMD`) to see it route; `adapt --undo` reverses it"
               % (tid, feat, what, a.loses, rel, line))


def undo_tradeoff(root, tid):
    """Reverse trade-off `tid`: its policy keys leave the compiled policy, its client options leave the wrap (the client
    stays wrapped with whatever other trade-offs still need). A hand edit can't be undone automatically. Returns files changed."""
    from . import apply_py
    led = load_ledger(root)
    t = next((x for x in led.get("tradeoffs") or [] if x["id"] == tid), None)
    lv = t.get("levers") if t else None
    if t is None or t.get("undone") or not any(k in (lv or {}) for k in POLICY_LEVERS + CLIENT_LEVERS):
        return []  # a hand edit can't be undone automatically: the dashboard says to revert that hunk
    t["undone"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    files = []
    if lv.get("client"):
        cf, _, cl = lv["client"].rpartition(":")
        keep = {}
        for x in led.get("tradeoffs") or []:
            if x is not t and not x.get("undone") and (x.get("levers") or {}).get("client") == lv["client"]:
                for k in CLIENT_LEVERS:
                    if k in x["levers"]:
                        keep[k] = sorted(set(keep.get(k, [])) | set(x["levers"][k])) if isinstance(x["levers"][k], list) else x["levers"][k]
        before = open(os.path.join(root, cf), encoding="utf-8").read()
        if not cf.endswith(".py"):  # TS/JS: unwrap and re-apply with only the options other trade-offs still need
            cc = next((x for x in (current_plan(root).get("clients") or {}).values() if x["file"] == cf), None)
            _wrap_ts_with(root, cc or {"file": cf, "line": int(cl), "lang": "ts"}, keep)
            if open(os.path.join(root, cf), encoding="utf-8").read() != before:
                files.append(cf)
            files += compile_tradeoffs(root, led)
            led["edits"].append({"by": "tradeoff-undo", "id": tid, "files": sorted(set(files)), "at": t["undone"]})
            save_ledger(root, led)
            return files
        near = lambda: min((x for x in (current_plan(root).get("clients") or {}).values() if x["file"] == cf),
                           key=lambda x: abs(int(x["line"]) - int(cl)), default=None)  # import blocks move constructors
        cc = near()
        apply_py.unwrap_file(root, cf, int(cc["line"]) if cc else int(cl))
        cc = near()
        if cc:  # stays wrapped (clean traffic still routes), with only the options other trade-offs need
            _wrap_with(root, cc, keep)
        if open(os.path.join(root, cf), encoding="utf-8").read() != before:
            files.append(cf)
    files += compile_tradeoffs(root, led)
    led["edits"].append({"by": "tradeoff-undo", "id": tid, "files": sorted(set(files)), "at": t["undone"]})
    save_ledger(root, led)
    return files



def add_parsers(sub):
    s = sub.add_parser("check", help="does each LLM call route to Router now, and why not (--all: every call the scanner sees; "
                                     "--run CMD: run code against the local Router simulator; --final: dry run of the final check)")
    s.add_argument("loc", nargs="?", help="FILE:LINE[,FILE:LINE...] (relative to the repo root)")
    s.add_argument("--all", action="store_true", help="every LLM call the scanner sees")
    s.add_argument("--run", help="shell command to run with the runtime pointed at router-sim (provider keys dummied, offline)")
    s.add_argument("--final", action="store_true", help="dry run of the final check that runs after the agent (tests skipped unless --tests)")
    s.add_argument("--tests", action="store_true", help="with --final: also run the test suite Router-on against the baseline")
    s.add_argument("--live", action="store_true", help="with --run: the real Router, with the migration key injected into that command only")
    s.add_argument("--timeout", type=int, default=PROBE_TIMEOUT)
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("wrap", help="the deterministic wrap at a client construction (--native: point it straight at Router when provably safe)")
    s.add_argument("loc", help="FILE:LINE of the constructor call (FILE:1 adds a file's LiteLLM/openai-module install hook)")
    s.add_argument("--path", default=".")
    s.add_argument("--stateful", action="store_true", help="keep this client's Responses API traffic on the provider (previous_response_id/store)")
    s.add_argument("--native", action="store_true", help="base_url + key from RAMP_ROUTER_* when the key is set, no runtime (Python; refused unless eligible)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("unwrap", help="undo router-migrate's edit at a construction (FILE:1: a file's install hook)")
    s.add_argument("loc", help="FILE:LINE of the wrapped constructor")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("start", help="clean tree required; branch router-migrate/migration; baseline in the background; prints the "
                                     "migrate + improve instructions for the agent (Claude Code: your session)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--test-cmd")
    s.add_argument("--typecheck-cmd")
    s = sub.add_parser("levers", help="Router facts for improvement work: the lever table, catalog rows and same-family alternatives "
                                      "(live with a key), for a model or the model at a call site")
    s.add_argument("loc", nargs="?", help="MODEL[,MODEL] and/or FILE:LINE[,...] (default: the catalog by vendor)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("dashboard", help="write REPORT.html and open the live dashboard (detached; prints its URL)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--serve", action="store_true", help=argparse_suppress())
    s.add_argument("--no-browser", action="store_true")
    for hidden in ("_baseline", "_validate"):
        s = sub.add_parser(hidden)
        s.add_argument("path")
        s.add_argument("--test-cmd")
        s.add_argument("--typecheck-cmd")
    s = sub.add_parser("policy", help="compile router.policy.json into the policy files the vendored runtimes read (after a hand edit)")
    s.add_argument("path", nargs="?", default=".")
    s = sub.add_parser("request", help="record what you want for an item: apply (an improvement), undo, dismiss, postpone (the "
                                       "dashboard does the same); `router-migrate apply` then hands the list to the agent")
    s.add_argument("item", help="item id from MIGRATION.json (shown on every dashboard card)")
    s.add_argument("action", choices=["apply", "undo", "dismiss", "postpone", "done"])
    s.add_argument("--reason")
    s.add_argument("--until", help="YYYY-MM-DD (postpone)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("adapt", help="optional helper: a policy trade-off (drop a parameter Router rejects, map a model) recorded with what it costs")
    s.add_argument("loc", nargs="?", help="FILE:LINE of the call site (or its client construction)")
    s.add_argument("--undo", metavar="T<n>", help="reverse trade-off T<n> (policy keys and client options; a hand edit you revert yourself)")
    s.add_argument("--drop", help="request parameters to drop on Router-bound requests (comma-separated); max_tokens is raised to 16, name folded into the text")
    s.add_argument("--model-map", action="append", help="OLD=NEW: serve OLD with Router catalog model NEW (repeatable)")
    s.add_argument("--unlock-host", action="append", help="another vendor's OpenAI-compatible host whose traffic goes through Router")
    s.add_argument("--operator-key-only", action="store_true", help="route requests made with the operator's own key; users' keys stay on the provider")
    s.add_argument("--drop-betas", help="Anthropic beta flags to drop (comma-separated)")
    s.add_argument("--loses", help="what the application gives up, one line (required)")
    s.add_argument("--why")
    s.add_argument("--evidence", action="append")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("finish", help="the outside check once, on the migration branch and every improvement branch (parse, "
                                      "typecheck, tests with Router on vs the baseline, existing tests unchanged, route truth; "
                                      "flags for you); then the dashboard. Never reverts anything")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-dashboard", action="store_true", help="write REPORT.html but don't serve the live dashboard")
    s.add_argument("--no-browser", action="store_true", help="serve the dashboard without opening a browser")


def _each(fn, a):
    """Run a per-location command over comma-separated locations."""
    import copy
    rc = 0
    for loc in [x.strip() for x in a.loc.split(",") if x.strip()]:
        b = copy.copy(a)
        b.loc = loc
        rc = max(rc, fn(b) or 0)
    return rc


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS


def dispatch(a):
    """Route a parsed command: the agent's code tools here, everything else to the harness."""
    from . import harness
    tools = {"wrap": cmd_wrap, "unwrap": cmd_unwrap, "adapt": cmd_adapt}
    if a.cmd in tools:
        return _each(tools[a.cmd], a) if "," in (a.loc or "") else tools[a.cmd](a)
    return {"start": harness.cmd_start, "finish": harness.cmd_finish, "run": harness.cmd_run, "apply": harness.cmd_apply,
            "check": harness.cmd_check, "levers": harness.cmd_levers, "policy": harness.cmd_policy,
            "dashboard": harness.cmd_dashboard, "request": harness.cmd_request, "_baseline": harness.cmd_baseline,
            "_validate": harness.cmd_validate}[a.cmd](a)
