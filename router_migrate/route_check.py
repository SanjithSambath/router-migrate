"""Route check: does each LLM call site's representative request reach Router, valid, under the repo's current code and
compiled policy? And when it doesn't, what would move it?

For every production site the scanner sees, a representative request is built from the site's static shape (its literal
kwargs, its literal model else a default; LangChain/LlamaIndex constructor settings mapped to wire names) and sent through
the REAL vendored runtime (`router_runtime.py`, rendered with the current catalog) with the repo's compiled policy installed
and the site's feature id (`<file>:<function>`) as the call-stack feature, so drop_params / model_map trade-offs apply exactly
as in production. Router-bound requests go to router-sim (fallback off); anything the runtime leaves on the provider is
recorded, never sent. A site counts as routing only when Router would accept the request (a Router rejection falls back to
the provider in production, so it is not Router traffic).

    from router_migrate.route_check import check_sites
    check_sites(root)            # every production scanner site of the code as it stands
    -> [{"file", "line", "surface", "sdk", "routes", "valid", "reason", "model", "host", "levers", "feature", ...}]

`routes` is True only for a valid Router request. `reason` is actionable: it names the router-migrate command (and lever)
that would move a site that doesn't route, or the hard limit that means nothing will. `levers` is that suggestion as data
({"wrap": "FILE:LINE"} | {"drop_params": [...]} | {"model_map": {old: new}} | {"unlock_hosts": [...]} | {"drop_betas": [...]}
| {"operator_key_only": True}); empty when the site routes or nothing can move it.

Non-literal kwargs are invisible to this check (a request built at runtime may differ), and TS sites are checked with the
Python planner (the two runtimes share the policy parity fixtures).
"""
import difflib
import glob
import importlib.util
import json
import os
import re
import shutil
import tempfile
import threading

_LOCK = threading.Lock()  # the runtime reads os.environ and module globals: one route check at a time
LC_KW = {"temperature": "temperature", "max_tokens": "max_tokens", "maxTokens": "max_tokens", "top_p": "top_p", "topP": "top_p",
         "stop": "stop", "seed": "seed", "n": "n", "logprobs": "logprobs", "presence_penalty": "presence_penalty",
         "presencePenalty": "presence_penalty", "frequency_penalty": "frequency_penalty", "frequencyPenalty": "frequency_penalty",
         "reasoning_effort": "reasoning_effort", "reasoningEffort": "reasoning_effort", "max_completion_tokens": "max_completion_tokens"}
