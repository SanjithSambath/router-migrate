"""v4: the agent does the migration; the tools verify outcomes (SPEC_V3.md, METHODOLOGY.md).

Nothing is applied before the agent looks. `run` records the baseline and turns the rules into a proposal per site (outcome,
reason, hard limit, suggested edit); the agent reviews every one and decides. Hard limits stay hard (no endpoint, provider-side
state, a broken build or test suite); everything else is the agent's call with evidence, and lossy or business moves become
cards a human approves. A migrated claim counts when the code routes it as written (static) or a valid router-sim request
carries its feature (dynamic: `probe` or the gate's Router-on test run).

  run    PATH            baseline + a proposal for every site + hotspots (nothing applied); --agent launches the agent, then the gate
  check  FILE:LINE       facts for one site: routes or not, hard limit, proposal, evidence so far, native eligibility
  wrap   FILE:LINE       the deterministic wrap at a construction (--native: base_url + key, no runtime, when provably safe)
  unwrap FILE:LINE       its inverse
  probe  [FILE:LINE] --cmd CMD   run any command against router-sim; valid requests tagged with a site's feature confirm it
  claim  FILE:LINE[,...] OUTCOME  a decision with evidence (lines read, probes run); --latency feeds the guardrails
  card   FILE:LINE --kind K       model_swap | vendor_endpoint | per_user_key | beta_flags, for a human to approve
  finish PATH            the gate; after --max-attempts failures it reverts every edit

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
REASON_CODES = {  # enumerated reasons for every outcome other than migrated (free text goes in --why)
    "R_NO_ENDPOINT": "Router has no endpoint for this surface (embeddings, images, audio, files, moderation, legacy completions)",
    "R_OTHER_HOST": "the request goes to another vendor's endpoint in production (incl. a user-defined provider list)",
    "R_PER_TENANT": "the API key is per user / tenant / session",
    "R_STATEFUL": "stateful Responses use or provider-side files",
    "R_BETA": "Anthropic beta features",
    "R_MODEL_NOT_IN_CATALOG": "the model is not a Router catalog id",
    "R_DEAD_CODE": "unreachable or unused code",
    "R_NOT_PRODUCTION": "tests, examples, scripts, eval harnesses, docs, templates, or not an LLM call",
    "R_RAW_REQUESTS": "raw HTTP to OpenAI/Anthropic that needs a code change (no runtime adapter for this client)",
    "R_OTHER_GATEWAY": "another LLM gateway (Vercel AI Gateway, LiteLLM proxy, ...)",
    "R_OTHER_VENDOR_SDK": "another vendor's SDK whose model is in Router's catalog (needs a port or BYOK)",
    "R_BYOK_REQUIRED": "Router serves the model only with the customer's own provider key",
    "R_UNDECIDED": "the code doesn't settle it; say what a human needs to check",
    "R_GATE_FAILED": "the edit failed the gate and was reverted",
}
# v4: the only outcomes the tools refuse. Everything else is the agent's call, with evidence.
HARD_LIMITS = {"no_endpoint": "Router has no endpoint for this surface (embeddings, images, audio, files, assistants, vector stores, "
                              "moderation, legacy completions, model lists)",
               "stateful": "provider-side state Router does not keep (previous_response_id, conversation, store=True, retrieve, "
                           "background, provider files or vector stores)"}
# lossy or business moves: never edits, only cards a human approves (applied by `apply --decisions`)
CARD_KINDS = {"model_swap": "serve this feature with a different Router catalog model (applied as policy model_map)",
              "vendor_endpoint": "serve another vendor's OpenAI-compatible endpoint's traffic through Router (same catalog model)",
              "per_user_key": "route only requests made with the operator's own provider key; users' keys stay on the provider",
              "beta_flags": "drop the listed Anthropic beta flags so the request can go to Router (states what is lost)"}
PROVIDER_KEY_ENV = re.compile(r"\b(OPENAI_API_KEY|ANTHROPIC_API_KEY|AZURE_OPENAI_API_KEY|OPENAI_ORG_ID|OPENAI_ORGANIZATION)\b")
GUARD_TIMEOUT = int(os.environ.get("ROUTER_MIGRATE_GUARD_TIMEOUT", "30"))  # provider_timeout for user-waiting buffered features
NATIVE_SURFACES = ("responses", "messages", "messages.stream", "messages.count_tokens")
PROBE_TIMEOUT = 300
SRC_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".next", "dist", "build", "out", "coverage", STATE,
             ".tox", ".mypy_cache", ".pytest_cache", "site-packages", ".turbo", ".cache"}
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock", "bun.lockb")
MAX_LINES = 200
TEST_FILES = re.compile(r"\.(test|spec|stories|story|e2e)\.[cm]?[jt]sx?$|(^|/)(__tests__|__mocks__|__fixtures__|cypress|playwright|e2e|storybook)(/|$)")
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


def next_for(root, f, p=None):
    """The concrete next step for a site, in v4 terms: facts, then the agent's decision, then evidence."""
    if not f.get("found"):
        return ("the scanner can't see this call: if it is a production LLM call, decide its outcome; to claim `migrated`, route it "
                "(wrap its client, or edit by hand) and prove it: `router-migrate probe %s:%s --cmd \"<run the code path>\"`" % (f["file"], f["line"]))
    if f.get("hard_limit"):
        return "hard limit (%s): this call can't move to Router; claim `kept` (or `not_a_site`)" % HARD_LIMITS[f["hard_limit"]]
    if f["routes"] and f.get("verdict") == "routes_if":
        return ("routes only if %s: settle that from config, env examples and docs. If production points elsewhere, can it still "
                "move (`adapt` with a lever, e.g. --unlock-host or --model-map)? Claim what is true, citing the line that settles it" % f["condition"])
    if f["routes"]:
        return "routes as the code stands (static evidence): claim it migrated"
    reason = f["reason"]
    if "constructed outside this repo or not resolvable" in reason or "not resolved statically" in reason or "could not be patched" in reason \
            or reason.startswith("handoff"):
        cl = [s for s in _scan(root)["sites"] if s["kind"] == "client" and s["file"] == f["file"] and not s.get("wrapped")]
        hint = ", ".join("%s:%d %s" % (c["file"], c["line"], c["surface"]) for c in cl[:3])
        return ("the tool can't see this call's client routing: wrap the construction it uses (%s), or edit by hand; then prove the call "
                "routes with `probe`" % (hint or "follow where the client comes from"))
    if reason.startswith("module-level") and "install hook" in reason:
        return "module-level usage: `router-migrate wrap %s:1` adds the install hook for this file" % f["file"]
    return ("doesn't route now (%s). Can it move at an acceptable cost? `adapt` takes the lever (drop a parameter, swap the model, "
            "unlock the host, route only the operator's key, drop a beta) and records what is lost; then prove it routes. Keep it only "
            "if the loss would be the product itself" % reason)


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
        if not scan_py.is_prod(rel) or RUNTIME_FILES.search(rel) or TEST_FILES.search(rel):
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
    # one import hotspot per file, and one model-literal hotspot per file (with the count): model-name lists are data far more
    # often than calls, and resolving them one by one cost agents most of their time (eval/v3/tool_usage.json)
    seen, outl, lit_count = set(), [], {}
    for h in found:
        if h["kind"] == "model-literal":
            lit_count[h["file"]] = lit_count.get(h["file"], 0) + 1
        k = (h["file"], h["kind"]) if h["kind"] in ("llm-import", "model-literal") else (h["file"], h["kind"], h["line"])
        if k not in seen:
            seen.add(k)
            outl.append(h)
    for h in outl:
        if h["kind"] == "model-literal" and lit_count.get(h["file"], 0) > 1:
            h["text"] = "%s  (+%d more model-name literals in this file)" % (h["text"], lit_count[h["file"]] - 1)
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
            verdict, cond = trace(s, clients)
            if verdict == "routes_if":
                why = "routes_if: %s; settle the condition with evidence or it is reported as conditional" % cond
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


