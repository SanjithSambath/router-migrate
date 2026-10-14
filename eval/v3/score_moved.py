#!/usr/bin/env python3
"""Sites moved = real Router traffic, scored against a mechanical "movable" ground truth (the migrate-by-default stance).

A site counts as MOVED only if the arm reports it migrated AND it actually sends a valid Router request:
  (a) dynamic evidence: the harness re-ran the agent's recorded probes on the final code against router-sim and saw a valid
      request from the site's feature (ablation_b2.reverify); when the trial tree is gone, the ledger's `confirmations`; or
  (b) static: the code routes it (run_v3.measure: wrapped client / client pointed at Router) AND a representative request
      built from the site's static kwargs (verify.replay's shape: literal kwargs, literal model else a default) is routed by
      the *current* vendored runtime's plan_request under the arm's compiled policy (router_policy.py / router.policy.json in
      the trial tree; drop_params / model_map trade-offs apply), and router-sim accepts the request Router would get (a
      Router rejection falls back to the provider in production, so it is not Router traffic). Sites on a client pointed at
      Router by hand (no runtime) are sent to router-sim as written.

Ground truth (no LLM adjudication): movable(site) over the inventory fields. movable = prod AND an LLM generation call AND not
a hard limit: Router has no endpoint (embeddings, images, audio/tts/transcription, files, moderation, legacy completions, model
lists), provider-side state actually used, a local/self-hosted model, or the app's own proxy/gateway. Wrong move = a moved site
that is not movable (hard limit, non-production, non-LLM); broken repos are reported beside it.

  .venv/bin/python eval/v3/score_moved.py classify                 # per-repo movable table + every not-movable site
  .venv/bin/python eval/v3/score_moved.py score [--arms A,C] [--only repo,..] [--jobs 4]
                                                                    # runs/<arm>/<repo>/sites_moved.json, then report
  .venv/bin/python eval/v3/score_moved.py report                   # eval/v3/SITES_MOVED.md + sites_moved.json
RM_EVAL_SET=dev scores eval/v3/runs_dev (no inventory: moved = valid Router traffic only).
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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
    if R.EVAL_SET != "v3":
        return None
    for sub in ("inventory_adjudicated", "inventory"):
        p = os.path.join(HERE, sub, name + ".json")
        if os.path.exists(p):
            return json.load(open(p))["sites"]
    return None


def cmd_classify(a=None):
    rows, nots = [], []
    for f in sorted(glob.glob(os.path.join(HERE, "inventory", "*.json")), key=lambda p: os.path.basename(p).lower()):
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
_LOCK = threading.Lock()  # the runtime reads os.environ and module globals: one route check at a time
LC_KW = {"temperature": "temperature", "max_tokens": "max_tokens", "maxTokens": "max_tokens", "top_p": "top_p", "topP": "top_p",
         "stop": "stop", "seed": "seed", "n": "n", "logprobs": "logprobs", "presence_penalty": "presence_penalty",
         "presencePenalty": "presence_penalty", "frequency_penalty": "frequency_penalty", "frequencyPenalty": "frequency_penalty",
         "reasoning_effort": "reasoning_effort", "reasoningEffort": "reasoning_effort", "max_completion_tokens": "max_completion_tokens"}


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


def load_policy(dst):
    """(policy, source) the trial tree's runtime would load: a compiled router_policy.py beside a vendored runtime, else
    router.policy.json at the root. (None, None) when the tree has no policy."""
    for p in sorted(glob.glob(os.path.join(dst, "**", "router_policy.py"), recursive=True)):
        rel = os.path.relpath(p, dst)
        if "node_modules" in rel or rel.startswith(".router-migrate"):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_rm_eval_policy", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "POLICY", None), rel
        except Exception:
            continue
    p = os.path.join(dst, "router.policy.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)), "router.policy.json"
        except ValueError:
            pass
    return None, None


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


def _clients(dst, sites):
    from router_migrate import plan as planmod
    out = {}
    for c in planmod.link_same_file([dict(x) for x in sites]):
        if c["kind"] == "client":
            try:
                native = planmod.client_target(c) == "router" or planmod._router_by_assignment(dst, c)
            except Exception:
                native = False
            out[c["id"]] = dict(c, _native=bool(native))
    return out


_HOST = re.compile(r"(?:localhost|\d+\.\d+\.\d+\.\d+|(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?", re.I)


def endpoint_host(endpoint):
    """Where the inventory says the call's requests go, as a URL host: None for the provider's default endpoint (or one chosen
    by config / the user at runtime: assumed to be the provider), else the other host (a placeholder when it names no host)."""
    e = str(endpoint or "")
    if e in ("", "default", "env-config", "runtime-user-defined"):
        return None
    if e == "azure":
        return "example.openai.azure.com"
    if e in ("bedrock", "vertex"):
        return "%s.cloud.invalid" % e
    m = _HOST.search(e[6:] if e.startswith("other:") else e)
    return m.group(0).lower() if m else re.sub(r"[^a-z0-9]+", "-", e.lower()).strip("-")[:40] + ".invalid"


def wrap_opts(dst, c):
    """Runtime options on a wrapped client's wrap call (unlock_hosts, drop_betas, operator_key_only), read from its source."""
    try:
        lines = open(os.path.join(dst, c["file"]), encoding="utf-8", errors="replace").read().splitlines()
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