PROVIDER_HOSTS = ("api.openai.com", "api.anthropic.com")
TS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx")
_ENV_KEYS = ("RAMP_ROUTER_API_KEY", "RAMP_ROUTER_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ROUTER_MIGRATE_FALLBACK",
             "ROUTER_MIGRATE_DISABLE", "ROUTER_MIGRATE_POLICY", "ROUTER_MIGRATE_MODEL_MAP", "ROUTER_MIGRATE_SERVICE_TIER",
             "ROUTER_MIGRATE_TRACE", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")
LOSES = '--loses "..."'


# ------------------------------------------------------------------------------------------------ inputs
def _framework_kwargs(s):
    """A LangChain / LlamaIndex model construction's literal settings, as the wire kwargs the library sends."""
    out = {}
    lit = s.get("static_kwargs") or {}
    for k, v in lit.items():
        if k in LC_KW:
            out[LC_KW[k]] = v
    for k in ("model_kwargs", "modelKwargs", "additional_kwargs"):
        if isinstance(lit.get(k), dict):
            out.update({kk: vv for kk, vv in lit[k].items() if kk not in ("messages", "model", "stream")})
    return out


def load_policy(root):
    """(policy, source) the repo's runtime would load: a compiled router_policy.py beside a vendored runtime, else
    router.policy.json at the root. (None, None) when the repo has no policy."""
    for p in sorted(glob.glob(os.path.join(root, "**", "router_policy.py"), recursive=True)):
        rel = os.path.relpath(p, root)
        if "node_modules" in rel or rel.startswith(".router-migrate"):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_rm_route_check_policy", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "POLICY", None), rel
        except Exception:
            continue
    p = os.path.join(root, "router.policy.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)), "router.policy.json"
        except ValueError:
            pass
    return None, None


_HOST = re.compile(r"(?:localhost|\d+\.\d+\.\d+\.\d+|(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?", re.I)


def endpoint_host(endpoint):
    """Where an inventory `endpoint` says a call's requests go, as a URL host: None for the provider's default endpoint (or
    one chosen by config / the user at runtime: assumed to be the provider), else the other host (a placeholder when it
    names no host)."""
    e = str(endpoint or "")
    if e in ("", "default", "env-config", "runtime-user-defined"):
        return None
    if e == "azure":
        return "example.openai.azure.com"
    if e in ("bedrock", "vertex"):
        return "%s.cloud.invalid" % e
    m = _HOST.search(e[6:] if e.startswith("other:") else e)
    return m.group(0).lower() if m else re.sub(r"[^a-z0-9]+", "-", e.lower()).strip("-")[:40] + ".invalid"


def wrap_opts(root, c):
    """Runtime options on a wrapped client's wrap call (unlock_hosts, drop_betas, operator_key_only), read from its source."""
    try:
        lines = open(os.path.join(root, c["file"]), encoding="utf-8", errors="replace").read().splitlines()
    except (OSError, KeyError):
        return {}
    text = "\n".join(lines[max(0, int(c["line"]) - 3):int(c["line"]) + 8])
    opts = {}
    for k in ("unlock_hosts", "drop_betas"):
        m = re.search(r"\b%s\s*[=:]\s*\[([^\]]*)\]" % k, text) or re.search(r"\b%s\s*[=:]\s*\[([^\]]*)\]" % re.sub(r"_(\w)", lambda x: x.group(1).upper(), k), text)
        if m:
            opts[k] = re.findall(r"[\"']([^\"']+)[\"']", m.group(1))
    if re.search(r"operator_?[kK]ey_?[oO]nly\s*[=:]\s*(?:True|true)", text):
        opts["operator_key_only"] = True
    return opts


def _clients(root, raw):
    """{client id: client record + _native (pointed at Router without the runtime)} from raw scanner sites."""
    from . import plan as planmod
    out = {}
    for c in planmod.link_same_file([dict(x) for x in raw]):
        if c["kind"] == "client":
            try:
                native = planmod.client_target(c) == "router" or planmod._router_by_assignment(root, c)
            except Exception:
                native = False
            out[c["id"]] = dict(c, _native=bool(native))
    return out


_FAMILY_OWNER = (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
                 ("gemini", "vertex"), ("grok", "xai"))


def nearest_model(name, cat=None):
    """The catalog id closest to a model name Router doesn't serve (same vendor first), for a model_map suggestion."""
    from .catalog import Catalog
    cat = cat or Catalog.load()
    ids = [m["id"] for m in cat.models if not m.get("requires_byok") and not re.match(r"^(us|eu|global|apac)\.", m["id"])]
    owner = next((o for p, o in _FAMILY_OWNER if str(name).lower().startswith(p)), None)
    same = [i for i in ids if (cat.by_id[i].get("owned_by") == owner)] if owner else []
    for pool in (same, ids):
        hit = difflib.get_close_matches(str(name), pool, n=1, cutoff=0.0)
        if hit:
            return hit[0]
    return None


# ------------------------------------------------------------------------------------------------ the simulator
class _Sim:
    """router-sim + a freshly rendered runtime with the given policy, the process env pinned for the check (restored after)."""

    def __init__(self, policy):
        self.policy = policy

    def __enter__(self):
        import httpx
        from . import verify as V
        from .catalog import Catalog
        from .runtime import render
        from .sim.server import serve
        _LOCK.acquire()
        self.tmp = tempfile.mkdtemp(prefix="rm-route-")
        self.old = {k: os.environ.get(k) for k in _ENV_KEYS}
        self.srv = None
        try:
            self.srv, self.sim, self.base = serve()
            for k in _ENV_KEYS:
                os.environ.pop(k, None)
            os.environ.update(RAMP_ROUTER_API_KEY=V.SIM_KEY, RAMP_ROUTER_BASE_URL=self.base, OPENAI_API_KEY="sk-provider-dummy",
                              ANTHROPIC_API_KEY="sk-ant-provider-dummy", ROUTER_MIGRATE_FALLBACK="0")
            p = os.path.join(self.tmp, "router_runtime.py")
            open(p, "w").write(render("router_runtime.py", Catalog.load()))
            spec = importlib.util.spec_from_file_location("rm_route_check_%d" % id(self.tmp), p)
            self.rt = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.rt)
            self.rt.set_policy(self.policy, "route-check")
            u = httpx.URL(self.base)
            self.sim_host, self.sim_port = u.host, u.port
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.srv is not None:
                self.srv.shutdown()
            for k, v in self.old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(self.tmp, ignore_errors=True)
        finally:
            _LOCK.release()

    def send(self, s, fam, model, kw, stream, surface, native, host, opts, feat):
        """One representative request. Returns {"routed", "valid", "kept" (the runtime's reason), "error", "rejects"}."""
        import anthropic
        import httpx
        import openai
        from . import verify as V
        rt, sim_host, sim_port = self.rt, self.sim_host, self.sim_port

        class Inner(httpx.BaseTransport):
            """Router-bound requests go to router-sim; anything left on the provider is recorded, never sent."""
            def __init__(self):
                self.real, self.provider = httpx.HTTPTransport(), 0

            def handle_request(self, req):
                if req.url.host == sim_host and req.url.port == sim_port:
                    return self.real.handle_request(req)
                self.provider += 1
                return httpx.Response(599, json={"error": {"message": "route check: stayed on the provider"}})

        kw = dict(kw)
        rt._feature = (lambda f=feat: f)
        rt._KEPT.reason = None
        inner = Inner()
        n0 = len(self.sim.records)
        if native:
            hc = httpx.Client(transport=inner)
            burl = self.base + ("/v1" if fam == "openai" else "")
        else:
            hc = httpx.Client(transport=rt.RouterTransport(inner=inner, opts=opts))
            burl = ("https://%s" % host + ("/v1" if fam == "openai" else "")) if host else None
        err = None
        try:
            if fam == "anthropic":
                cl = anthropic.Anthropic(http_client=hc, max_retries=0, **({"base_url": burl} if burl else {}))
                out_ = V._send(cl.messages.create, dict(model=model, max_tokens=kw.pop("max_tokens", 16),
                                                        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
                                                        **({"stream": True} if stream else {})), kw)
                if stream:
                    list(out_)
            else:
                cl = openai.OpenAI(http_client=hc, max_retries=0, **({"base_url": burl} if burl else {}))
                if surface == "responses":
                    out_ = V._send(cl.responses.create, dict(model=model, input="Reply with exactly: pong",
                                                             max_output_tokens=kw.pop("max_output_tokens", 16), stream=stream), kw)
                else:
                    b = dict(model=model, messages=[{"role": "user", "content": "Reply with exactly: pong"}])
                    if "max_tokens" not in kw and "max_completion_tokens" not in kw:
                        b["max_tokens"] = 16
                    if stream:
                        b["stream"] = True
                    out_ = V._send(cl.chat.completions.create, b, kw)
                if stream:
                    list(out_)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, str(e)[:200])
        finally:
            hc.close()
        recs = self.sim.records[n0:]
        return {"routed": bool(recs), "valid": (all(x.get("valid") for x in recs) if recs else None),
                "kept": getattr(rt._KEPT, "reason", None), "error": err,
                "rejects": [str(x.get("error"))[:160] for x in recs if not x.get("valid")]}