def build_proposals(root, p0):
    """The handoff: one proposal per production site from the rules' plan (plan.json): the outcome the rules would give, why,
    any hard limit, and the edit they would make. Nothing is applied; the agent decides every one."""
    patches = {pt["site_id"]: pt for pt in p0.get("patches") or []}
    out = []
    for s in p0["sites"]:
        if not s.get("prod"):
            continue
        hl = hard_limit(s)
        pt = patches.get(s.get("via_client") or s.get("client_id") or "") or patches.get(s["id"])
        if hl:
            edit = {"do": "none", "why": HARD_LIMITS[hl]}
        elif s["outcome"] == "migrated" and pt:
            edit = {"do": "wrap", "at": "%s:%s" % (pt["file"], pt["line"]), "cmd": "router-migrate wrap %s:%s" % (pt["file"], pt["line"])}
        elif s["outcome"] == "migrated" and s["kind"] == "module_client":
            edit = {"do": "install", "at": "%s:1" % s["file"], "cmd": "router-migrate wrap %s:1" % s["file"]}
        else:
            edit = {"do": "none", "hint": "doesn't route as written; `check` it and consider whether a lever (`adapt`) moves it"}
        out.append({"id": "P%d" % (len(out) + 1), "type": "site", "file": s["file"], "line": int(s["line"]), "what": "%s %s" % (s["sdk"], s["surface"]),
                    "proposal": s["outcome"], "reason": s["reason"], "hard_limit": hl, "edit": edit, "stream": s.get("stream"),
                    "why": "proposal %s: %s" % (s["outcome"], s["reason"]), "tool_outcome": s["outcome"]})
    return out


def shadow_floor_run(root, test_cmd, tree, timeout=1800):
    """Router-on baseline for the gate: apply the rules' suggested edits, run the tests against router-sim, then put the tree
    back exactly (the agent never sees it). Suites that mock HTTP transports see Router requests in their mocks under any
    correct migration; only failures beyond this run are the agent's. The suggested edits stay readable as proposals.diff."""
    from . import cli
    from .verify import sim_test_run
    res = None
    try:
        cli.do_apply(root, quiet=True)
        res = sim_test_run(root, test_cmd, timeout)
    finally:
        restore(root, tree, changed_since(root, tree))
        for src, dst in (("apply.diff", "proposals.diff"), ("apply.json", "proposals_apply.json")):
            if os.path.exists(sp(root, src)):
                os.replace(sp(root, src), sp(root, dst))
    return res


def native_eligibility(root, p, client_id):
    """(eligible, why) for pointing a client straight at Router (base_url + key, no runtime): every site it serves must use a
    surface Router serves natively, with an exact catalog model, no beta flags, no provider state, an operator key. The proof
    is still dynamic: each native site needs a valid probe or test-run request (there is no runtime fallback)."""
    from .catalog import Catalog as _C
    c = (p.get("clients") or {}).get(client_id)
    if not c:
        return False, "no client construction found for this site"
    if c["lang"] != "py" or c["sdk"] not in ("openai", "anthropic") or c["surface"] not in ("OpenAI", "AsyncOpenAI", "Anthropic", "AsyncAnthropic"):
        return False, "the native codemod covers Python OpenAI/Anthropic SDK constructors; elsewhere edit by hand and prove it with `probe`"
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
    return True, "%d site(s), all on Router-native surfaces with exact catalog models; prove each with `probe` (no runtime fallback)" % len(sites)


def glob_for(feature):
    """Policy glob for a feature, robust to the process's working directory: '**<last two path parts>:<function>'."""
    path, _, fn = feature.rpartition(":")
    return "**%s:%s" % ("/".join(path.split("/")[-2:]), fn)


def compile_guardrails(root, led):
    """Latency guardrails from the agent's judgments, compiled into the policy the runtime reads (no human step: they only
    bound latency). user_waiting + buffered -> provider_timeout; latency-critical (cited) -> service_tier "default". Flex, the
    account default, applies everywhere else. The keys it set last time (led["guardrails"]) are re-derived each run; keys from
    approved cards are never touched. Returns the files written."""
    from . import improve as imp, policy as pol
    want = {}
    for c in (led.get("claims") or {}).values():
        if c.get("outcome") != "migrated" or not c.get("feature"):
            continue
        e = {}
        if c.get("latency") == "user_waiting" and not c.get("stream"):
            e["provider_timeout"] = GUARD_TIMEOUT
        if c.get("latency_critical"):
            e["service_tier"] = "default"
        if e:
            want.setdefault(glob_for(c["feature"]), {}).update(e)
    prev = led.get("guardrails") or {}
    p, err = pol.load(root)
    if err or (not want and not prev):
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
    led["guardrails"] = {g: sorted(e) for g, e in want.items()}
    rts = imp.runtime_paths(root)
    if not rts or not pol.policy_valid(p):
        return []
    open(os.path.join(root, pol.POLICY_FILE), "w").write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    return [pol.POLICY_FILE] + imp._compile_everywhere(root, p, rts)


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


def cmd_run(a):
    """v4: record the baseline and hand the agent a proposal for every site. Nothing is applied to the code."""
    from . import cli
    root = os.path.abspath(a.path)
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode:
        return err(a.json, "%s is not a git repository" % root, "run `git init && git add -A && git commit -m base` first (the baseline is snapshotted with git)")
    prev = load_ledger(root)
    if prev and prev.get("floor") and not getattr(a, "force", False):
        wl = prev.get("worklist") or []
        left = [it for it in wl if not resolved(prev, it)]
        lines = ["%s; baseline tests: %s; test command: %s" % (
            "nothing applied by the tool: every edit is yours" if not prev["floor"].get("applied", True) else
            "floor applied (%d sites migrated by the codemod)" % prev["floor"].get("migrated", 0),
            _tc((prev.get("baseline") or {}).get("before")), (prev.get("baseline") or {}).get("test_cmd"))]
        if (prev.get("baseline") or {}).get("python"):
            lines.append("probe with this repo's Python: %s" % prev["baseline"]["python"])
        lines.append("to decide: %d of %d items (P = proposal for a site the rules found, H = hotspot no scanner site explains)" % (len(left), len(wl)))
        for it in wl:
            edit = (it.get("edit") or {}).get("cmd")
            lines.append("  %-4s %s %s:%s  %s  -- %s%s" % (it["id"], " " if it in left else "x", it["file"], it["line"], it["what"], it["why"][:140],
                                                       ("  [suggested: %s]" % edit) if edit else ""))
        lines.append("next: `check` each item, decide, make the edit (or not), prove what you claim, `claim` it; then `router-migrate finish`")
        rc = out({"ok": True, "worklist": wl, "open": [it["id"] for it in left]}, a.json, "\n".join(lines))
        return _run_agent_then_gate(root, a) if getattr(a, "agent", None) else rc
    t0 = time.time()
    typecheck_before = run_cmd(root, a.typecheck_cmd) if getattr(a, "typecheck_cmd", None) else None
    cli.do_scan(root, quiet=True)
    p0 = cli.do_plan(root, quiet=True)
    v = None
    if not a.skip_verify:
        v = cli.do_verify(root, a)
    tree = snapshot_tree(root)
    shadow = shadow_floor_run(root, v["test_cmd"], tree) if v and v.get("test_cmd") else None
    p = current_plan(root)
    proposals = build_proposals(root, p0)
    hs = hotspots(root, p["sites"] + [s for s in _scan(root)["sites"] if s["kind"] == "client"])
    hitems = [{"id": "H%d" % (i + 1), "type": "hotspot", "file": h["file"], "line": h["line"], "what": h["kind"],
               "why": "no scanner site explains: %s" % h["text"]} for i, h in enumerate(hs)]
    tc = (v or {}).get("test_cmd") or getattr(a, "test_cmd", None) or ""
    import re as _re
    py = _re.search(r"(\S+/bin/python[\d.]*)\b", tc)
    on_base, on_src, on_note = (shadow, "shadow floor", None) if shadow is not None else ((v or {}).get("sim_tests"), "source", None)
    before = (v or {}).get("tests_before") or {}
    if shadow is not None and shadow.get("passed") is None and shadow.get("exit") and before.get("passed") is not None:
        # The shadow run exists to excuse tests that assert on a mocked transport, not a crash: hold Router-on runs to the
        # Router-off baseline and tell the agent, because a suggested edit that crashes with Router on is a finding.
        on_base, on_src = before, "Router off (the Router-on run with the suggested edits crashed)"
        on_note = "the suggested edits crash the test command with Router on (exit %s): %s" % (shadow.get("exit"), _last_error(shadow.get("tail")))
    elif shadow is not None and _worse(before, shadow):
        on_note = "with Router on and the suggested edits, tests are %s vs %s with Router off: tests that mock the HTTP transport, " \
                  "or a suggested edit that changes behavior with Router on: %s" % (_tc(shadow), _tc(before), _last_error(shadow.get("tail")))
    led = load_ledger(root) or {}
    led.update({"version": 4, "root": root, "created": led.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "floor": {"tree": tree, "migrated": 0, "applied": False},
                "baseline": {"test_cmd": tc or None, "before": (v or {}).get("tests_before"), "after_floor": (v or {}).get("tests_before"),
                             "router_on_floor": on_base, "router_on_source": on_src, "router_on_note": on_note,
                             "typecheck_cmd": getattr(a, "typecheck_cmd", None), "python": py.group(1) if py else None,
                             "typecheck_before": typecheck_before and {"exit": typecheck_before["exit"], "errors": typecheck_before["ts_errors"]}},
                "proposals": proposals, "worklist": proposals + hitems, "claims": led.get("claims") or {}, "edits": led.get("edits") or [],
                "probes": led.get("probes") or {}, "confirmations": led.get("confirmations") or {}, "cards": led.get("cards") or [],
                "finish": {"attempts": 0, "history": []}})
    save_ledger(root, led)
    sm = p0["summary"]
    _event(root, "proposals", "done", {"sites": len(proposals), "hotspots": len(hitems), "proposed_migrated": sm["migrated"]},
           msg="tests %s" % _tc((v or {}).get("tests_before")) if v else "")
    lines = ["proposals: %d production sites (the rules would move %d; %d hit a hard limit), %d hotspots; nothing applied (%.0fs)" % (
        len(proposals), sm["migrated"], sum(1 for x in proposals if x["hard_limit"]), len(hitems), time.time() - t0)]
    if v:
        lines.append("baseline: tests %s | Router on, suggested edits applied then reverted: %s | test command: %s" % (
            _tc(v.get("tests_before")), _tc(shadow), v.get("test_cmd")))
        if on_note:
            lines.append("WARNING: %s" % on_note)
    if py:
        lines.append("probe with this repo's Python: %s" % py.group(1))
    lines.append("decide every item (the suggested edits are proposals; `.router-migrate/proposals.diff` shows them all):")
    for it in proposals + hitems:
        edit = (it.get("edit") or {}).get("cmd")
        lines.append("  %-4s %s:%s  %s  -- %s%s" % (it["id"], it["file"], it["line"], it["what"], it["why"][:140], ("  [suggested: %s]" % edit) if edit else ""))
    lines.append("next: `check` each item, decide, make the edit (or not), prove what you claim, `claim` it; then `router-migrate finish`")
    rc = out({"ok": True, "summary": sm, "worklist": led["worklist"], "baseline": led["baseline"]}, a.json, "\n".join(lines))
    return _run_agent_then_gate(root, a) if getattr(a, "agent", None) else rc


