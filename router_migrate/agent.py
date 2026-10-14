"""v3: agent-facing primitives over the deterministic core (SPEC_V3.md, Track 1).

The coding agent runs the migration; these commands give it facts and gates it cannot skip. Five commands:

  run    PATH            the deterministic floor (v1 scan -> plan -> apply -> verify), a snapshot of it, and the agent's worklist
  check  FILE:LINE       tool facts for one site in the code as it stands: does the runtime reroute it, and if not, why and what next
  wrap   FILE:LINE       the deterministic one-line wrap at any client construction the agent points at (factories included)
  claim  FILE:LINE OUT   record an outcome with evidence; facts beat judgments (a "migrated" claim the tool contradicts is rejected)
  finish PATH            the gate: syntax, scope, tests and typecheck vs baseline, Router-on run against router-sim, worklist resolved.
                         After --max-attempts failed gate runs it reverts the agent's edits to the floor and stops.

Everything is recorded in .router-migrate/ledger.json. Every error says what to run next.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from . import plan as planmod
from . import scan_py
from .catalog import Catalog

STATE = ".router-migrate"
OUTCOMES = ("migrated", "kept", "flagged", "handoff", "needs_human", "not_a_site")
SRC_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".next", "dist", "build", "out", "coverage", STATE,
             ".tox", ".mypy_cache", ".pytest_cache", "site-packages", ".turbo", ".cache"}
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock", "bun.lockb")
MAX_LINES = 200
RUNTIME_FILES = re.compile(r"(^|/)(router_runtime\.py|routerRuntime\.ts|router_policy\.py|routerPolicy\.ts)$")

# Hotspots: text the scanner did not explain. Over-inclusive on purpose; the agent resolves each one.
LLM_HOSTS = re.compile(r"api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.x\.ai|api\.deepseek\.com|"
                       r"openrouter\.ai/api|api\.groq\.com|api\.mistral\.ai|api\.together\.(xyz|ai)|dashscope|api\.fireworks\.ai|"
                       r"/chat/completions|/v1/messages|/v1/responses")
LLM_IMPORTS = re.compile(r"""^\s*(?:from|import)\s+(litellm|openai|anthropic|google\.genai|google\.generativeai|langchain_\w+|"""
                         r"""llama_index[\w.]*|groq|mistralai|cohere|ollama|instructor|dspy)\b|"""
                         r"""(?:from|require\()\s*['"](openai|@anthropic-ai/sdk|@google/genai|@google/generative-ai|ai|@ai-sdk/[\w-]+|"""
                         r"""@langchain/[\w-]+|langchain[\w/-]*|ollama|groq-sdk|@mistralai/[\w-]+|litellm)['"]""", re.M)
MODEL_LIT = re.compile(r"""['"`]((?:gpt-[\w.-]+|o[134](?:-[\w.-]+)?|claude-[\w.-]+|gemini-[\w.-]+|grok-[\w.-]+|deepseek-[\w.-]+|"""
                       r"""chatgpt-[\w.-]+|text-embedding-[\w.-]+))['"`]""")


# ------------------------------------------------------------------------------------------------ io helpers
def sp(root, name):
    return os.path.join(root, STATE, name)


def load_ledger(root):
    p = sp(root, "ledger.json")
    return json.load(open(p)) if os.path.exists(p) else None


def save_ledger(root, led):
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    tmp = sp(root, "ledger.json.tmp")
    json.dump(led, open(tmp, "w"), indent=1, sort_keys=True, default=str)
    os.replace(tmp, sp(root, "ledger.json"))


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
    idx = sp(root, "snap.index")
    env = {"GIT_INDEX_FILE": idx}
    if os.path.exists(idx):
        os.remove(idx)
    r = _git(root, "add", "-A", "--", ".", ":(exclude)%s" % STATE, env=env)
    if r.returncode:
        raise RuntimeError("git add failed: %s" % r.stderr.strip()[-300:])
    t = _git(root, "write-tree", env=env)
    if t.returncode:
        raise RuntimeError("git write-tree failed: %s" % t.stderr.strip()[-300:])
    return t.stdout.strip()


def changed_since(root, tree):
    """[(status, path)] between a snapshot tree and the working tree now."""
    now = snapshot_tree(root)
    r = _git(root, "diff", "--name-status", "--no-renames", tree, now)
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def diff_since(root, tree, paths=None):
    now = snapshot_tree(root)
    r = _git(root, "diff", "--no-color", "--no-renames", tree, now, "--", *(paths or []))
    return r.stdout


def restore(root, tree, changes):
    """Put every changed path back to its content in `tree` (added files are deleted)."""
    for st, path in changes:
        full = os.path.join(root, path)
        if st == "A":
            if os.path.exists(full):
                os.remove(full)
        else:
            blob = _git(root, "rev-parse", "%s:%s" % (tree, path)).stdout.strip()
            data = subprocess.run(["git", "cat-file", "-p", blob], cwd=root, capture_output=True).stdout
            os.makedirs(os.path.dirname(full) or root, exist_ok=True)
            open(full, "wb").write(data)


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
    return {"sites": sites}


def current_plan(root):
    """The planner's verdict on the code as it stands (measure_only: router-migrate wraps count only where present)."""
    return planmod.build(_scan(root), Catalog.load(), measure_only=True)


def site_at(sites, rel, line, slack=3):
    """Closest site to file:line (exact first), or None."""
    cands = [s for s in sites if s["file"] == rel and abs(int(s["line"]) - line) <= slack]
    return sorted(cands, key=lambda s: (abs(int(s["line"]) - line), s["kind"] != "call"))[0] if cands else None


def facts_for(root, rel, line, p=None):
    p = p or current_plan(root)
    s = site_at(p["sites"], rel, line)
    clients = {x["site_id"]: x for x in []}
    if s is None:
        return {"found": False, "file": rel, "line": line, "routes": None,
                "why": "the scanner sees no LLM call at %s:%d (nearest sites in this file: %s)" % (
                    rel, line, ", ".join(str(x["line"]) for x in p["sites"] if x["file"] == rel)[:120] or "none")}
    f = {"found": True, "site": s["id"], "file": s["file"], "line": s["line"], "kind": s["kind"], "sdk": s["sdk"], "surface": s["surface"],
         "model": s.get("model"), "base_url": s.get("base_url"), "outcome": s["outcome"], "reason": s["reason"],
         "routes": s["outcome"] == "migrated", "client": s.get("via_client") or s.get("client_id")}
    del clients
    return f


def next_for(root, f, p=None):
    """The concrete next step for a site the runtime does not reroute yet."""
    if not f.get("found"):
        return ("if this is a real LLM call the scanner misses, decide its outcome and `claim` it with evidence; a migrated claim "
                "here is confirmed only if `finish`'s Router-on test run sees a request from it")
    if f["routes"]:
        return "routes: claim it (`router-migrate claim %s:%s migrated --why ... --evidence FILE:LINE`)" % (f["file"], f["line"])
    reason = f["reason"]
    p = p or current_plan(root)
    if "constructed outside this repo or not resolvable" in reason or "not resolved statically" in reason or "could not be patched" in reason:
        cl = [s for s in _scan(root)["sites"] if s["kind"] == "client" and s["file"] == f["file"] and not s.get("wrapped")]
        hint = ", ".join("%s:%d %s" % (c["file"], c["line"], c["surface"]) for c in cl[:3])
        return ("find the client construction this call uses and `router-migrate wrap FILE:LINE` it" +
                (" (unwrapped constructions in this file: %s)" % hint if hint else "; follow where the client object comes from"))
    if reason.startswith("module-level") and "install hook" in reason:
        return "module-level usage: `router-migrate wrap %s:1` adds the install hook for this file" % f["file"]
    return "the tool says this site stays where it is (%s); claim the matching outcome with evidence, or `needs_human`" % reason


# ------------------------------------------------------------------------------------------------ hotspots + worklist
def _source_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for fn in files:
            if fn.endswith(SRC_EXT) and not fn.endswith((".d.ts", ".min.js")):
                full = os.path.join(d, fn)
                if os.path.getsize(full) <= 1024 * 1024:
                    yield os.path.relpath(full, root)


def hotspots(root, sites):
    """Lines that look like LLM usage but that no scanner site explains (production files only)."""
    by_file = {}
    for s in sites:
        by_file.setdefault(s["file"], []).append(int(s["line"]))
    found = []
    for rel in _source_files(root):
        if not scan_py.is_prod(rel) or RUNTIME_FILES.search(rel):
            continue
        try:
            lines = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        near = by_file.get(rel, [])
        for i, l in enumerate(lines, 1):
            if len(l) > 400 or l.lstrip().startswith(("#", "//", "*")):
                continue
            kind = "endpoint" if LLM_HOSTS.search(l) else "llm-import" if LLM_IMPORTS.search(l) else "model-literal" if MODEL_LIT.search(l) else None
            if not kind:
                continue
            if kind == "llm-import" and near:
                continue  # the scanner found sites in this file
            if kind != "llm-import" and any(abs(i - n) <= 15 for n in near):
                continue
            found.append({"kind": kind, "file": rel, "line": i, "text": l.strip()[:120]})
    # one import hotspot per file is enough
    seen, outl = set(), []
    for h in found:
        k = (h["file"], h["kind"]) if h["kind"] == "llm-import" else (h["file"], h["kind"], h["line"])
        if k not in seen:
            seen.add(k)
            outl.append(h)
    return outl


def worklist(root, p, hs):
    """What needs the agent's judgment: scanner sites the rules can't settle, plus unexplained hotspots."""
    clients = {s["id"]: s for s in _scan(root)["sites"] if s["kind"] == "client"}
    items = []
    for s in p["sites"]:
        if not s.get("prod"):
            continue
        why = None
        if s["outcome"] in ("flagged", "handoff"):
            why = s["outcome"] + ": " + s["reason"]
        elif s["outcome"] == "migrated":
            c = clients.get(s.get("via_client") or s.get("client_id") or "") or {}
            bu = c.get("base_url") or s.get("base_url")
            if bu and not (bu.get("static") and bu.get("value")):
                why = "migrated only if the configured base URL is OpenAI/Anthropic in production: confirm where it points"
            elif (c.get("api_key") or {}).get("static") is False and (c.get("api_key") or {}).get("value") is None and c.get("api_key"):
                why = "migrated, but the API key comes from code the tool can't follow: confirm it is the operator's key, not a user's"
        elif s["outcome"] == "kept" and ("not resolved statically" in s["reason"] or "config-driven" in s["reason"]):
            why = "kept: " + s["reason"]
        if why:
            items.append({"id": "S%d" % (len(items) + 1), "type": "site", "file": s["file"], "line": s["line"], "what": "%s %s" % (s["sdk"], s["surface"]),
                          "why": why, "tool_outcome": s["outcome"]})
    for h in hs:
        items.append({"id": "H%d" % (len(items) + 1), "type": "hotspot", "file": h["file"], "line": h["line"], "what": h["kind"],
                      "why": "no scanner site explains: %s" % h["text"]})
    return items


def resolved(led, item):
    return any(c["file"] == item["file"] and abs(int(c["line"]) - int(item["line"])) <= 3 for c in led["claims"].values())


# ------------------------------------------------------------------------------------------------ commands
def cmd_run(a):
    from . import cli
    root = os.path.abspath(a.path)
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode:
        return err(a.json, "%s is not a git repository" % root, "run `git init && git add -A && git commit -m base` first (the floor is snapshotted with git)")
    t0 = time.time()
    typecheck_before = run_cmd(root, a.typecheck_cmd) if getattr(a, "typecheck_cmd", None) else None
    cli.do_scan(root, quiet=True)
    p0 = cli.do_plan(root, quiet=True)
    cli.do_apply(root, quiet=True)
    v = None
    if not a.skip_verify:
        v = cli.do_verify(root, a)
    cli.do_report(root, quiet=True)
    tree = snapshot_tree(root)
    p = current_plan(root)
    hs = hotspots(root, p["sites"] + [s for s in _scan(root)["sites"] if s["kind"] == "client"])
    led = load_ledger(root) or {}
    led.update({"version": 3, "root": root, "created": led.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "floor": {"tree": tree, "migrated": p["summary"]["migrated"]},
                "baseline": {"test_cmd": (v or {}).get("test_cmd") or getattr(a, "test_cmd", None),
                             "before": (v or {}).get("tests_before"), "after_floor": (v or {}).get("tests_after"),
                             "router_on_floor": (v or {}).get("sim_tests"),
                             "typecheck_cmd": getattr(a, "typecheck_cmd", None),
                             "typecheck_before": typecheck_before and {"exit": typecheck_before["exit"], "errors": typecheck_before["ts_errors"]}},
                "worklist": worklist(root, p, hs), "claims": led.get("claims") or {}, "edits": led.get("edits") or [],
                "finish": {"attempts": 0, "history": []}})
    led["edits"].append({"by": "codemod", "what": "floor", "files": json.load(open(sp(root, "apply.json"))).get("files_changed", [])})
    save_ledger(root, led)
    wl = led["worklist"]
    sm = p["summary"]
    lines = ["floor applied: %d production sites, %d migrated by the deterministic codemod (%d kept, %d flagged, %d handoff) in %.0fs" % (
        sm["sites_found"], sm["migrated"], sm["kept"], sm["flagged"], sm["handoff"], time.time() - t0)]
    if v:
        lines.append("baseline: tests %s | after floor %s | test command: %s" % (_tc(v.get("tests_before")), _tc(v.get("tests_after")), v.get("test_cmd")))
    lines.append("worklist: %d items need your judgment (S = scanner site the rules can't settle, H = hotspot no scanner site explains)" % len(wl))
    for it in wl:
        lines.append("  %-4s %s:%s  %s  -- %s" % (it["id"], it["file"], it["line"], it["what"], it["why"][:150]))
    lines.append("next: for each item read the code, then `router-migrate check FILE:LINE`; `wrap` client constructions the floor missed; "
                 "`claim` every item; then `router-migrate finish`")
    return out({"ok": True, "summary": sm, "worklist": wl, "baseline": led["baseline"]}, a.json, "\n".join(lines))


def cmd_check(a):
    root = os.path.abspath(a.path)
    rel, line = parse_loc(a.loc)
    if not rel:
        return err(a.json, "expected FILE:LINE, got %r" % a.loc, "e.g. `router-migrate check app/llm.py:42`")
    if not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "no such file: %s" % rel, "paths are relative to the repo root (%s)" % root)
    p = current_plan(root)
    f = facts_for(root, rel, line, p)
    f["next"] = next_for(root, f, p)
    led = load_ledger(root)
    if led is not None:
        led.setdefault("facts", {})["%s:%d" % (rel, line)] = dict(f, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        save_ledger(root, led)
    if f["found"]:
        txt = "%s:%s %s %s -> %s%s\n  why: %s\n  next: %s" % (f["file"], f["line"], f["sdk"], f["surface"], "ROUTES" if f["routes"] else "does not route",
                                                          "" if f["routes"] else " (%s)" % f["outcome"], f["reason"], f["next"])
    else:
        txt = "%s:%d: %s\n  next: %s" % (rel, line, f["why"], f["next"])
    return out(dict(f, ok=True), a.json, txt)


def cmd_wrap(a):
    from . import apply_py, cli
    from .runtime import render
    root = os.path.abspath(a.path)
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "point at the line where the client is constructed, e.g. `OpenAI(`")
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    before = open(os.path.join(root, rel), encoding="utf-8").read()
    sites = _scan(root)["sites"]
    c = [s for s in sites if s["file"] == rel and s["kind"] in ("client", "module_client") and abs(int(s["line"]) - line) <= 2]
    c = sorted(c, key=lambda s: abs(int(s["line"]) - line))
    cat = Catalog.load()
    if rel.endswith(".py"):
        opts = {"responses": False} if a.stateful else None
        installs = set()
        if c and c[0]["kind"] == "module_client":
            installs = {"install_litellm" if c[0]["sdk"] == "litellm" else "install_openai_module"}
            targets = {}
        elif c:
            kind = planmod.PATCHABLE.get(c[0]["sdk"])
            if not kind or kind == "ts":
                return err(a.json, "%s (%s) has no deterministic wrap" % (c[0]["surface"], c[0]["sdk"]),
                           "only OpenAI/Anthropic SDK clients, LangChain ChatOpenAI/ChatAnthropic and LlamaIndex OpenAI can be wrapped; "
                           "claim this site with the right outcome instead")
            targets = {(int(c[0]["line"]), int(c[0]["col"])): (kind, opts)}
        else:
            targets = {(line, None): ("wrap_openai", opts)}  # a bare OpenAI(/Anthropic( constructor the scanner did not record
            if re.search(r"\bAsyncAnthropic\(|\bAnthropic\(", before.splitlines()[line - 1] if line <= len(before.splitlines()) else ""):
                targets = {(line, None): ("wrap_anthropic", None)}
        changed, done, skipped, rt_rel = apply_py.patch_file(root, rel, targets, installs)
        if skipped:
            return err(a.json, "; ".join(skipped.values()), "check the line points at the constructor call itself")
        if not done and not changed:
            return err(a.json, "no OpenAI/Anthropic/LangChain/LlamaIndex client construction found at %s:%d" % (rel, line),
                       "point at the line where the constructor call starts (`router-migrate check` lists unwrapped constructions)")
        if rt_rel:
            rtp = os.path.join(root, rt_rel)
            if not os.path.exists(rtp):
                open(rtp, "w").write(render("router_runtime.py", cat))
    else:
        if not c:
            return err(a.json, "the scanner sees no client construction at %s:%d" % (rel, line),
                       "point at `new OpenAI(`/`new Anthropic(`/`createOpenAI(`/`createAnthropic(`; other providers can't be wrapped")
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
            rf.write(render("routerRuntime.ts", cat))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
            json.dump([c[0]["id"]], sf)
        r = cli.run_ts(["apply", root, "--runtime", rf.name, "--sites", sf.name])
        bad = [e for e in r.get("edits", []) if not e.get("ok")]
        if bad:
            return err(a.json, bad[0].get("reason") or "TS wrap failed", "claim the site with the right outcome, or `needs_human`")
    after = open(os.path.join(root, rel), encoding="utf-8").read()
    import difflib
    d = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/" + rel, "b/" + rel))
    led = load_ledger(root)
    led["edits"].append({"by": "agent-wrap", "file": rel, "line": line, "changed": before != after, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    save_ledger(root, led)
    msg = (d or "already wrapped: nothing to change\n") + "next: `router-migrate check FILE:LINE` on the calls this client serves, then claim them"
    return out({"ok": True, "file": rel, "changed": before != after, "diff": d}, a.json, msg)


def cmd_claim(a):
    root = os.path.abspath(a.path)
    rel, line = parse_loc(a.loc)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "use the line where the call or construction starts")
    if a.outcome not in OUTCOMES:
        return err(a.json, "outcome must be one of %s" % ", ".join(OUTCOMES), "pick the outcome the rubric in your instructions gives")
    if not a.why:
        return err(a.json, "--why is required", "say in one sentence why, citing what you read")
    ev = []
    for e in a.evidence or []:
        for item in e.split(","):
            item = item.strip()
            if not item:
                continue
            er, el = parse_loc(item)
            if not er or not os.path.exists(os.path.join(root, er)):
                return err(a.json, "evidence %r does not resolve to an existing FILE:LINE" % item, "cite real lines, e.g. `--evidence config.py:12`")
            n = sum(1 for _ in open(os.path.join(root, er), encoding="utf-8", errors="replace"))
            if el < 1 or el > n:
                return err(a.json, "evidence %s: line %d is outside the file (%d lines)" % (er, el, n), "cite a line that exists")
            ev.append("%s:%d" % (er, el))
    if not ev:
        return err(a.json, "at least one --evidence FILE:LINE is required", "cite the line(s) that justify the outcome (config default, key source, ...)")
    p = current_plan(root)
    f = facts_for(root, rel, line, p)
    confirmed = None
    if a.outcome == "migrated":
        if f["found"] and not f["routes"]:
            return err(a.json, "the tool says %s:%s does not route: %s" % (f["file"], f["line"], f["reason"]),
                       next_for(root, f, p) + " (facts beat claims: a migrated claim needs the runtime to reroute the site)")
        confirmed = "static" if f["found"] else None  # unseen by the scanner: finish's Router-on run must see it
    key = "%s:%d" % (f["file"] if f.get("found") else rel, int(f["line"]) if f.get("found") else line)
    led["claims"][key] = {"file": f["file"] if f.get("found") else rel, "line": int(f["line"]) if f.get("found") else line,
                          "outcome": a.outcome, "why": a.why, "evidence": ev, "tool_outcome": f.get("outcome"), "tool_reason": f.get("reason"),
                          "origin": "scanner" if f["found"] else "agent", "confirmed": confirmed, "by": "agent",
                          "feature": _feature(root, rel, line), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_ledger(root, led)
    left = [it for it in led.get("worklist", []) if not resolved(led, it)]
    note = ""
    if a.outcome == "migrated" and not confirmed:
        note = " (unconfirmed: the scanner can't see this call; `finish` confirms it only if its Router-on test run sends a request from it)"
    elif f.get("found") and f["routes"] and a.outcome != "migrated":
        note = " (judgment recorded over the tool's static view, which says it routes: your evidence must show why it doesn't in production)"
    return out({"ok": True, "claim": led["claims"][key], "worklist_left": len(left)}, a.json,
               "claimed %s %s%s\nworklist: %d item(s) left%s" % (key, a.outcome, note, len(left),
                                                                 "" if left else "; next: `router-migrate finish`"))


def _feature(root, rel, line):
    """file:function the runtime would tag requests from this line with."""
    from .recommend import enclosing_function
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


def gate(root, led, quick=False):
    """Run the gates cheapest first; stop at the first hard failure. Returns (ok, failures, info)."""
    from .verify import syntax_check, SIM_KEY
    fails, info = [], {}
    changes = changed_since(root, led["floor"]["tree"])
    changed = [p for st, p in changes if st != "D"]
    info["agent_changed_files"] = [p for _, p in changes]
    # 1 parse
    sx = syntax_check(root, changed)
    for x in sx["failed"]:
        fails.append({"gate": "parse", "detail": "%s: %s" % (x["file"], x["error"][-300:]), "next": "fix the syntax error in %s" % x["file"]})
    # 2 scope: no tests, lockfiles or CI edited; no deletions; every edited file holds a claimed site or an agent wrap
    linked = {c["file"] for c in led["claims"].values()} | {e.get("file") for e in led["edits"] if e.get("by") == "agent-wrap"}
    runtime_like = RUNTIME_FILES
    for st, path in changes:
        if st == "D":
            fails.append({"gate": "scope", "detail": "%s was deleted" % path, "next": "restore it: deleting files is out of scope"})
        elif not scan_py.is_prod(path) or re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[jt]sx?$", path):
            fails.append({"gate": "scope", "detail": "test file %s was edited" % path,
                          "next": "revert it: tests prove nothing broke, so they are never edited to pass (`git checkout -- %s` or undo your edit)" % path})
        elif os.path.basename(path) in LOCKFILES or path.startswith((".github/", ".gitlab")):
            fails.append({"gate": "scope", "detail": "%s was edited" % path, "next": "revert it: dependency and CI files are out of scope"})
        elif not runtime_like.search(path) and path not in linked and not path.endswith((".md", ".example")):
            fails.append({"gate": "scope", "detail": "%s changed but no claim or wrap links it to a site" % path,
                          "next": "claim the site you changed in it (`router-migrate claim %s:LINE ...`) or revert the file" % path})
    if fails:
        return False, fails, info
    if quick:
        return True, [], info
    base = led.get("baseline") or {}
    # 3 typecheck vs baseline
    if base.get("typecheck_cmd"):
        tc = run_cmd(root, base["typecheck_cmd"])
        info["typecheck"] = {"exit": tc["exit"], "errors": tc["ts_errors"]}
        tb = base.get("typecheck_before") or {}
        if tc["ts_errors"] > (tb.get("errors") or 0) or (tc["exit"] and not tb.get("exit")):
            fails.append({"gate": "typecheck", "detail": "%d type errors (baseline %s)\n%s" % (tc["ts_errors"], tb.get("errors"), tc["tail"][-800:]),
                          "next": "fix the new type errors in the files you changed"})
            return False, fails, info
    # 4 tests vs baseline (Router off)
    cmd = base.get("test_cmd")
    if cmd:
        t = run_cmd(root, cmd)
        info["tests"] = {k: t.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")}
        if _worse(base.get("before"), t):
            fails.append({"gate": "tests", "detail": "tests now %s, baseline %s\n%s" % (_tc(t), _tc(base.get("before")), t["tail"][-1000:]),
                          "next": "fix what your edits broke (never edit the tests); if you can't, `router-migrate claim` the site needs_human "
                                  "and undo its edits"})
            return False, fails, info
        # 5 Router on, against router-sim: confirms claims the scanner can't see
        from .sim.server import serve
        srv, sim, sbase = serve()
        try:
            t2 = run_cmd(root, cmd, env={"RAMP_ROUTER_API_KEY": SIM_KEY, "RAMP_ROUTER_BASE_URL": sbase})
            recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
            info["sim"] = {"requests": len(recs), "valid": sum(1 for x in recs if x.get("valid")),
                           "features": sorted({x.get("feature") for x in recs if x.get("feature")})[:50]}
            bad = [x for x in recs if not x.get("valid")]
            if bad:
                fails.append({"gate": "sim", "detail": "router-sim rejected %d request(s): %s" % (len(bad), "; ".join(
                    "%s %s" % (x.get("feature"), x.get("error")) for x in bad[:3])), "next": "a request your edit sends to Router is invalid there; "
                    "fix the edit or undo it and claim needs_human"})
                return False, fails, info
            info["tests_router_on"] = {k: t2.get(k) for k in ("passed", "failed", "errors", "exit")}
            # compared with the floor's own Router-on run: suites that mock HTTP transports see Router requests in their mocks
            # under the deterministic floor too, so only a regression past the floor is the agent's
            if _worse(base.get("router_on_floor"), t2):
                fails.append({"gate": "sim", "detail": "tests with Router on (router-sim) now %s, floor %s\n%s" % (
                    _tc(t2), _tc(base.get("router_on_floor")), t2["tail"][-800:]),
                              "next": "your edit changes behavior when Router is on; fix it or undo it"})
                return False, fails, info
        finally:
            srv.shutdown()
    return True, [], info


def cmd_finish(a):
    root = os.path.abspath(a.path)
    hook_in = {}
    if a.hook:
        try:
            hook_in = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            hook_in = {}
    led = load_ledger(root)
    if led is None:
        return _finish_out(a, False, "no ledger: run `router-migrate run .` first, then work the worklist", [])
    ok, fails, info = gate(root, led)
    # static claims are re-checked on the final code; scanner-invisible migrated claims need the Router-on run
    p = current_plan(root)
    feats = set((info.get("sim") or {}).get("features") or [])
    for c in led["claims"].values():
        if c["outcome"] != "migrated":
            continue
        f = facts_for(root, c["file"], c["line"], p)
        if f["found"]:
            c["confirmed"] = "static" if f["routes"] else None
            if not f["routes"]:
                fails.append({"gate": "claims", "detail": "%s:%s is claimed migrated but the tool says: %s" % (c["file"], c["line"], f["reason"]),
                              "next": next_for(root, f, p)})
        else:
            c["confirmed"] = "dynamic" if c.get("feature") in feats else "unconfirmed"
    left = [it for it in led.get("worklist", []) if not resolved(led, it)]
    if left:
        fails.append({"gate": "worklist", "detail": "%d worklist item(s) have no claim: %s" % (len(left), ", ".join(
            "%s %s:%s" % (it["id"], it["file"], it["line"]) for it in left[:12])), "next": "claim each one (needs_human is allowed)"})
    ok = not fails
    fin = led.setdefault("finish", {"attempts": 0, "history": []})
    fin["attempts"] = fin.get("attempts", 0) + (0 if ok else 1)
    fin["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "gates": [f["gate"] for f in fails], "info": info})
    msg_extra = ""
    if not ok and fin["attempts"] >= a.max_attempts:
        hard = [f for f in fails if f["gate"] in ("parse", "scope", "typecheck", "tests", "sim")]
        if hard:
            changes = changed_since(root, led["floor"]["tree"])
            keep_rt = [(st, pth) for st, pth in changes if not (st == "A" and re.search(r"router_runtime\.py$|routerRuntime\.ts$", pth))]
            restore(root, led["floor"]["tree"], keep_rt)
            for c in led["claims"].values():
                if c["outcome"] == "migrated" and c.get("tool_outcome") != "migrated" or c.get("origin") == "agent" and c["outcome"] == "migrated":
                    c["outcome"], c["why"] = "needs_human", "reverted after %d failed gate runs: " % fin["attempts"] + c["why"]
            fin["reverted"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": [pth for _, pth in keep_rt], "last_failures": fails}
            msg_extra = "reverted your edits to the floor after %d failed gate runs (%s); sites you changed are now needs_human" % (
                fin["attempts"], ", ".join(sorted({f["gate"] for f in hard})))
        for it in [it for it in led.get("worklist", []) if not resolved(led, it)]:
            led["claims"]["%s:%s" % (it["file"], it["line"])] = {"file": it["file"], "line": it["line"], "outcome": "needs_human",
                                                                 "why": "unresolved when the gate attempts ran out", "evidence": [], "by": "finish",
                                                                 "origin": "scanner" if it["type"] == "site" else "hotspot", "confirmed": None}
        ok, fails = True, []
        msg_extra = msg_extra or "gate attempts ran out; unresolved items marked needs_human"
    led["result"] = summarize(led, p)
    save_ledger(root, led)
    return _finish_out(a, ok, msg_extra, fails, led["result"])


def summarize(led, p):
    cl = list(led["claims"].values())
    by = {}
    for c in cl:
        by[c["outcome"]] = by.get(c["outcome"], 0) + 1
    floor_ids = {"%s:%s" % (s["file"], s["line"]) for s in p["sites"] if s["outcome"] == "migrated" and s.get("prod")}
    agent_moved = [c for c in cl if c["outcome"] == "migrated" and c.get("confirmed") in ("static", "dynamic")]
    return {"migrated_static": len(floor_ids), "claims": by, "migrated_confirmed_claims": len(agent_moved),
            "migrated_unconfirmed_claims": sum(1 for c in cl if c["outcome"] == "migrated" and c.get("confirmed") == "unconfirmed"),
            "overridden_to_not_migrated": sum(1 for c in cl if c.get("tool_outcome") == "migrated" and c["outcome"] != "migrated")}


def _finish_out(a, ok, extra, fails, result=None):
    if a.hook:
        if ok:
            print(json.dumps({"systemMessage": "router-migrate finish: %s" % (extra or "all gates pass")}))
        else:
            reason = "router-migrate finish failed; you are not done:\n" + "\n".join(
                "- [%s] %s\n  next: %s" % (f["gate"], f["detail"][:600], f["next"]) for f in fails) + ("\n" + extra if extra else "")
            print(json.dumps({"decision": "block", "reason": reason[:6000]}))
        return 0
    if a.json:
        print(json.dumps({"ok": ok, "failures": fails, "note": extra, "result": result}, indent=1, default=str))
    else:
        if ok:
            print("finish: OK%s" % (" (%s)" % extra if extra else ""))
            if result:
                print("result: %s" % json.dumps(result))
        else:
            for f in fails:
                print("FAIL [%s] %s\n  next: %s" % (f["gate"], f["detail"][:1500], f["next"]))
    return 0 if ok else 1


def add_parsers(sub):
    s = sub.add_parser("check", help="v3: tool facts for one site in the code as it stands (does the runtime reroute it? why not? next step)")
    s.add_argument("loc", help="FILE:LINE (relative to the repo root)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("wrap", help="v3: apply the deterministic one-line wrap at a client construction (factories included); prints the diff")
    s.add_argument("loc", help="FILE:LINE of the constructor call")
    s.add_argument("--path", default=".")
    s.add_argument("--stateful", action="store_true", help="keep this client's Responses API traffic on the provider (previous_response_id/store)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("claim", help="v3: record an outcome for a site or hotspot with evidence (facts beat claims)")
    s.add_argument("loc", help="FILE:LINE")
    s.add_argument("outcome", choices=OUTCOMES)
    s.add_argument("--why", required=False)
    s.add_argument("--evidence", action="append", help="FILE:LINE that justifies the outcome (repeatable or comma-separated)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("finish", help="v3: the gate (syntax, scope, typecheck, tests vs baseline, Router-on sim run, claims, worklist); "
                                      "reverts to the floor after --max-attempts failures")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--hook", action="store_true", help="Claude Code Stop hook mode (JSON decision on stdout)")
    s.add_argument("--max-attempts", type=int, default=3)
    s.add_argument("--json", action="store_true")


def dispatch(a):
    return {"check": cmd_check, "wrap": cmd_wrap, "claim": cmd_claim, "finish": cmd_finish, "run": cmd_run}[a.cmd](a)