# ------------------------------------------------------------------------------------------------ reasons
def _hard_limit(s):
    from . import agent as AG
    return AG.hard_limit(s)


def _hard_reason(s, hl):
    if hl == "no_endpoint":
        return "hard limit: %s — Router has no endpoint for it" % (s.get("surface") or "this surface")
    return "hard limit: provider-side state (store / previous_response_id / conversation / provider files) — Router keeps none"


def _kept_reason(kept, loc, kw, host, cat):
    """(reason, levers) for a request the runtime left on the provider."""
    if kept:
        m = re.match(r"untranslatable: (.+)$", kept)
        if m:
            items = [x.strip() for x in m.group(1).split(",") if x.strip()]
            if items and all(x in kw for x in items):
                return ("untranslatable: %s — `router-migrate adapt %s --drop %s %s` would route it" % (
                    ", ".join(items), loc, ",".join(items), LOSES), {"drop_params": items})
            return "untranslatable: %s — Router can't take this request shape; rewrite the call or keep it" % m.group(1), {}
        m = re.match(r"model '(.+)' is not in the Router catalog$", kept) or re.match(r'model "(.+)" is not in the Router catalog$', kept)
        if m:
            near = nearest_model(m.group(1), cat)
            return ("model '%s' not in catalog — `router-migrate adapt %s --model-map %s=%s %s`" % (
                m.group(1), loc, m.group(1), near or "<catalog id>", LOSES), {"model_map": {m.group(1): near}} if near else {})
        m = re.match(r"anthropic-beta (.+)$", kept)
        if m:
            return ("anthropic-beta %s — `router-migrate adapt %s --drop-betas %s %s` would route it" % (m.group(1), loc, m.group(1), LOSES),
                    {"drop_betas": m.group(1).split(",")})
        return "stays on provider: %s" % kept, {}
    if host:
        return ("host %s not unlocked — `router-migrate adapt %s --unlock-host %s %s` (only if that endpoint serves models Router has)"
                % (host, loc, host, LOSES), {"unlock_hosts": [host]})
    return "stays on provider: plan_request returned None", {}