PRODUCTION_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "production_config.json")


def production_config(name):
    """{file: {host, model}} for config-driven clients in this repo's shipped setup (eval/v3/production_config.json)."""
    try:
        return json.load(open(PRODUCTION_CONFIG)).get(name) or {}
    except (OSError, ValueError):
        return {}


def route_check(dst, sites, policy=None, hosts=None, models=None):
    """For each statically routed site: does a representative request actually reach Router, valid? hosts: {site_id: host}
    where the site's requests really go when that differs from the provider default (from the inventory's endpoint: a
    config-driven base URL the scanner cannot resolve, or an ambiguous client link). Returns
    {site_id: {"routed": bool, "valid": bool|None, "path": "runtime"|"native", "reason": str, "model": str, "kwargs": [...]}}."""
    import httpx
    import openai
    import anthropic
    from router_migrate import agent as AG, scan_py, verify as V
    from router_migrate.catalog import Catalog
    from router_migrate.runtime import render
    from router_migrate.sim.server import serve
    if not sites:
        return {}
    cat = Catalog.load()
    raw = list(scan_py.scan(dst)["sites"])
    from router_migrate import cli
    if any(s["file"].endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx")) for s in sites) and cli.node_ok():
        try:
            raw += cli.run_ts(["scan", dst]).get("sites", [])
        except Exception:
            pass
    clients = _clients(dst, raw)
    out = {}
    with _LOCK:
        tmp = tempfile.mkdtemp(prefix="rm-route-")
        keys = ("RAMP_ROUTER_API_KEY", "RAMP_ROUTER_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ROUTER_MIGRATE_FALLBACK",
                "ROUTER_MIGRATE_DISABLE", "ROUTER_MIGRATE_POLICY", "ROUTER_MIGRATE_MODEL_MAP", "ROUTER_MIGRATE_SERVICE_TIER",
                "ROUTER_MIGRATE_TRACE", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")
        old = {k: os.environ.get(k) for k in keys}
        srv, sim, base = serve()
        try:
            for k in keys:
                os.environ.pop(k, None)
            os.environ.update(RAMP_ROUTER_API_KEY=V.SIM_KEY, RAMP_ROUTER_BASE_URL=base, OPENAI_API_KEY="sk-provider-dummy",
                              ANTHROPIC_API_KEY="sk-ant-provider-dummy", ROUTER_MIGRATE_FALLBACK="0")
            p = os.path.join(tmp, "router_runtime.py")
            open(p, "w").write(render("router_runtime.py", cat))
            spec = importlib.util.spec_from_file_location("rm_route_check_%d" % id(tmp), p)
            rt = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rt)
            rt.set_policy(policy, "eval")
            sim_host, sim_port = httpx.URL(base).host, httpx.URL(base).port

            class Inner(httpx.BaseTransport):
                """Router-bound requests go to router-sim; anything left on the provider is recorded, never sent."""
                def __init__(self):
                    self.real, self.provider = httpx.HTTPTransport(), 0

                def handle_request(self, req):
                    if req.url.host == sim_host and req.url.port == sim_port:
                        return self.real.handle_request(req)
                    self.provider += 1
                    return httpx.Response(599, json={"error": {"message": "route check: stayed on the provider"}})

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
                native = bool(c and c.get("_native") and not c.get("wrapped"))
                host = None if native else (hosts or {}).get(s["id"])
                if host is None and c is not None and not native:
                    bu = c.get("base_url") or {}
                    if bu.get("static") and bu.get("value"):
                        h = httpx.URL(str(bu["value"])).host
                        host = h if h and h not in ("api.openai.com", "api.anthropic.com") else None
                opts = wrap_opts(dst, c) if (c is not None and c.get("wrapped")) else {}
                if s.get("installed") or s["kind"] == "module_client":
                    opts = {}
                try:
                    feat = AG._feature(dst, s["file"], int(s.get("line_now") or s["line"]))
                except Exception:
                    feat = "%s:<module>" % s["file"]
                rt._feature = (lambda f=feat: f)
                rt._KEPT.reason = None
                inner = Inner()
                n0 = len(sim.records)
                if native:
                    hc = httpx.Client(transport=inner)
                    burl = base + ("/v1" if fam == "openai" else "")
                else:
                    hc = httpx.Client(transport=rt.RouterTransport(inner=inner, opts=opts))
                    burl = ("https://%s" % host + ("/v1" if fam == "openai" else "")) if host else None
                r = {"path": "native" if native else "runtime", "model": model, "kwargs": sorted(kw), "feature": feat, "surface": surface,
                     "host": host or "provider default", "wrap_opts": opts or None}
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
                recs = sim.records[n0:]
                routed = bool(recs)
                valid = (all(x.get("valid") for x in recs) if recs else None)
                r.update(routed=routed, valid=valid)
                if not routed:
                    r["reason"] = "stays on provider: %s" % (getattr(rt._KEPT, "reason", None) or ("no Router request" if native else
                                                             ("its requests go to %s, which the runtime does not route" % host) if host else
                                                             err or "plan_request returned None"))
                elif not valid:
                    r["reason"] = "Router rejects: %s" % "; ".join(str(x.get("error"))[:160] for x in recs if not x.get("valid"))
                else:
                    r["reason"] = "valid Router request"
                out[s["id"]] = r
        finally:
            srv.shutdown()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(tmp, ignore_errors=True)
    return out


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
    return dst if code == 0 else None


def score_one(arm, name, dst=None, measured=None, reported=None, broken=None):
    """sites_moved.json for one trial: moved sites (valid Router traffic), new/old ground truth, wrong moves, trade-offs."""
    import ablation_b2 as B  # installs the B2/Cnf/D reported-outcome hooks on run_v3
    out_dir = os.path.join(R.RUNS, arm, name)
    dst = dst or os.path.join(R.TRIALS, arm, name)
    fallback = None
    led_p = os.path.join(dst, ".router-migrate", "ledger.json")
    if not os.path.isdir(dst):
        rec = reconstruct(name, out_dir)
        fallback = "trial tree missing: rebuilt from pristine + diff.patch" if rec else "trial tree missing and not rebuildable"
        dst = rec
        led_p = os.path.join(out_dir, "ledger.json")
    led = json.load(open(led_p)) if led_p and os.path.exists(led_p) else (
        json.load(open(os.path.join(out_dir, "ledger.json"))) if os.path.exists(os.path.join(out_dir, "ledger.json")) else None)
    res_p = os.path.join(out_dir, "result.json")
    res = json.load(open(res_p)) if os.path.exists(res_p) else {}
    row = {"arm": arm, "repo": name, "set": R.EVAL_SET, "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fallback": fallback,
           "broken": res.get("broken") if broken is None else broken, "usd": res.get("usd") or 0.0, "minutes": res.get("minutes"),
           "tests_before": res.get("tests_before"), "tests_after": res.get("tests_after"),
           "tradeoffs": len(_tradeoffs(led)), "tradeoff_detail": [{"site": t.get("site"), "levers": t.get("levers"), "loses": t.get("loses")}
                                                                for t in _tradeoffs(led)]}
    if dst is None:
        row["error"] = fallback
        return row
    row["tree_matches_saved_patch"] = None if fallback else tree_matches_patch(dst, out_dir)
    static = B._orig_measure(dst, name) if measured is None else [s for s in measured if not str(s.get("reason", "")).startswith("dynamic:")]
    if reported is None:
        reported = R.reported_outcomes(arm, dst, name, static)
    # (a) dynamic evidence
    dyn, dyn_src = [], None
    given = [s for s in measured or [] if str(s.get("reason", "")).startswith("dynamic:")]
    if given:  # score_trial's measure (ablation_b2.measure) already re-ran the probes
        dyn, dyn_src = given, "harness re-ran the agent's probes"
    elif led and (led.get("probes") or {}) and not fallback:
        try:
            dyn, dyn_src = B.reverify(dst, name), "harness re-ran the agent's probes"
        except Exception as e:
            dyn_src = "reverify failed: %s" % str(e)[:200]
    elif led and led.get("confirmations"):
        pristine = os.path.join(R.RE.WORK, "pristine", name)
        for k in led["confirmations"]:
            f, ln = k.rsplit(":", 1)
            m = R.line_map(os.path.join(pristine, f), os.path.join(dst, f))
            dyn.append({"file": f, "line": m(int(ln)) if m else int(ln), "line_now": int(ln), "surface": "chat", "outcome": "migrated"})
        dyn_src = "ledger confirmations (not re-verified)"
    # (b) static shape through the runtime planner + the tree's policy
    policy, pol_src = load_policy(dst)
    if policy is None and fallback and led:
        policy, pol_src = policy_from_ledger(led), "ledger trade-offs"
    rep_mig = [r for r in reported if r.get("outcome") == "migrated"]
    claimed = lambda s: any(r["file"] == s["file"] and abs(int(r["line"]) - int(s["line"])) <= 3 for r in rep_mig)
    routed_static = [s for s in static if s.get("outcome") == "migrated"]
    inv = inventory_all(name)
    to_check = [s for s in routed_static if claimed(s)]
    hosts = {}
    if inv is not None:  # where each call's requests really go (a config-driven base URL / ambiguous client the scanner can't resolve)
        for i, p in R.match_moved([i for i in inv if i.get("prod")], to_check)[0]:
            if endpoint_host(i.get("endpoint")):
                hosts[p["id"]] = endpoint_host(i.get("endpoint"))
    models = {}
    for s in to_check:  # config-driven clients: the shipped setup's host and model
        pc = production_config(name).get(s["file"])
        if pc and s["id"] not in hosts:
            hosts[s["id"]], models[s["id"]] = pc.get("host"), pc.get("model")
    checks = route_check(dst, to_check, policy, hosts, models)
    moved, lost = [], []
    for s in routed_static:
        if not claimed(s):
            continue
        ck = checks.get(s["id"]) or {}
        d = next((x for x in dyn if x["file"] == s["file"] and abs(int(x["line"]) - int(s["line"])) <= 3), None)
        if ck.get("routed") and ck.get("valid"):
            moved.append(dict(s, evidence="static: " + ck["reason"]))
        elif d is not None:
            moved.append(dict(s, evidence="dynamic: " + dyn_src))
        else:
            lost.append({"file": s["file"], "line": s["line"], "line_now": s.get("line_now"), "surface": s.get("surface"),
                         "why": ck.get("reason") or "not checked", "model": ck.get("model"), "kwargs": ck.get("kwargs"), "host": ck.get("host")})
    for d in dyn:  # dynamically confirmed claims the static measure does not see (e.g. a hand-written client)
        if claimed(d) and not any(m["file"] == d["file"] and abs(int(m["line"]) - int(d["line"])) <= 3 for m in moved):
            moved.append(dict(d, evidence="dynamic: " + dyn_src))
    row.update({"policy": pol_src, "dynamic_source": dyn_src, "dynamic_sites": len(dyn), "routed_static": len(routed_static),
                "claimed_migrated": len(rep_mig), "claimed_and_routed_static": len(checks), "sites_moved": len(moved),
                "moved": [{"file": s["file"], "line": s["line"], "surface": s.get("surface"), "evidence": s["evidence"]} for s in moved],
                "wrapped_but_no_router_traffic": lost})
    if inv is not None:
        prod = [i for i in inv if i.get("prod")]
        pairs, missed, extra, file_level = R.match_moved(prod, moved)
        np_pairs, _, extra = HA.match([i for i in inv if not i.get("prod")], extra)
        pairs += np_pairs
        mv = {id(i): movable(i, has_operator_key(inv)) for i in inv}
        movable_sites = [i for i in prod if mv[id(i)][0]]
        old_gt = [i for i in prod if i.get("expected") == "migrated"]
        good = [(i, p) for i, p in pairs if mv[id(i)][0]]
        wrong = [(i, p) for i, p in pairs if not mv[id(i)][0]]
        row.update({"movable_total": len(movable_sites), "sites_moved_movable": len(good), "sites_moved_wrong": len(wrong),
                    "sites_moved_unmatched": len(extra), "file_level_matches": file_level,
                    "old_gt_total": len(old_gt), "sites_moved_old_gt": sum(1 for i, _ in pairs if i.get("expected") == "migrated"),
                    "wrong_detail": [{"file": i["file"], "line": i["line"], "why_not_movable": mv[id(i)][1], "evidence": p["evidence"]}
                                     for i, p in wrong],
                    "unmatched_detail": [{"file": p["file"], "line": p["line"], "evidence": p["evidence"]} for p in extra],
                    "movable_missed": [{"file": i["file"], "line": i["line"], "hint": mv[id(i)][2]} for i in movable_sites
                                       if not any(i is a for a, _ in good)]})
    else:
        row.update({"movable_total": None, "sites_moved_movable": None, "sites_moved_wrong": None, "old_gt_total": None,
                    "sites_moved_old_gt": None})
    os.makedirs(out_dir, exist_ok=True)
    json.dump(row, open(os.path.join(out_dir, OUT_NAME), "w"), indent=1, default=str)
    if fallback and dst and dst.startswith(tempfile.gettempdir()):
        shutil.rmtree(dst, ignore_errors=True)
    print("%-10s %-30s moved=%s/%s (old GT %s/%s) wrong=%s lost=%d broken=%s tradeoffs=%d" % (
        arm, name, row["sites_moved"], row.get("movable_total"), row.get("sites_moved_old_gt"), row.get("old_gt_total"),
        row.get("sites_moved_wrong"), len(lost), row["broken"], row["tradeoffs"]), flush=True)
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


# ------------------------------------------------------------------------------------------------ report
ARM_ORDER = ["A", "B", "B_prime", "C", "B2_r1", "Cnf_r1", "D_r1"]


def _arm_key(a):
    return (ARM_ORDER.index(a) if a in ARM_ORDER else len(ARM_ORDER), a)


def load_rows():
    rows = {}
    for f in sorted(glob.glob(os.path.join(R.RUNS, "*", "*", OUT_NAME))):
        r = json.load(open(f))
        rows.setdefault(r["arm"], []).append(r)
    return rows


def report():
    rows = load_rows()
    v3 = R.EVAL_SET == "v3"
    repos = sorted(R.repos(), key=str.lower)
    cls = {}
    if v3:
        for n in repos:
            inv = inventory_all(n) or []
            cls[n] = [(i, movable(i, has_operator_key(inv))) for i in inv if i.get("prod")]
    movable_total = sum(1 for n in cls for _, m in cls[n] if m[0])
    old_total = sum(1 for n in cls for i, _ in cls[n] if i.get("expected") == "migrated")
    summary = {}
    for arm, rs in rows.items():
        t = {"repos": len(rs), "sites_moved": sum(r.get("sites_moved") or 0 for r in rs),
             "sites_moved_movable": sum(r.get("sites_moved_movable") or 0 for r in rs),
             "sites_moved_old_gt": sum(r.get("sites_moved_old_gt") or 0 for r in rs),
             "sites_moved_wrong": sum(r.get("sites_moved_wrong") or 0 for r in rs),
             "sites_moved_unmatched": sum(r.get("sites_moved_unmatched") or 0 for r in rs),
             "moved_in_unbroken": sum(r.get("sites_moved_movable") or (0 if v3 else r.get("sites_moved") or 0) for r in rs if not r.get("broken")),
             "broken": sum(1 for r in rs if r.get("broken")), "tradeoffs": sum(r.get("tradeoffs") or 0 for r in rs),
             "wrapped_no_traffic": sum(len(r.get("wrapped_but_no_router_traffic") or []) for r in rs),
             "claimed_migrated": sum(r.get("claimed_migrated") or 0 for r in rs),
             "usd": round(sum(r.get("usd") or 0 for r in rs), 2), "minutes": round(sum(r.get("minutes") or 0 for r in rs), 1),
             "fallbacks": sum(1 for r in rs if r.get("fallback")), "stale_trees": sum(1 for r in rs if r.get("tree_matches_saved_patch") is False)}
        summary[arm] = t
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "set": R.EVAL_SET, "movable_total": movable_total, "old_gt_total": old_total,
           "arms": summary, "rows": rows}
    json.dump(out, open(os.path.join(HERE, "sites_moved.json" if v3 else "sites_moved_%s.json" % R.EVAL_SET), "w"), indent=1, default=str)
    L = ["# Sites moved = real Router traffic (%s set)" % R.EVAL_SET, "",
         "Generated by `eval/v3/score_moved.py` (re-run: `score` then `report`). A site is **moved** only if the arm reports it "
         "migrated AND it sends a valid Router request: a representative request built from the site's static kwargs is routed by "
         "the current vendored runtime's `plan_request` under the trial tree's compiled policy and router-sim accepts it, or the "
         "harness re-ran the agent's probes and saw it (dynamic). Wrapped-and-claimed alone earns nothing.", ""]
    if v3:
        L += ["Ground truth (mechanical, no LLM adjudication; `movable()` in score_moved.py): **%d movable** production LLM "
              "generation sites (every prod site that is not a hard limit: no Router endpoint, provider-side state, local/self-hosted "
              "model, or the app's own proxy). Old ground truth: the adjudicated inventory's **%d** should-move sites. "
              "Wrong move = a moved site that is not movable (hard limit, non-production, non-LLM)." % (movable_total, old_total), "",
              "| arm | repos | moved / movable | moved (old GT) / %d | wrong moves | unmatched moves | moved in unbroken repos | "
              "broken repos | wrapped+claimed but no Router traffic | trade-offs | $ | minutes |" % old_total,
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm in sorted(summary, key=_arm_key):
            t = summary[arm]
            L.append("| %s | %d | %d / %d | %d | %d | %d | %d | %d/%d | %d | %d | %.2f | %.0f |" % (
                arm, t["repos"], t["sites_moved_movable"], movable_total, t["sites_moved_old_gt"], t["sites_moved_wrong"],
                t["sites_moved_unmatched"], t["moved_in_unbroken"], t["broken"], t["repos"], t["wrapped_no_traffic"], t["tradeoffs"],
                t["usd"], t["minutes"]))
    else:
        L += ["No ground-truth inventory for this set: moved = migrated claims with valid Router traffic.", "",
              "| arm | repos | sites moved | claimed migrated | wrapped+claimed but no Router traffic | broken repos | trade-offs | $ | minutes |",
              "|---|---|---|---|---|---|---|---|---|"]
        for arm in sorted(summary, key=_arm_key):
            t = summary[arm]
            L.append("| %s | %d | %d | %d | %d | %d/%d | %d | %.2f | %.0f |" % (arm, t["repos"], t["sites_moved"], t["claimed_migrated"],
                                                                         t["wrapped_no_traffic"], t["broken"], t["repos"], t["tradeoffs"],
                                                                         t["usd"], t["minutes"]))
    out["table_md"] = [l for l in L if l.startswith("|")]
    notes = [a for a, t in summary.items() if t["fallbacks"] or t["stale_trees"]]
    if notes:
        L += ["", "Tree caveats: " + "; ".join("%s: %d rebuilt from diff.patch, %d trees differ from the saved diff.patch" % (
            a, summary[a]["fallbacks"], summary[a]["stale_trees"]) for a in sorted(notes, key=_arm_key))]
    # per repo
    L += ["", "## Per repo", ""]
    arms = sorted(rows, key=_arm_key)
    if v3:
        L += ["Cell: moved movable / wrong (b = broken repo). Header: movable sites (old GT should-move).", "",
              "| repo | movable (old) | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
        for n in repos:
            cells = []
            for arm in arms:
                r = next((x for x in rows[arm] if x["repo"] == n), None)
                cells.append("-" if r is None else "%s/%s%s" % (r.get("sites_moved_movable"), r.get("sites_moved_wrong"), " b" if r.get("broken") else ""))
            L.append("| %s | %d (%d) | %s |" % (n, sum(1 for _, m in cls[n] if m[0]), sum(1 for i, _ in cls[n] if i.get("expected") == "migrated"),
                                                " | ".join(cells)))
    else:
        L += ["| repo | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        for n in repos:
            cells = []
            for arm in arms:
                r = next((x for x in rows[arm] if x["repo"] == n), None)
                cells.append("-" if r is None else "%s moved, tests %s->%s%s" % (r.get("sites_moved"), _tests(r.get("tests_before")),
                                                                             _tests(r.get("tests_after")), " b" if r.get("broken") else ""))
            L.append("| %s | %s |" % (n, " | ".join(cells)))
    # wrapped-but-no-traffic and wrong moves
    L += ["", "## Wrapped and claimed migrated, but no valid Router traffic", "",
          "| arm | repo | site | why |", "|---|---|---|---|"]
    for arm in arms:
        for r in sorted(rows[arm], key=lambda r: r["repo"].lower()):
            for x in r.get("wrapped_but_no_router_traffic") or []:
                L.append("| %s | %s | `%s:%s` | %s |" % (arm, r["repo"], x["file"], x["line"], str(x["why"]).replace("|", "/")[:160]))
    if v3:
        L += ["", "## Wrong moves", "", "| arm | repo | site | not movable because |", "|---|---|---|---|"]
        for arm in arms:
            for r in sorted(rows[arm], key=lambda r: r["repo"].lower()):
                for x in r.get("wrong_detail") or []:
                    L.append("| %s | %s | `%s:%s` | %s |" % (arm, r["repo"], x["file"], x["line"], x["why_not_movable"]))
        L += ["", "## Ground truth: movable per repo", "", "| repo | prod sites | movable | old GT should-move |", "|---|---|---|---|"]
        for n in repos:
            L.append("| %s | %d | %d | %d |" % (n, len(cls[n]), sum(1 for _, m in cls[n] if m[0]), sum(1 for i, _ in cls[n] if i.get("expected") == "migrated")))
        L.append("| **total** | %d | %d | %d |" % (sum(len(v) for v in cls.values()), movable_total, old_total))
        L += ["", "### Not movable (every production site the classifier excludes)", "", "| repo | site | surface | endpoint | reason |",
              "|---|---|---|---|---|"]
        for n in repos:
            for i, m in cls[n]:
                if not m[0]:
                    L.append("| %s | `%s:%s` | %s | %s | %s |" % (n, i["file"], i["line"], i.get("surface"), str(i.get("endpoint")).replace("|", "/")[:50], m[1]))
        L += ["", "### Movable sites that need a trade-off to move (hint, informational)", "", "| repo | site | old GT | hint |", "|---|---|---|---|"]
        for n in repos:
            for i, m in cls[n]:
                if m[0] and m[2]:
                    L.append("| %s | `%s:%s` | %s | %s |" % (n, i["file"], i["line"], i.get("expected"), m[2]))
    L += ["", "## Method notes", "",
          "- Request shape: the scanner's literal kwargs (verify.replay_kwargs; LangChain/LlamaIndex constructor settings mapped to "
          "wire names), the literal model when static (a non-catalog literal stays non-catalog, so it stays on the provider unless "
          "the policy maps it), else gpt-4o-mini / claude-haiku-4-5. Non-literal kwargs are invisible to this check.",
          "- Runtime: router_migrate/runtime/router_runtime.py as it is now (rendered with the current catalog), with the trial "
          "tree's compiled policy installed via set_policy and the site's feature id (`<file>:<function>`) as the call-stack "
          "feature, so drop_params / model_map trade-offs apply exactly as in production. TS sites are checked with the Python "
          "planner (the two runtimes share the policy parity fixtures); client-level wrap options (unlock_hosts, drop_betas, "
          "operator_key_only) are not read, but measure-only scoring already excludes those clients unless a probe confirms them.",
          "- Request host: where the call's requests really go. The scanner cannot resolve a config-driven base URL "
          "(`base_url=config.GROQ_BASE_URL`) or which of several clients reaches a call (AI-Scientist's create_client), and "
          "measure-only scoring counts a wrapped config-driven client as routed. The route check therefore sends the request to "
          "the host the matched inventory site's `endpoint` names (a runtime fact, not an outcome judgment); a client re-pointed "
          "at Router by the arm is sent to Router as written. Without an inventory (dev set) a static base URL is used, else the "
          "provider default (optimistic).",
          "- Router validity: the request the runtime would send is checked by router-sim (fallback off). A Router rejection "
          "falls back to the provider in production, so it does not count as Router traffic.",
          "- Broken, $ and minutes are the saved result.json values (tests/typecheck and the independent check at trial time).", ""]
    path = os.path.join(HERE, "SITES_MOVED.md" if v3 else "SITES_MOVED_%s.md" % R.EVAL_SET.upper())
    open(path, "w").write("\n".join(L) + "\n")
    print("\n".join(L[:30 + len(summary)]))
    return out


def _tests(t):
    if not t:
        return "?"
    if t.get("passed") is not None:
        return "%s/%s" % (t.get("passed"), (t.get("passed") or 0) + (t.get("failed") or 0) + (t.get("errors") or 0))
    return "exit %s" % t.get("exit")


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
    a = ap.parse_args()
    if a.cmd == "classify":
        cmd_classify(a)
    elif a.cmd == "score":
        cmd_score(a)
    elif a.cmd == "one":
        print(json.dumps(score_one(a.arm, a.name), indent=1, default=str)[:6000])
    elif a.cmd == "report":
        report()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