AGENT_TOOLS = ("Read,Grep,Glob,Edit,MultiEdit,TodoWrite,Task,Bash(router-migrate:*),Bash(git diff:*),Bash(git status:*),Bash(git log:*),"
               "Bash(ls:*),Bash(rg:*),Bash(grep:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(sed -n:*),Bash(wc:*)")


def launch_agent(root, agent="claude", model=None, timeout=2700, max_turns=250, phase="migrate"):
    """Run the coding agent headless: our prompt for the phase, a Stop hook that runs the phase's gate (`finish` for migrate,
    `improve --hook` for improve), tools limited to reading, editing and router-migrate. Nothing is written into the repo's
    .claude/ (settings come from a temp file). Returns {exit, usd, turns, minutes, transcript}."""
    if agent != "claude":
        raise SystemExit("only --agent claude is supported in Track 1 (Codex: follow AGENTS.md; `finish` is the gate)")
    here = os.path.dirname(os.path.abspath(__file__))
    rm_bin = os.path.join(os.path.dirname(here), "bin")
    rm = os.path.join(rm_bin, "router-migrate")
    if phase == "migrate":
        floor_applied = ((load_ledger(root) or {}).get("floor") or {}).get("applied", True)
        prompt = open(os.environ.get("ROUTER_MIGRATE_PROMPT") or os.path.join(here, "prompts", "migrate.md")).read() + (
            "\n\nThe floor has already been applied in this repository; start with `router-migrate run .`.\n" if floor_applied else
            "\n\nNo codemod has been applied in this repository: every edit is yours. Start with `router-migrate run .`.\n")
        hook = "%s finish %s --hook" % (rm, json.dumps(root))
    else:
        prompt = open(os.path.join(here, "prompts", "improve.md")).read() + "\n\nStart with `router-migrate improve .`.\n"
        hook = "%s improve %s --hook" % (rm, json.dumps(root))
    tmp = tempfile.mkdtemp(prefix="router-migrate-agent-")
    settings = os.path.join(tmp, "settings.json")
    json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 1800, "command": hook}]}]}}, open(settings, "w"))
    tools = AGENT_TOOLS if phase == "migrate" else AGENT_TOOLS.replace("Edit,MultiEdit,", "")  # improve only reads and judges
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--settings", settings,
           "--allowedTools", tools, "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ)
    env.pop("RAMP_ROUTER_API_KEY", None)  # the migration needs no live key; finish's Router-on run uses router-sim
    env["PATH"] = rm_bin + os.pathsep + env.get("PATH", "")
    log = sp(root, "agent_transcript.jsonl" if phase == "migrate" else "agent_improve_transcript.jsonl")
    t = time.time()
    with open(log, "w") as fh:
        try:
            code = subprocess.run(cmd, cwd=root, env=env, stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    res = {}
    for line in open(log, encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            res = ev
    return {"exit": code, "usd": res.get("total_cost_usd"), "turns": res.get("num_turns"), "minutes": round((time.time() - t) / 60, 1),
            "model": list((res.get("modelUsage") or {}).keys()), "final": (res.get("result") or "")[:4000],
            "terminal_reason": res.get("terminal_reason"), "transcript": log}


def _event(root, stage, status, counts=None, msg=""):
    from . import improve as imp
    imp.event(root, stage, status, counts or {}, msg=msg)


def _run_agent_then_gate(root, a):
    _event(root, "agent_migrate", "running")
    info = launch_agent(root, a.agent, getattr(a, "model", None), getattr(a, "agent_timeout", 2700))
    led = load_ledger(root)
    led.setdefault("cost", {})
    led["cost"].update({"agent_usd": info["usd"], "agent_minutes": info["minutes"], "agent_turns": info["turns"], "agent_model": info["model"],
                        "agent_exit": info["exit"]})
    save_ledger(root, led)
    _event(root, "agent_migrate", "done" if info["exit"] == 0 else "error",
           {"claims": len(led.get("claims") or {}), "turns": info["turns"] or 0, "usd": round(info["usd"] or 0, 2), "minutes": info["minutes"]})
    fa = argparse_ns(path=root, hook=False, max_attempts=3, json=False)
    print("agent: %s, %s turns, %.1f min, $%s" % (info["exit"], info["turns"], info["minutes"], info["usd"]))
    rc = cmd_finish(fa)
    led = load_ledger(root)
    fin = led.get("finish") or {}
    _event(root, "gate", "done" if rc == 0 else "error", {k: v for k, v in (led.get("result") or {}).items() if isinstance(v, int)},
           msg="reverted agent edits to the floor" if fin.get("reverted") else "")
    if rc != 0 or getattr(a, "skip_improve", False):
        return rc
    return _improve_phase(root, a)


def _improve_phase(root, a):
    """After the gate passes: deterministic cards from the migrated code, an agent pass that replaces filename heuristics with
    judgments (latency, task, cacheability) recorded with evidence, the final cards, REPORT.html, then the live dashboard."""
    p0 = do_improve(root)
    feats = p0["features_to_judge"]
    led = load_ledger(root)
    led.setdefault("improve", {})["features"] = len(p0["features"])
    save_ledger(root, led)
    if feats:
        _event(root, "agent_improve", "running", {"features": len(feats)})
        info = launch_agent(root, a.agent, getattr(a, "model", None), min(getattr(a, "agent_timeout", 2700), 1500), max_turns=120, phase="improve")
        led = load_ledger(root)
        led.setdefault("improve", {})["cost"] = {"usd": info["usd"], "minutes": info["minutes"], "turns": info["turns"], "exit": info["exit"]}
        save_ledger(root, led)
        print("improve agent: %s, %s turns, %.1f min, $%s" % (info["exit"], info["turns"], info["minutes"], info["usd"]))
        _event(root, "agent_improve", "done" if info["exit"] == 0 else "error",
               {"judged": len(led.get("judgments") or {}), "turns": info["turns"] or 0, "usd": round(info["usd"] or 0, 2)})
    else:
        _event(root, "agent_improve", "skipped", msg="no migrated features to judge")
    final = do_improve(root)
    r = final["recommendations"]
    _event(root, "improve", "done", {"cards": len(r["cards"]), "considered": len(r["considered_not_recommended"])})
    print("improve: %d cards across %d levers; %d considered and not recommended" % (
        len(r["cards"]), len({c["rule"].split(".")[0] for c in r["cards"]}), len(r["considered_not_recommended"])))
    for c in r["cards"][:12]:
        print("  %-34s %s" % (c["rule"], c["title"][:110]))
    return open_dashboard(root, a)


def open_dashboard(root, a):
    """Write the frozen REPORT.html, then serve the live dashboard (127.0.0.1, session token) unless --no-dashboard."""
    from . import improve as imp
    from .dashboard import render as dash_render
    html = dash_render.render_report(imp.bundle(root, repo=os.path.basename(root)))
    open(sp(root, "REPORT.html"), "w").write(html)
    print("report: %s" % sp(root, "REPORT.html"))
    if getattr(a, "no_dashboard", False):
        return 0
    srv, url = serve_dashboard(root, open_browser=not getattr(a, "no_browser", False))
    _event(root, "dashboard", "done", msg=url.split("?")[0])
    print("router-migrate dashboard: %s  (Ctrl-C to stop)" % url, flush=True)
    import threading
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


def serve_dashboard(root, open_browser=False):
    """The live dashboard's server: Approve/Skip write decisions.json; Apply runs `apply --decisions` plus the quick gate.
    Returns (server, url with session token); stop with server.shutdown()."""
    from . import improve as imp
    from .dashboard import server as dash_server
    from .catalog import Catalog as _Cat

    def on_decide(payload):
        return imp.decide(root, payload["card_id"], payload["action"], reason=payload.get("reason"), until=payload.get("until"), by="ui")

    def on_apply():
        out = imp.apply_decisions(root, _Cat.load())
        led = load_ledger(root)
        ok, fails, _ = gate(root, led, quick=True) if led else (True, [], {})
        do_improve(root)
        lines = ["applied %d card(s), skipped %d%s%s" % (len(out["applied"]), len(out["skipped"]),
                                                        ", %d applied earlier" % len(out["already_applied"]) if out.get("already_applied") else "",
                                                        "; policy %s" % out["policy_hash"][:8] if out.get("policy_hash") else "")]
        lines += ["  applied %s (%s)" % (x["card"], x["rule"]) for x in out["applied"]]
        lines += ["  skipped %s: %s" % (x["card"], x["reason"]) for x in out["skipped"]]
        lines += ["  warning %s" % w for w in out.get("warnings") or []]
        lines.append("quick gate (parse, scope): %s" % ("ok" if ok else "; ".join(f["detail"] for f in fails)))
        lines.append("next: router-migrate finish %s  (full gate: tests vs baseline, Router-on run)" % root)
        return {"command": "router-migrate apply --decisions %s" % root, "output": "\n".join(lines)}

    return dash_server.serve(lambda: imp.bundle(root, repo=os.path.basename(root)), on_decide, on_apply, open_browser=open_browser)


# ------------------------------------------------------------------------------------------------ improve (v3)
JUDGE_KEYS = {"latency": "interactive | user_waiting | background", "task": "classification | extraction | routing | summarization | "
              "chat | generation | agentic | reasoning | other", "cacheable": "estimated tokens of the static prompt prefix sent on every call (0 if none)",
              "model": "the model id this feature uses in production by default (follow config/env defaults), when the tool can't resolve it"}


def plan_for_improve(root):
    """The plan Improve works from: the code as it stands after the agent (measure-only), with the agent's claims overriding
    outcomes (a site the agent showed stays on its provider is not improved as if it moved)."""
    p = current_plan(root)
    led = load_ledger(root) or {}
    claims = list((led.get("claims") or {}).values())
    for s in p["sites"]:
        c = [x for x in claims if x["file"] == s["file"] and abs(int(x["line"]) - int(s["line"])) <= 3]
        if c and c[0]["outcome"] != "migrated":
            out_ = "kept" if c[0]["outcome"] == "not_a_site" else c[0]["outcome"]
            s["agent_note"] = "%s %s" % (c[0].get("reason_code") or "", c[0].get("why", "")[:200])
            if out_ != s["outcome"]:  # the agent overrode the tool: its reason replaces the tool's
                s["outcome"], s["reason"] = out_, "agent: " + s["agent_note"]
            # same outcome: keep the tool's reason (rules such as unlock.vendor_endpoint read it)
    v1 = json.load(open(sp(root, "plan.json"))) if os.path.exists(sp(root, "plan.json")) else {}
    p["patches"] = v1.get("patches") or []
    p["summary"] = planmod.summarize(p["sites"])
    return p


def do_improve(root):
    from . import improve as imp
    led = load_ledger(root) or {}
    p = plan_for_improve(root)
    json.dump(p, open(sp(root, "plan_v3.json"), "w"), indent=1, default=str)
    r = imp.recommend(root, Catalog.load(), repo=os.path.basename(root), plan=p, judgments=led.get("judgments") or {}, current_lines=True)
    feats = {}
    for sid, sg in r["signals"].items():
        s = next((x for x in p["sites"] if x["id"] == sid), None)
        if s and s.get("prod") and s["outcome"] == "migrated":
            feats.setdefault(sg["feature"], {"feature": sg["feature"], "file": s["file"], "line": s["line"], "model": sg.get("resolved_model"),
                                              "model_needed": not sg.get("resolved_model") and sg["sources"].get("resolved_model") != "agent",
                                              "latency_heuristic": sg["latency_class"] if sg["sources"]["latency_class"] == "heuristic" else None})
    judged = led.get("judgments") or {}
    need = lambda f: ("latency", "task") + (("model",) if f["model_needed"] else ())
    todo = [f for f in feats.values() if not all(k in (judged.get(f["feature"]) or {}) for k in need(f))]
    return {"recommendations": r, "features": list(feats.values()), "features_to_judge": todo}


def cmd_judge(a):
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE (the call site of the feature), got %r" % a.loc, "use a line from `router-migrate improve .`")
    if a.key not in JUDGE_KEYS:
        return err(a.json, "KEY must be one of %s" % ", ".join(JUDGE_KEYS), "; ".join("%s = %s" % kv for kv in JUDGE_KEYS.items()))
    from . import recommend as rec
    v = a.value.strip().lower()
    if a.key == "latency" and v not in rec.LATENCY:
        return err(a.json, "latency must be one of %s" % ", ".join(rec.LATENCY), "background = nobody waits on the reply (jobs, cron, queues, batch)")
    if a.key == "task" and v not in rec.TASKS:
        return err(a.json, "task must be one of %s" % ", ".join(rec.TASKS), "pick the closest; `other` is allowed")
    if a.key == "model":
        v = a.value.strip()
        rid = Catalog.load().resolve(v)[0]
        if not rid:
            print("note: %r is not a Router catalog id; recorded, but no model-dependent card (Flex, fallbacks, model recs) applies" % v)
    if a.key == "cacheable" and not v.replace(",", "").isdigit():
        return err(a.json, "cacheable takes an estimated token count of the static prompt prefix (0 if none)", "count ~4 characters per token of the prompt text that is the same on every call")
    if not a.why:
        return err(a.json, "--why is required", "one sentence citing what you read")
    ev = _resolve_evidence(root, a.evidence or [])
    if isinstance(ev, str):
        return err(a.json, ev, "cite real lines, e.g. `--evidence app/tasks.py:30`")
    if not ev:
        return err(a.json, "at least one --evidence FILE:LINE is required", "cite the decorator, caller, queue or prompt lines that justify it")
    feat = _feature(root, rel, line)
    led.setdefault("judgments", {}).setdefault(feat, {})[a.key] = {"value": v.replace(",", ""), "why": a.why, "evidence": ev,
                                                                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_ledger(root, led)
    left = [f["feature"] for f in do_improve(root)["features_to_judge"]]
    return out({"ok": True, "feature": feat, "key": a.key, "value": v, "left": left}, a.json,
               "judged %s %s=%s\nfeatures still missing judgments: %d%s" % (feat, a.key, v, len(left), "" if left else "; next: `router-migrate improve .`"))


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


def cmd_improve(a):
    root = ledger_root(a.path)
    if a.hook:
        try:
            json.loads(sys.stdin.read() or "{}")
        except ValueError:
            pass
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` (and `finish`) first")
    if getattr(a, "agent", None):
        return _improve_phase(root, a)
    res = do_improve(root)
    r, todo = res["recommendations"], res["features_to_judge"]
    led = load_ledger(root)
    imp_state = led.setdefault("improve", {})
    if a.hook:
        imp_state["hook_blocks"] = imp_state.get("hook_blocks", 0) + (1 if todo else 0)
        save_ledger(root, led)
        if todo and imp_state["hook_blocks"] <= 3:
            print(json.dumps({"decision": "block", "reason": "router-migrate improve: %d feature(s) still need latency and task judgments: %s. "
                              "Run `router-migrate judge FILE:LINE latency|task|cacheable VALUE --why ... --evidence FILE:LINE` for each." % (
                                  len(todo), ", ".join("%s (%s:%s)" % (f["feature"], f["file"], f["line"]) for f in todo[:10]))}))
        else:
            print(json.dumps({"systemMessage": "router-migrate improve: %d cards" % len(r["cards"])}))
        return 0
    lines = ["improve: %d migrated feature(s); %d card(s) across levers; %d considered and not recommended" % (
        len(res["features"]), len(r["cards"]), len(r["considered_not_recommended"]))]
    if todo:
        lines.append("features that need your judgments (latency and task always; model when the tool can't resolve it; cacheable when "
                     "there is a long static prompt):")
        for f in todo:
            lines.append("  %s  at %s:%s  model %s%s" % (f["feature"], f["file"], f["line"],
                                                        f["model"] or "UNKNOWN to the tool: judge `model` from the config/env default",
                                                        "  (heuristic latency: %s)" % f["latency_heuristic"] if f["latency_heuristic"] else ""))
        lines.append("next: read each feature's callers and prompt, then `router-migrate judge FILE:LINE latency VALUE --why ... --evidence FILE:LINE`"
                     " (and task, and cacheable); keys: " + "; ".join("%s = %s" % kv for kv in JUDGE_KEYS.items()))
    for c in r["cards"]:
        lines.append("  card %-32s %s" % (c["rule"], c["title"][:120]))
    return out({"ok": not todo, "features": res["features"], "features_to_judge": todo, "cards": len(r["cards"])}, a.json, "\n".join(lines))


def argparse_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


def cmd_check(a):
    root = ledger_root(a.path)
    rel, line = parse_loc(a.loc)
    if not rel:
        return err(a.json, "expected FILE:LINE, got %r" % a.loc, "e.g. `router-migrate check app/llm.py:42`")
    if not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "no such file: %s" % rel, "paths are relative to the repo root (%s)" % root)
    p = current_plan(root)
    f = facts_for(root, rel, line, p)
    f["next"] = next_for(root, f, p)
    led = load_ledger(root)
    fl, ln = (f["file"], int(f["line"])) if f["found"] else (rel, line)
    f["dynamic"] = dynamic_confirmed(root, led, fl, ln) if led else None
    f["native"] = native_eligibility(root, p, f["client"]) if f.get("client") else (False, "no client construction linked")
    prop = [x for x in (led or {}).get("proposals") or [] if x["file"] == fl and abs(int(x["line"]) - ln) <= 3]
    f["proposal"] = prop[0] if prop else None
    if led is not None:
        led.setdefault("facts", {})["%s:%d" % (rel, line)] = dict(f, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        save_ledger(root, led)
    if f["found"]:
        txt = "%s:%s %s %s -> %s%s\n  why: %s" % (f["file"], f["line"], f["sdk"], f["surface"], "ROUTES" if f["routes"] else "does not route",
                                                 "" if f["routes"] else " (%s)" % f["outcome"], f["reason"])
        if f["hard_limit"]:
            txt += "\n  HARD LIMIT: %s" % HARD_LIMITS[f["hard_limit"]]
    else:
        txt = "%s:%d: %s" % (rel, line, f["why"])
    if f["proposal"]:
        txt += "\n  proposal %s: %s%s" % (f["proposal"]["id"], f["proposal"]["proposal"], ("; suggested edit: %s" % f["proposal"]["edit"]["cmd"])
                                         if f["proposal"]["edit"].get("cmd") else "")
    txt += "\n  evidence: static %s; dynamic %s" % ("yes (the code routes it as written)" if f.get("routes") else "no",
                                                   f["dynamic"] or "none yet (`router-migrate probe %s:%d --cmd ...`)" % (fl, ln))
    if f.get("client"):
        txt += "\n  native (base_url + key, no runtime): %s: %s" % ("eligible" if f["native"][0] else "no", f["native"][1])
    txt += "\n  next: %s" % f["next"]
    return out(dict(f, ok=True), a.json, txt)


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
                    return "probe %s does not exist (run `router-migrate probe` first)" % item
                ev.append(item)
            elif item:
                lines.append(item)
    r = _resolve_evidence(root, lines)
    return r if isinstance(r, str) else ev + r


def _is_native(p, f):
    c = (p.get("clients") or {}).get(f.get("client") or "")
    return bool(c) and planmod.client_target(c) == "router"


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
        return err(a.json, "no ledger", "run `router-migrate run .` first")
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
    return out({"ok": True, "file": rel, "undone": done, "diff": d}, a.json, d + "next: the calls this client serves no longer route; claim them")


def cmd_probe(a):
    """Run any command with the runtime pointed at router-sim (provider keys dummied, outbound HTTP blocked) and record which
    features sent valid Router requests. A valid request tagged with a site's feature confirms that site dynamically."""
    from .sim.server import serve
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    locs = _locs(a.loc) if a.loc else []
    if locs is None:
        return err(a.json, "expected FILE:LINE[,FILE:LINE...], got %r" % a.loc, "e.g. `router-migrate probe app/llm.py:42 --cmd \"...\"`")
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


def cmd_card(a):
    """Propose a lossy or business move for a human to approve (never an edit): a model swap, another vendor's endpoint,
    per-user keys (route only the operator's key), or dropping beta flags. `apply --decisions` applies approved cards."""
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    rel, line = parse_loc(a.loc)
    if not rel or not os.path.exists(os.path.join(root, rel)):
        return err(a.json, "expected an existing FILE:LINE, got %r" % a.loc, "the call site the card is about")
    if a.kind not in CARD_KINDS:
        return err(a.json, "--kind must be one of %s" % ", ".join(CARD_KINDS), "; ".join("%s = %s" % kv for kv in CARD_KINDS.items()))
    if not a.why:
        return err(a.json, "--why is required", "what the human gains and loses, citing what you read")
    ev = _evidence(root, led, a.evidence)
    if isinstance(ev, str) or not ev:
        return err(a.json, ev or "at least one --evidence is required", "cite the lines that justify the card")
    p = current_plan(root)
    f = facts_for(root, rel, line, p)
    feat = _feature(root, f["file"] if f.get("found") else rel, int(f["line"]) if f.get("found") else line)
    client = (p.get("clients") or {}).get(f.get("client") or "")
    cat = Catalog.load()
    if a.kind == "model_swap":
        frm = a.from_model or ((f.get("model") or {}).get("value") if (f.get("model") or {}).get("static") else None)
        if not a.to or cat.resolve(a.to)[0] != a.to:
            return err(a.json, "--to must be an exact Router catalog id (got %r)" % a.to, "pick the replacement model from the catalog")
        if not frm:
            return err(a.json, "the call's model is not a static literal", "pass --from MODEL (the model the code uses in production)")
        change = {"kind": "policy", "diff": json.dumps({"globs": {glob_for(feat): {"model_map": {frm: a.to}}}}), "file": None}
        loses = ["%s answers instead of %s for %s (quality, cost and behavior change)" % (a.to, frm, feat)]
    else:
        if not client:
            return err(a.json, "no client construction is linked to %s:%d" % (rel, line), "point at a call whose client the tool can see")
        wrap = {"openai": "wrap_openai", "anthropic": "wrap_anthropic"}.get(planmod._family(client["sdk"]), "wrap_langchain")
        if a.kind == "vendor_endpoint":
            host = planmod._host((client.get("base_url") or {}).get("value") or "")
            if not host or not (client.get("base_url") or {}).get("static"):
                return err(a.json, "the client's base URL is not a static host", "vendor_endpoint needs a literal base URL")
            opts, loses = {"unlock_hosts": [host]}, ["%s's own serving (its rate limits, regions, vendor-only features)" % host,
                                                     "requests are billed by Router instead of %s" % host]
        elif a.kind == "per_user_key":
            opts, loses = {"operator_key_only": True}, ["only requests made with the operator's provider key go to Router; users' "
                                                        "own keys stay on the provider (and stay billed to them)"]
        else:
            betas = [b.strip() for b in (a.betas or "").split(",") if b.strip()]
            if not betas:
                return err(a.json, "--betas is required for beta_flags", "list the anthropic-beta flags that may be dropped")
            wrap, opts, loses = "wrap_anthropic", {"drop_betas": betas}, ["the beta features: %s" % ", ".join(betas)]
        change = {"kind": "code", "file": client["file"], "line": int(client["line"]), "wrap": wrap, "opts": opts,
                  "diff": "%s(...) at %s:%s with %s" % (wrap, client["file"], client["line"], json.dumps(opts))}
    led = load_ledger(root)
    card = {"id": "AC%d" % (len(led.get("cards") or []) + 1), "kind": a.kind, "site": "%s:%s" % (rel, line), "feature": feat, "why": a.why,
            "evidence": ev, "change": change, "loses": loses, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    led.setdefault("cards", []).append(card)
    save_ledger(root, led)
    return out({"ok": True, "card": card}, a.json, "card %s (%s) proposed for a human: %s\n  loses: %s\n  next: claim the site's outcome "
               "(a card is for a product decision; for an acceptable cost, `adapt` takes the trade-off now)" % (card["id"], a.kind, change["diff"], "; ".join(loses)))


def cmd_wrap(a):
    from . import apply_py, cli
    from .runtime import render
    root = ledger_root(a.path)
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
                           "edit by hand to pass the runtime's client/fetch in (e.g. http_client=router_runtime.http_client()), then prove it with `probe`")
            targets = {(int(c[0]["line"]), int(c[0]["col"])): (kind, opts)}
        else:
            targets = {(line, None): ("wrap_openai", opts)}
            if re.search(r"\bAsyncAnthropic\(|\bAnthropic\(", before.splitlines()[line - 1] if line <= len(before.splitlines()) else ""):
                targets = {(line, None): ("wrap_anthropic", None)}
        changed, done, skipped, rt_rel = apply_py.patch_file(root, rel, targets, installs)
        if skipped:
            return err(a.json, "; ".join(skipped.values()), "check the line points at the constructor call itself, or edit by hand and `probe`")
        if not done and not changed:
            return err(a.json, "no OpenAI/Anthropic/LangChain/LlamaIndex client construction found at %s:%d" % (rel, line),
                       "point at the line where the constructor call starts; for anything else edit by hand and prove it with `probe`")
        if rt_rel and not os.path.exists(os.path.join(root, rt_rel)):
            open(os.path.join(root, rt_rel), "w").write(render("router_runtime.py", cat))
    else:
        if native:
            return err(a.json, "the native codemod is Python-only", "edit by hand (baseURL and apiKey from RAMP_ROUTER_BASE_URL / "
                       "RAMP_ROUTER_API_KEY when the key is set, the originals otherwise), then prove each site with `probe`")
        if not c:
            return err(a.json, "the scanner sees no client construction at %s:%d" % (rel, line),
                       "point at `new OpenAI(`/`new Anthropic(`/`createOpenAI(`/`createAnthropic(`; elsewhere pass `routerFetch()` by hand and `probe`")
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
            rf.write(render("routerRuntime.ts", cat))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
            json.dump([c[0]["id"]], sf)
        r = cli.run_ts(["apply", root, "--runtime", rf.name, "--sites", sf.name])
        bad = [e for e in r.get("edits", []) if not e.get("ok")]
        if bad:
            return err(a.json, bad[0].get("reason") or "TS wrap failed", "edit by hand to pass `routerFetch()` in, then prove it with `probe`")
    after = open(os.path.join(root, rel), encoding="utf-8").read()
    import difflib
    d = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/" + rel, "b/" + rel))
    led = load_ledger(root)
    led["edits"].append({"by": "agent-wrap", "file": rel, "line": line, "native": native, "changed": before != after, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    if before != after:
        _remap_lines(led, rel, before, after)
    save_ledger(root, led)
    msg = (d or "already wrapped: nothing to change\n") + ("next: native clients have no runtime fallback: `probe` every site it serves"
                                                          if native else "next: `check` the calls this client serves, then claim them")
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


def cmd_claim(a):
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
    locs = _locs(a.loc)
    if not locs or any(not os.path.exists(os.path.join(root, r)) for r, _ in locs):
        return err(a.json, "expected existing FILE:LINE[,FILE:LINE...], got %r" % a.loc, "use the line where each call or construction starts")
    a.outcome = a.outcome or a.outcome_pos
    if a.outcome not in OUTCOMES:
        return err(a.json, "--outcome must be one of %s" % ", ".join(OUTCOMES), "migrated, or the outcome your evidence supports")
    if not a.why:
        return err(a.json, "--why is required", "say in one sentence why, citing what you read or ran")
    ev = _evidence(root, led, a.evidence)
    if isinstance(ev, str):
        return err(a.json, ev, "cite real lines (`--evidence config.py:12`) or probe ids (`--evidence PR3`)")
    if not ev:
        return err(a.json, "at least one --evidence FILE:LINE or probe id is required", "cite the lines or probe that justify the outcome")
    if a.latency and a.latency not in ("interactive", "user_waiting", "background"):
        return err(a.json, "--latency must be interactive, user_waiting or background", "who waits on this call?")
    p = current_plan(root)
    done, notes = [], []
    for rel, line in locs:
        f = facts_for(root, rel, line, p)
        if a.outcome == "migrated" and f.get("hard_limit"):
            return err(a.json, "%s:%s can't be migrated: %s" % (f["file"], f["line"], HARD_LIMITS[f["hard_limit"]]),
                       "hard limits are physical: claim `kept --reason %s`" % ("R_NO_ENDPOINT" if f["hard_limit"] == "no_endpoint" else "R_STATEFUL"))
        fl, ln = (f["file"], int(f["line"])) if f.get("found") else (rel, line)
        confirmed = None
        if a.outcome == "migrated":
            native = _is_native(p, f) if f.get("found") else False
            dyn = dynamic_confirmed(root, led, fl, ln)
            confirmed = "dynamic" if dyn else ("static" if f.get("found") and f["routes"] and not native else None)
            if not confirmed:
                notes.append("%s:%d unconfirmed: %s" % (fl, ln, "a native client has no runtime fallback, so it needs a valid probe" if native else
                                                        "prove it routes (`router-migrate probe %s:%d --cmd \"<run this code path>\"`)" % (fl, ln)))
        key = "%s:%d" % (fl, ln)
        feat = _feature(root, fl, ln)
        led["claims"][key] = {"file": fl, "line": ln, "outcome": a.outcome, "reason_code": a.reason if a.outcome != "migrated" else None,
                              "why": a.why, "evidence": ev, "tool_outcome": f.get("outcome"), "tool_reason": f.get("reason"), "verdict": f.get("verdict"),
                              "condition": f.get("condition"), "origin": "scanner" if f.get("found") else "agent", "confirmed": confirmed,
                              "latency": a.latency, "latency_critical": bool(a.latency_critical), "stream": f.get("stream"),
                              "feature": feat, "by": "agent", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if a.latency and a.outcome == "migrated":
            led.setdefault("judgments", {}).setdefault(feat, {})["latency"] = {"value": a.latency, "why": a.why, "evidence": [e for e in ev if ":" in e],
                                                                               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        done.append(key)
    save_ledger(root, led)
    left = [it for it in led.get("worklist", []) if not resolved(led, it)]
    return out({"ok": True, "claimed": done, "notes": notes, "worklist_left": len(left)}, a.json,
               "claimed %s %s\n%sto decide: %d item(s) left%s" % (", ".join(done), a.outcome, "".join(n + "\n" for n in notes), len(left),
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


def gate(root, led, quick=False):
    """v4 gate, cheapest first: parse, scope + invariants, guardrails, typecheck and tests vs baseline, the Router-on run.
    Returns (ok, failures, info). Hard limits only: everything here is something Router or the build physically can't take."""
    from .verify import syntax_check, SIM_KEY
    fails, info = [], {}
    changes = changed_since(root, led["floor"]["tree"])
    changed = [p for st, p in changes if st != "D"]
    info["agent_changed_files"] = [p for _, p in changes]
    sx = syntax_check(root, changed)
    for x in sx["failed"]:
        fails.append({"gate": "parse", "detail": "%s: %s" % (x["file"], x["error"][-300:]), "next": "fix the syntax error in %s" % x["file"]})
    linked = {c["file"] for c in led["claims"].values()} | {e.get("file") for e in led["edits"] if e.get("by") in ("agent-wrap", "agent-unwrap")} | \
        {f for e in led["edits"] if e.get("by") in ("decisions", "guardrails", "tradeoff", "tradeoff-undo") for f in e.get("files") or []} | {"router.policy.json"}
    for st, path in changes:
        if st == "D":
            fails.append({"gate": "scope", "detail": "%s was deleted" % path, "next": "restore it: deleting files is out of scope"})
        elif not scan_py.is_prod(path) or re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[jt]sx?$", path):
            fails.append({"gate": "scope", "detail": "test file %s was edited" % path,
                          "next": "revert it: tests prove nothing broke, so they are never edited (`git checkout -- %s`)" % path})
        elif os.path.basename(path) in LOCKFILES or path.startswith((".github/", ".gitlab")):
            fails.append({"gate": "scope", "detail": "%s was edited" % path, "next": "revert it: dependency and CI files are out of scope"})
        elif not RUNTIME_FILES.search(path) and path not in linked and not path.endswith((".md", ".example")):
            fails.append({"gate": "scope", "detail": "%s changed but no claim links it to a site" % path,
                          "next": "claim the site you changed in it (`router-migrate claim %s:LINE ...`) or revert the file" % path})
    gone = provider_key_removals(root, led["floor"]["tree"])
    if gone:
        fails.append({"gate": "invariant", "detail": "provider key reference(s) removed: %s" % ", ".join(gone),
                      "next": "put them back: traffic Router can't serve still needs the provider key"})
    if fails:
        return False, fails, info
    if quick:
        return True, [], info
    files = compile_guardrails(root, led) + compile_tradeoffs(root, led)
    if files:
        led["edits"].append({"by": "guardrails", "files": files, "guardrails": led.get("guardrails"), "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        info["guardrails"] = led.get("guardrails")
    base = led.get("baseline") or {}
    if base.get("typecheck_cmd"):
        tc = run_cmd(root, base["typecheck_cmd"])
        info["typecheck"] = {"exit": tc["exit"], "errors": tc["ts_errors"]}
        tb = base.get("typecheck_before") or {}
        if tc["ts_errors"] > (tb.get("errors") or 0) or (tc["exit"] and not tb.get("exit")):
            fails.append({"gate": "typecheck", "detail": "%d type errors (baseline %s)\n%s" % (tc["ts_errors"], tb.get("errors"), tc["tail"][-800:]),
                          "next": "fix the new type errors in the files you changed"})
            return False, fails, info
    cmd = base.get("test_cmd")
    if cmd:
        t = run_cmd(root, cmd)
        info["tests"] = {k: t.get(k) for k in ("passed", "failed", "errors", "exit", "seconds")}
        if _worse(base.get("before"), t):
            fails.append({"gate": "tests", "detail": "tests now %s, baseline %s\n%s" % (_tc(t), _tc(base.get("before")), t["tail"][-1000:]),
                          "next": "fix what your edits broke (never edit the tests); if you can't, undo the edit and claim the site needs_human"})
            return False, fails, info
        from .sim.server import serve
        srv, sim, sbase = serve()
        try:
            t2 = run_cmd(root, cmd, env={"RAMP_ROUTER_API_KEY": SIM_KEY, "RAMP_ROUTER_BASE_URL": sbase})
            recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
            info["sim"] = {"requests": len(recs), "valid": sum(1 for x in recs if x.get("valid")),
                           "features": sorted({x.get("feature") for x in recs if x.get("feature")})[:50]}
            info["confirmed_by_tests"] = confirm_from_records(root, led, recs, "router-on test run", p=current_plan(root))
            bad = [x for x in recs if not x.get("valid")]
            if bad:
                fails.append({"gate": "sim", "detail": "router-sim rejected %d request(s): %s" % (len(bad), "; ".join(
                    "%s %s" % (x.get("feature"), x.get("error")) for x in bad[:3])), "next": "a request your edit sends to Router is invalid there; "
                    "fix the edit or undo it"})
                return False, fails, info
            info["tests_router_on"] = {k: t2.get(k) for k in ("passed", "failed", "errors", "exit")}
            if _worse(base.get("router_on_floor"), t2):
                fails.append({"gate": "sim", "detail": "tests with Router on (router-sim) now %s; baseline (%s) %s\n%s" % (
                    _tc(t2), base.get("router_on_source") or "shadow floor", _tc(base.get("router_on_floor")), t2["tail"][-800:]),
                              "next": "your edit changes behavior when Router is on; fix it or undo it"})
                return False, fails, info
        finally:
            srv.shutdown()
    return True, [], info


def cmd_finish(a):
    root = ledger_root(a.path)
    if a.hook:
        try:
            json.loads(sys.stdin.read() or "{}")
        except ValueError:
            pass
    led = load_ledger(root)
    if led is None:
        return _finish_out(a, False, "no ledger: run `router-migrate run .` first, then decide every item", [])
    ok, fails, info = gate(root, led)
    p = current_plan(root)
    for c in led["claims"].values():
        if c["outcome"] != "migrated":
            continue
        f = facts_for(root, c["file"], c["line"], p)
        if f.get("found") and f.get("hard_limit"):
            fails.append({"gate": "hard_limit", "detail": "%s:%s is claimed migrated but %s" % (c["file"], c["line"], HARD_LIMITS[f["hard_limit"]]),
                          "next": "claim it kept"})
            continue
        native = _is_native(p, f) if f.get("found") else False
        dyn = dynamic_confirmed(root, led, c["file"], c["line"])
        c["confirmed"] = "dynamic" if dyn else ("static" if f.get("found") and f["routes"] and not native else None)
        c["confirmed_by"] = dyn or ("static" if c["confirmed"] else None)
        if not c["confirmed"]:
            fails.append({"gate": "evidence", "detail": "%s:%s is claimed migrated with no evidence it routes%s" % (
                c["file"], c["line"], " (native: no runtime fallback, so only a valid request proves it)" if native else ""),
                          "next": "prove it: `router-migrate probe %s:%s --cmd \"<run this code path>\"`, or claim what is true" % (c["file"], c["line"])})
    left = [it for it in led.get("worklist", []) if not resolved(led, it)]
    if left:
        fails.append({"gate": "decisions", "detail": "%d item(s) have no decision: %s" % (len(left), ", ".join(
            "%s %s:%s" % (it["id"], it["file"], it["line"]) for it in left[:12])), "next": "decide each one (claim it; needs_human is allowed)"})
    ok = not fails
    fin = led.setdefault("finish", {"attempts": 0, "history": []})
    fin["attempts"] = fin.get("attempts", 0) + (0 if ok else 1)
    fin["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "gates": [f["gate"] for f in fails], "info": info})
    msg_extra = ""
    if not ok and fin["attempts"] >= a.max_attempts:
        hard = [f for f in fails if f["gate"] in ("parse", "scope", "invariant", "typecheck", "tests", "sim")]
        if hard:
            changes = changed_since(root, led["floor"]["tree"])
            restore(root, led["floor"]["tree"], changes)
            for c in led["claims"].values():
                if c["outcome"] == "migrated":
                    c["outcome"], c["reason_code"], c["confirmed"] = "needs_human", "R_GATE_FAILED", None
                    c["why"] = "reverted after %d failed gate runs: " % fin["attempts"] + c["why"]
            fin["reverted"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": [pth for _, pth in changes], "last_failures": fails}
            msg_extra = "reverted every edit after %d failed gate runs (%s); migrated claims are now needs_human" % (
                fin["attempts"], ", ".join(sorted({f["gate"] for f in hard})))
        for it in [it for it in led.get("worklist", []) if not resolved(led, it)]:
            led["claims"]["%s:%s" % (it["file"], it["line"])] = {"file": it["file"], "line": it["line"], "outcome": "needs_human", "reason_code": "R_UNDECIDED",
                                                                 "why": "undecided when the gate attempts ran out", "evidence": [], "by": "finish",
                                                                 "origin": "scanner" if it["type"] == "site" else "hotspot", "confirmed": None}
        ok, fails = True, []
        msg_extra = msg_extra or "gate attempts ran out; undecided items are needs_human, unproven migrated claims are reported unconfirmed"
    led["result"] = summarize(led, p)
    save_ledger(root, led)
    return _finish_out(a, ok, msg_extra, fails, led["result"])


def summarize(led, p):
    cl = list(led["claims"].values())
    by = {}
    for c in cl:
        by[c["outcome"]] = by.get(c["outcome"], 0) + 1
    mig = [c for c in cl if c["outcome"] == "migrated"]
    return {"claims": by, "migrated": len(mig), "migrated_confirmed_static": sum(1 for c in mig if c.get("confirmed") == "static"),
            "migrated_confirmed_dynamic": sum(1 for c in mig if c.get("confirmed") == "dynamic"),
            "migrated_unconfirmed": sum(1 for c in mig if not c.get("confirmed")),
            "overridden_proposals": sum(1 for c in cl if c.get("tool_outcome") and c["outcome"] != c.get("tool_outcome") and c.get("by") == "agent"),
            "unwraps": sum(1 for e in led.get("edits") or [] if e.get("by") == "agent-unwrap"),
            "native": sum(1 for e in led.get("edits") or [] if e.get("by") == "agent-wrap" and e.get("native")),
            "probes": len(led.get("probes") or {}), "cards": len(led.get("cards") or []), "guardrails": led.get("guardrails") or {}}


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

POLICY_LEVERS = ("drop_params", "model_map")
CLIENT_LEVERS = ("unlock_hosts", "operator_key_only", "drop_betas")


def compile_tradeoffs(root, led):
    """The policy half of every live trade-off (drop_params, model_map), compiled into router.policy.json and the runtime's
    policy files. Keys set last time (led["tradeoff_policy"]) are re-derived each run, so an undone trade-off disappears;
    keys from approved cards and guardrails are never touched. Returns the files written."""
    from . import improve as imp, policy as pol
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
    rts = imp.runtime_paths(root)
    if not rts or not pol.policy_valid(p):
        return []
    open(os.path.join(root, pol.POLICY_FILE), "w").write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    return [pol.POLICY_FILE] + imp._compile_everywhere(root, p, rts)


def _client_of(root, rel, line, p):
    """(client record, construction FILE:LINE) for the site at rel:line, or for a construction at that line."""
    f = facts_for(root, rel, line, p)
    c = (p.get("clients") or {}).get(f.get("client") or "") if f.get("found") else None
    if c is None:
        cs = [x for x in (p.get("clients") or {}).values() if x["file"] == rel and abs(int(x["line"]) - line) <= 2]
        c = cs[0] if cs else None
    return c


def _wrap_with(root, c, opts):
    """(Re-)wrap one Python client construction with these runtime options (adds options to an existing wrap)."""
    from . import apply_py
    from .runtime import render
    kind = planmod.PATCHABLE.get(c["sdk"])
    if c["lang"] != "py" or kind not in ("wrap_openai", "wrap_anthropic", "wrap_langchain"):
        return None, "client options need a Python OpenAI/Anthropic/LangChain constructor; edit by hand and record it with `adapt --loses`"
    if kind == "wrap_openai" and c["sdk"] == "anthropic":
        kind = "wrap_anthropic"
    changed, done, skipped, rt_rel = apply_py.patch_file(root, c["file"], {(int(c["line"]), None): (kind, opts)}, set())
    if skipped:
        return None, "; ".join(skipped.values())
    if rt_rel and not os.path.exists(os.path.join(root, rt_rel)):
        open(os.path.join(root, rt_rel), "w").write(render("router_runtime.py", Catalog.load()))
    return c["file"], None


def cmd_adapt(a):
    """Take a trade-off so a site can move to Router, and record what it costs. Policy levers (drop params, swap a model)
    leave the code untouched; client levers (unlock another vendor's host, route only the operator's key, drop beta flags)
    re-wrap the client. With no lever it records a hand edit you made. Every trade-off reaches the dashboard with an undo."""
    root = ledger_root(a.path)
    led = load_ledger(root)
    if led is None:
        return err(a.json, "no ledger", "run `router-migrate run .` first")
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
            return err(a.json, why, "or edit by hand, prove it with `probe`, and record it with `adapt %s:%d --loses ...`" % (rel, line))
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
               "%s recorded for %s (%s); loses: %s\nnext: prove the site now routes (`probe %s:%d --cmd ...` or `check`), then claim it migrated"
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
    s = sub.add_parser("check", help="v4: facts for one site as the code stands: routes or not, hard limits, the proposal, evidence so far, native eligibility")
    s.add_argument("loc", help="FILE:LINE (relative to the repo root)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("wrap", help="v4: the deterministic wrap at a client construction (--native: point it straight at Router when provably safe)")
    s.add_argument("loc", help="FILE:LINE of the constructor call (FILE:1 adds a file's LiteLLM/openai-module install hook)")
    s.add_argument("--path", default=".")
    s.add_argument("--stateful", action="store_true", help="keep this client's Responses API traffic on the provider (previous_response_id/store)")
    s.add_argument("--native", action="store_true", help="base_url + key from RAMP_ROUTER_* when the key is set, no runtime (Python; refused unless eligible)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("unwrap", help="v4: undo router-migrate's edit at a construction (FILE:1: a file's install hook)")
    s.add_argument("loc", help="FILE:LINE of the wrapped constructor")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("probe", help="v4: run a command with the runtime pointed at router-sim; valid requests tagged with a site's feature confirm it")
    s.add_argument("loc", nargs="?", help="FILE:LINE[,FILE:LINE...] you expect the command to exercise (reported explicitly)")
    s.add_argument("--cmd", dest="probe_cmd", required=True, help="shell command that runs the code path (e.g. the repo's Python with -c)")
    s.add_argument("--timeout", type=int, default=PROBE_TIMEOUT)
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("claim", help="v4: record your decision for one or more sites, with evidence (lines you read, probes you ran)")
    s.add_argument("loc", help="FILE:LINE[,FILE:LINE...]")
    s.add_argument("outcome_pos", nargs="?", choices=OUTCOMES, metavar="OUTCOME", help="same as --outcome")
    s.add_argument("--outcome", choices=OUTCOMES)
    s.add_argument("--reason", help="optional short reason code, e.g. " + ", ".join(REASON_CODES))
    s.add_argument("--why", required=False)
    s.add_argument("--evidence", action="append", help="FILE:LINE you read or PRn probe you ran (repeatable or comma-separated)")
    s.add_argument("--latency", help="who waits on a migrated call: interactive | user_waiting | background (compiles latency guardrails)")
    s.add_argument("--latency-critical", action="store_true", help="this call needs the default tier, not Flex (cite why in --why)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("adapt", help="v4: take a trade-off so a site moves to Router, and record what it costs (shown on the dashboard with an undo)")
    s.add_argument("loc", help="FILE:LINE of the call site (or its client construction)")
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
    s = sub.add_parser("card", help="v4: propose a lossy/business move for a human to approve: model_swap, vendor_endpoint, per_user_key, beta_flags")
    s.add_argument("loc", help="FILE:LINE of the call site")
    s.add_argument("--kind", required=True, choices=sorted(CARD_KINDS))
    s.add_argument("--to", help="model_swap: the Router catalog id to use instead")
    s.add_argument("--from", dest="from_model", help="model_swap: the model the code uses (when not a literal at the call)")
    s.add_argument("--betas", help="beta_flags: comma-separated anthropic-beta flags that may be dropped")
    s.add_argument("--why")
    s.add_argument("--evidence", action="append")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("finish", help="v4: the gate (parse, scope + invariants, guardrails, typecheck, tests vs baseline, Router-on run, evidence "
                                      "for every migrated claim, a decision for every item); reverts after --max-attempts failures")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--hook", action="store_true", help="Claude Code Stop hook mode (JSON decision on stdout)")
    s.add_argument("--max-attempts", type=int, default=3)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("judge", help="v3 improve: record a judgment for the feature at FILE:LINE (latency | task | cacheable) with evidence")
    s.add_argument("loc", help="FILE:LINE of the feature's call site")
    s.add_argument("key", choices=sorted(JUDGE_KEYS))
    s.add_argument("value")
    s.add_argument("--why")
    s.add_argument("--evidence", action="append")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("improve", help="v3 improve: cards across every Router lever from the migrated code + your judgments; "
                                       "lists features still to judge (--hook: Stop-hook gate for the improve session)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--hook", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--agent", choices=["claude"], help="run the Improve phase with the agent: judgments, cards, REPORT.html, dashboard")
    s.add_argument("--model")
    s.add_argument("--agent-timeout", type=int, default=1500)
    s.add_argument("--no-dashboard", action="store_true")
    s.add_argument("--no-browser", action="store_true")


def dispatch(a):
    return {"check": cmd_check, "wrap": cmd_wrap, "adapt": cmd_adapt, "unwrap": cmd_unwrap, "probe": cmd_probe, "claim": cmd_claim, "card": cmd_card,
            "finish": cmd_finish, "run": cmd_run, "judge": cmd_judge, "improve": cmd_improve}[a.cmd](a)