def _static_reason(s, c, loc, cat):
    """(reason, levers) for a site the code doesn't send through the runtime at all (no simulation needed)."""
    from . import plan as planmod
    why = str(s.get("reason") or "")
    if c is not None and not c.get("wrapped") and not c.get("_native"):
        tgt = planmod.client_target(c)
        cloc = "%s:%s" % (c["file"], c["line"])
        if tgt.startswith("other:"):
            bu = c.get("base_url") or {}
            host = _host_of(bu.get("value")) if bu.get("static") else None
            if host:
                return ("host %s not unlocked — `router-migrate adapt %s --unlock-host %s %s` (only if that endpoint serves models "
                        "Router has)" % (host, cloc, host, LOSES), {"unlock_hosts": [host]})
            return ("client targets %s (another vendor's endpoint) — `router-migrate adapt %s --unlock-host <its host> %s` if that "
                    "endpoint serves models Router has" % (tgt[6:], cloc, LOSES), {"unlock_hosts": []})
        if (c.get("api_key") or {}).get("per_tenant"):
            return ("per-tenant keys — `router-migrate adapt %s --operator-key-only %s` routes only the operator's key" % (cloc, LOSES),
                    {"operator_key_only": True})
        if tgt in ("provider", "provider-or-config") and planmod.PATCHABLE.get(c["sdk"]):
            return "client not wrapped — `router-migrate wrap %s`" % cloc, {"wrap": cloc}
    if s.get("kind") == "module_client" and not s.get("installed"):
        return "module-level %s usage with no install hook — `router-migrate wrap %s:1`" % (s.get("sdk"), s["file"]), {"wrap": "%s:1" % s["file"]}
    if "Anthropic beta" in why:
        return "anthropic-beta — `router-migrate adapt %s --drop-betas <beta> %s` would route it" % (loc, LOSES), {"drop_betas": []}
    m = re.match(r"model '([^']+)' is not in the Router catalog", why)
    if m:
        near = nearest_model(m.group(1), cat)
        return ("model '%s' not in catalog — `router-migrate adapt %s --model-map %s=%s %s`" % (m.group(1), loc, m.group(1), near or "<catalog id>", LOSES),
                {"model_map": {m.group(1): near}} if near else {})
    return "%s: %s" % (s.get("outcome") or "not routed", why or "the code doesn't send this call through the runtime"), {}


def _host_of(url):
    if not url:
        return None
    try:
        import httpx
        return httpx.URL(str(url)).host or None
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------ API
_REPO_POLICY = object()


def check_sites(root, sites=None, policy=_REPO_POLICY, hosts=None, models=None, simulate_all=False):
    """Route check (see _check_sites); the runtime's one-time policy notes on stderr are for applications, not this report."""
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        return _check_sites(root, sites, policy, hosts, models, simulate_all)


def _check_sites(root, sites=None, policy=_REPO_POLICY, hosts=None, models=None, simulate_all=False):
    """Route check every site (default: every production site the scanner + measure-only planner sees in the code as it
    stands). hosts / models: {site_id: host / model} overrides for where a site's requests really go and which model it
    sends (an eval passes inventory-derived ones; by default the code's own static base URL and literal model are used).
    policy: default the repo's compiled policy (load_policy); None = no policy. simulate_all: send every site through the
    runtime as written, hard limits included (the caller asserts the code routes them: the eval's statically routed sites)."""
    from . import agent as AG, scan_py
    from . import verify as V
    from .catalog import Catalog
    root = os.path.abspath(root)
    if sites is None:
        p = AG.current_plan(root)
        sites = [s for s in p["sites"] if s.get("prod")]
        raw = list(p.get("clients", {}).values())
    else:
        raw = list(scan_py.scan(root)["sites"])
        from . import cli
        if any(str(s.get("file", "")).endswith(TS_EXT) for s in sites) and cli.node_ok():
            try:
                raw += cli.run_ts(["scan", root]).get("sites", [])
            except Exception:
                pass
    if not sites:
        return []
    if policy is _REPO_POLICY:
        policy, _ = load_policy(root)
    cat = Catalog.load()
    clients = _clients(root, raw)
    rows = []
    with _Sim(policy) as sim:
        for s in sites:
            fam = V._family(s)
            m = s.get("model") or {}
            model = m.get("value") if m.get("static") and m.get("value") else (models or {}).get(s["id"]) or V.DEFAULT_MODEL[fam]
            framework = s["kind"] == "client" or str(s.get("surface", "")).startswith("framework")
            kw = _framework_kwargs(s) if framework else V.replay_kwargs(s)
            kw.pop("__splat_keys__", None)
            stream = bool(s.get("stream") or (s.get("static_kwargs") or {}).get("streaming"))
            surface = "responses" if s.get("surface") == "responses" else ("messages" if fam == "anthropic" else "chat")
            c = clients.get(s.get("via_client") or "") or (clients.get(s["id"]) if s["kind"] == "client" else None)
            if c is None and s.get("client_id") and not simulate_all:  # a call the planner left unlinked (flagged / kept)
                c = clients.get(s["client_id"])
            native = bool(c and c.get("_native") and not c.get("wrapped"))
            host = None if native else (hosts or {}).get(s["id"])
            if host is None and c is not None and not native:
                bu = c.get("base_url") or {}
                if bu.get("static") and bu.get("value"):
                    h = _host_of(bu["value"])
                    host = h if h and h not in PROVIDER_HOSTS else None
            opts = wrap_opts(root, c) if (c is not None and c.get("wrapped")) else {}
            if s.get("installed") or s["kind"] == "module_client":
                opts = {}
            line_now = int(s.get("line_now") or s["line"])
            loc = "%s:%d" % (s["file"], line_now)
            try:
                feat = AG._feature(root, s["file"], line_now)
            except Exception:
                feat = "%s:<module>" % s["file"]
            row = {"id": s.get("id"), "file": s["file"], "line": s["line"], "kind": s.get("kind"), "surface": s.get("surface"),
                   "sdk": s.get("sdk"), "wire": surface, "model": model, "host": host, "feature": feat, "kwargs": sorted(kw),
                   "client": "%s:%s" % (c["file"], c["line"]) if c else None, "wrapped": bool(c and c.get("wrapped")),
                   "path": "native" if native else "runtime", "wrap_opts": opts or None,
                   "routes": False, "routed": False, "valid": None, "levers": {}}
            hl = None if simulate_all else _hard_limit(s)
            if hl:
                row.update(reason=_hard_reason(s, hl), hard_limit=hl, path=None)
                rows.append(row)
                continue
            through = simulate_all or s.get("outcome") in (None, "migrated") or native or bool(c and c.get("wrapped")) or \
                (s["kind"] == "module_client" and s.get("installed"))
            if not through:
                reason, levers = _static_reason(s, c, loc, cat)
                if levers.get("wrap"):  # what would still stop it once wrapped: the runtime's verdict as if it were
                    r = sim.send(s, fam, model, kw, stream, surface, False, host, {}, feat)
                    if not (r["routed"] and r["valid"]):
                        more, more_lv = _sim_reason(r, loc, kw, host, cat)
                        reason += "; once wrapped: " + more
                        levers = dict(levers, **more_lv)
                row.update(reason=reason, levers=levers, path=None)
                rows.append(row)
                continue
            r = sim.send(s, fam, model, kw, stream, surface, native, host, opts, feat)
            row.update(routed=r["routed"], valid=r["valid"], routes=bool(r["routed"] and r["valid"]))
            if row["routes"]:
                row["reason"] = "valid Router request"
            else:
                row["reason"], row["levers"] = _sim_reason(r, loc, kw, host, cat, native)
            rows.append(row)
    return rows


def _sim_reason(r, loc, kw, host, cat, native=False):
    if r["routed"]:
        return "Router rejects: %s" % "; ".join(r["rejects"]), {}
    if native:
        return "stays on provider: %s" % (r["kept"] or "no Router request"), {}
    if r["kept"] or host:
        return _kept_reason(r["kept"], loc, kw, host, cat)
    return "stays on provider: %s" % (r["error"] or "plan_request returned None"), {}


def route_check(dst, sites, policy=None, hosts=None, models=None):
    """Eval form: {site_id: {"routed", "valid", "path", "reason", "model", "kwargs", "feature", "surface", "host", "wrap_opts"}}
    for sites the caller already measured as statically routed (each is sent through the runtime as written)."""
    if not sites:
        return {}
    out = {}
    for row in check_sites(dst, sites, policy, hosts, models, simulate_all=True):
        out[row["id"]] = {"routed": row["routed"], "valid": row["valid"], "path": row["path"], "reason": row["reason"],
                          "model": row["model"], "kwargs": row["kwargs"], "feature": row["feature"], "surface": row["wire"],
                          "host": row["host"] or "provider default", "wrap_opts": row["wrap_opts"], "levers": row["levers"]}
    return out
