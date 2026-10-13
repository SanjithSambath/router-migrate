"""verify: tests before vs after, syntax checks, router-sim validation, optional live replay with a $ cap."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .catalog import Catalog
from .runtime import render

STATE = ".router-migrate"
SIM_KEY = "rm-sim-key-not-a-real-key"


# ------------------------------------------------------------------------------------------------ tests
def parse_counts(out):
    """Pass/fail counts from pytest, jest/vitest, node:test/tap, mocha or manten output. Multiple runs in one command are summed."""
    c = {"passed": None, "failed": None, "errors": None, "skipped": None}

    def add(k, v):
        c[k] = (c[k] or 0) + v

    found = False
    for m in re.finditer(r"^(?:=+ )?((?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, )?)+|no tests ran) in [\d.]+s", out, re.M):  # pytest (-q prints no === banner)
        found = True
        for k, pat in (("passed", r"(\d+) passed"), ("failed", r"(\d+) failed"), ("errors", r"(\d+) errors?"), ("skipped", r"(\d+) skipped")):
            mm = re.search(pat, m.group(1))
            add(k, int(mm.group(1)) if mm else 0)
    for m in re.finditer(r"^Tests:\s+(.*)$", out, re.M):  # jest / vitest
        found = True
        for k, pat in (("passed", r"(\d+) passed"), ("failed", r"(\d+) failed"), ("skipped", r"(\d+) skipped")):
            mm = re.search(pat, m.group(1))
            add(k, int(mm.group(1)) if mm else 0)
        add("errors", 0)
    for m in re.finditer(r"^Test Suites:\s+(\d+) failed", out, re.M):  # suites that failed to load
        add("errors", int(m.group(1)) if not re.search(r"Tests:", out) else 0)
    if found:
        return c
    p = re.findall(r"^(?:#|\u2139) pass (\d+)", out, re.M)  # node:test (tap "# pass N", spec reporter "ℹ pass N")
    if p:
        f = re.findall(r"^(?:#|\u2139) fail (\d+)", out, re.M)
        c.update(passed=sum(map(int, p)), failed=sum(map(int, f)), errors=0, skipped=0)
        return c
    p = re.findall(r"(\d+) passing", out)  # mocha
    if p:
        f = re.findall(r"(\d+) failing", out)
        c.update(passed=sum(map(int, p)), failed=sum(map(int, f)), errors=0, skipped=0)
        return c
    p = re.findall(r"^\s*(\d+) (?:tests? )?passed\s*$", out, re.M)  # manten ("79 passed") and similar
    if p:
        f = re.findall(r"^\s*(\d+) (?:tests? )?failed\s*$", out, re.M)
        c.update(passed=sum(map(int, p)), failed=sum(map(int, f)), errors=0, skipped=0)
    return c


def run_tests(cmd, cwd, env=None, timeout=1800):
    t = time.time()
    e = dict(os.environ)
    e.pop("RAMP_ROUTER_API_KEY", None)  # 'before/after' are measured with Router off: the default production state after apply
    e.update(env or {})
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=True, env=e, capture_output=True, text=True, timeout=timeout)
        out, code = (r.stdout or "") + "\n" + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired as ex:
        out, code = ((ex.stdout or b"").decode("utf-8", "replace") if isinstance(ex.stdout, bytes) else (ex.stdout or "")) + "\nTIMEOUT", 124
    res = {"cmd": cmd, "exit": code, "seconds": round(time.time() - t, 1), "tail": out[-3000:], "ts_errors": out.count("error TS")}
    res.update(parse_counts(out))
    return res


def detect_test_cmd(root):
    if os.path.exists(os.path.join(root, "package.json")):
        try:
            pkg = json.load(open(os.path.join(root, "package.json")))
            if (pkg.get("scripts") or {}).get("test"):
                return "npm test --silent"
        except ValueError:
            pass
    if any(os.path.exists(os.path.join(root, f)) for f in ("pytest.ini", "conftest.py", "tests", "test")) or "pytest" in _read(os.path.join(root, "pyproject.toml")):
        return "%s -m pytest -q -p no:cacheprovider" % sys.executable
    return None


def _read(p):
    try:
        return open(p).read()
    except OSError:
        return ""


def syntax_check(root, files, python=None):
    """Compile every changed Python file with the target interpreter."""
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return {"checked": 0, "failed": []}
    exe = python or sys.executable
    failed = []
    for f in py:
        r = subprocess.run([exe, "-m", "py_compile", os.path.join(root, f)], capture_output=True, text=True)
        if r.returncode:
            failed.append({"file": f, "error": r.stderr.strip()[-500:]})
    return {"checked": len(py), "failed": failed, "python": exe}


# ------------------------------------------------------------------------------------------------ replay
DEFAULT_MODEL = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5"}


def _family(s):
    return "anthropic" if s["sdk"] in ("anthropic", "langchain-anthropic", "vercel-ai-anthropic") or s["surface"].startswith("messages") else "openai"


# kwargs the replay never copies from a site: the request body is synthetic (messages/input), transport-level options are not
# part of the request shape, and stateful fields are planned "kept" anyway
_NOT_REPLAYED = {"messages", "input", "model", "stream", "prompt", "timeout", "max_retries", "idempotency_key", "extra_query",
                 "previous_response_id", "conversation", "background", "betas"}


def replay_kwargs(s):
    """The site's statically known literal kwargs (scanner: static_kwargs), as they would be sent."""
    return {k: v for k, v in (s.get("static_kwargs") or {}).items() if k not in _NOT_REPLAYED and not k.startswith("__")}


def _send(fn, base, kw):
    """Call an SDK method with `kw`; kwargs the installed SDK version does not know go through extra_body (same JSON body)."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    args = dict(base)
    extra = {}
    for k, v in kw.items():
        if k in params or k in ("extra_headers", "extra_body"):
            args[k] = v
        else:
            extra[k] = v
    if extra:
        args["extra_body"] = dict(args.get("extra_body") or {}, **extra)
    return fn(**args)


def replay(plan, base_url, key, catalog, budget=None, tag="sim"):
    """One request per unique request shape among migrated sites, through the vendored Python runtime and the real SDKs.

    The shape is the site's surface, stream flag, model (its literal model when it resolves in the catalog, else a default) and every
    literal kwarg the scanner captured (tools, response_format, thinking, output_config, extra_headers, temperature, max_tokens, stop,
    reasoning_effort...). Message content is synthetic. Sites with an identical shape share one request (counted once).
    Returns {"results": per-site, "unique_requests": n, "ok": n_ok, "spent_usd"}.
    """
    import httpx
    import openai
    import anthropic
    tmp = tempfile.mkdtemp(prefix="rm-replay-")
    p = os.path.join(tmp, "router_runtime.py")
    open(p, "w").write(render("router_runtime.py", catalog))
    old = {k: os.environ.get(k) for k in ("RAMP_ROUTER_API_KEY", "RAMP_ROUTER_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ROUTER_MIGRATE_FALLBACK")}
    # replay measures Router itself: its errors must surface, not be retried on the (fake) provider
    os.environ.update(RAMP_ROUTER_API_KEY=key, RAMP_ROUTER_BASE_URL=base_url, OPENAI_API_KEY="unused-provider-key", ANTHROPIC_API_KEY="unused-provider-key",
                      ROUTER_MIGRATE_FALLBACK="0")
    spec = importlib.util.spec_from_file_location("rm_replay_runtime_%s" % tag, p)
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    results, spent, seen = [], 0.0, {}

    class NoProvider(httpx.BaseTransport):
        """Anything the runtime would leave on the provider is recorded, never sent."""
        def __init__(self):
            self.real = httpx.HTTPTransport()

        def handle_request(self, req):
            if req.url.host in ("api.openai.com", "api.anthropic.com"):
                return httpx.Response(599, json={"error": {"message": "replay: would stay on provider"}})
            return self.real.handle_request(req)
    user = "Reply with exactly: pong"
    try:
        for s in plan["sites"]:
            if s["outcome"] != "migrated":
                continue
            fam = _family(s)
            m = s.get("model") or {}
            lit = m.get("value") if m.get("static") and m.get("value") else None
            model = lit if lit and catalog.resolve(lit)[0] else DEFAULT_MODEL[fam]
            stream = bool(s.get("stream"))
            surface = s["surface"]
            kw = replay_kwargs(s)
            sig = (fam, surface, model, stream, json.dumps(kw, sort_keys=True, default=str))
            if sig in seen:  # identical request shape already sent: reuse the verdict, count it once
                results.append(dict(seen[sig], site_id=s["id"], reused=True))
                continue
            if budget is not None and spent >= budget:
                results.append({"site_id": s["id"], "ok": None, "detail": "budget reached", "surface": surface})
                continue
            r = {"site_id": s["id"], "surface": surface, "model": model, "stream": stream, "family": fam, "kwargs": sorted(kw)}
            try:
                if fam == "anthropic":
                    c = anthropic.Anthropic(http_client=httpx.Client(transport=rt.RouterTransport(inner=NoProvider())))
                    msgs = [{"role": "user", "content": user}]
                    if surface == "messages.count_tokens":
                        kw.pop("max_tokens", None)
                        out = _send(c.messages.count_tokens, dict(model=model, messages=msgs), kw)
                        r.update(ok=out.input_tokens > 0, detail="count_tokens")
                    elif stream:
                        base = dict(model=model, max_tokens=kw.pop("max_tokens", 16), messages=msgs)
                        with _send(c.messages.stream, base, kw) as st:
                            text = "".join(st.text_stream)
                        r.update(ok=True, detail=text[:40])
                    else:
                        out = _send(c.messages.create, dict(model=model, max_tokens=kw.pop("max_tokens", 16), messages=msgs), kw)
                        r.update(ok=out.stop_reason is not None, detail=(out.content[0].text if out.content and hasattr(out.content[0], "text") else "")[:40])
                else:
                    c = openai.OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(inner=NoProvider())))
                    if surface == "responses":
                        base = dict(model=model, input=user, max_output_tokens=kw.pop("max_output_tokens", 16), stream=stream)
                        out = _send(c.responses.create, base, kw)
                        if stream:
                            evs = [e.type for e in out]
                            r.update(ok="response.completed" in evs or "response.incomplete" in evs, detail="%d events" % len(evs))
                        else:
                            spent += float((out.usage.model_extra or {}).get("cost") or 0) if out.usage else 0
                            r.update(ok=True, detail=out.output_text[:40])
                    else:
                        base = dict(model=model, messages=[{"role": "user", "content": user}])
                        if "max_tokens" not in kw and "max_completion_tokens" not in kw:
                            base["max_tokens"] = 16
                        if stream:
                            base.update(stream=True, stream_options=kw.pop("stream_options", None) or {"include_usage": True})
                            chunks = list(_send(c.chat.completions.create, base, kw))
                            text = "".join((ch.choices[0].delta.content or "") for ch in chunks if ch.choices)
                            usage = [ch.usage for ch in chunks if ch.usage]
                            spent += float((usage[-1].model_extra or {}).get("cost") or 0) if usage else 0
                            r.update(ok=any(ch.choices and ch.choices[0].finish_reason for ch in chunks), detail=text[:40])
                        else:
                            out = _send(c.chat.completions.create, base, kw)
                            spent += float((out.usage.model_extra or {}).get("cost") or 0) if out.usage else 0
                            r.update(ok=out.choices[0].finish_reason in ("stop", "length", "tool_calls", "function_call"),
                                     detail=(out.choices[0].message.content or "")[:40])
            except Exception as e:
                status = getattr(e, "status_code", None)
                r.update(ok=False, detail="%s %s: %s" % (type(e).__name__, status or "", str(e)[:200]))
                if status in (402, 429):
                    results.append(r)
                    r["stop"] = True
                    break
            seen[sig] = {k: v for k, v in r.items() if k != "site_id"}
            results.append(r)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    uniq = [r for r in results if not r.get("reused") and r.get("ok") is not None]
    return {"results": results, "unique_requests": len(uniq), "ok": sum(1 for r in uniq if r["ok"]), "spent_usd": round(spent, 6)}


# ------------------------------------------------------------------------------------------------ verify
def _git(root, *args):
    return subprocess.run(["git"] + list(args), cwd=root, capture_output=True, text=True)


def baseline_tests(root, cmd, files, timeout, env=None):
    """Run tests with the router-migrate changes temporarily removed (git stash of exactly the changed + vendored files)."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return {"skipped": "not a git repository; pass --baseline <unmodified copy>"}
    paths = [f for f in files if os.path.exists(os.path.join(root, f))]
    if not paths:
        return run_tests(cmd, root, env, timeout)
    r = _git(root, "stash", "push", "--include-untracked", "-m", "router-migrate-baseline", "--", *paths)
    if r.returncode != 0:
        return {"skipped": "git stash failed: %s" % r.stderr.strip()[-300:]}
    try:
        return run_tests(cmd, root, env, timeout)
    finally:
        pop = _git(root, "stash", "pop")
        if pop.returncode != 0:
            print("WARNING: `git stash pop` failed; your router-migrate changes are in `git stash list`.", file=sys.stderr)


def sim_test_run(root, cmd, timeout, env=None):
    """Run the repo's tests with Router active against router-sim; every request real code sends is validated and logged."""
    from .sim.server import serve
    log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
    srv, sim, base = serve(log_path=log)
    try:
        e = dict(env or {}, RAMP_ROUTER_API_KEY=SIM_KEY, RAMP_ROUTER_BASE_URL=base)
        t = time.time()
        ee = dict(os.environ)
        ee.update(e)
        try:
            r = subprocess.run(cmd, cwd=root, shell=True, env=ee, capture_output=True, text=True, timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            code = r.returncode
        except subprocess.TimeoutExpired:
            out, code = "TIMEOUT", 124
        recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
        res = {"exit": code, "seconds": round(time.time() - t, 1), "requests": len(recs), "valid": sum(1 for x in recs if x.get("valid")),
               "invalid": [{"path": x["path"], "error": x.get("error"), "model": x.get("model"), "feature": x.get("feature")} for x in recs if not x.get("valid")][:20],
               "features": sorted({x.get("feature") for x in recs if x.get("feature")})[:50]}
        res.update(parse_counts(out))
        return res
    finally:
        srv.shutdown()


def verify(root, args):
    from .cli import load, save, print_summary
    plan = load(root, "plan.json")
    applied = load(root, "apply.json") or {"files_changed": [], "runtime_files": []}
    if plan is None:
        print("verify: run `router-migrate apply` first")
        return None
    cat = Catalog.load()
    cmd = getattr(args, "test_cmd", None) or detect_test_cmd(root)
    timeout = getattr(args, "timeout", 1800)
    out = {"test_cmd": cmd, "syntax": syntax_check(root, applied["files_changed"] + applied["runtime_files"])}
    if cmd:
        if getattr(args, "baseline", None):
            out["tests_before"] = run_tests(cmd, args.baseline, timeout=timeout)
        else:
            out["tests_before"] = baseline_tests(root, cmd, applied["files_changed"] + applied["runtime_files"], timeout)
        out["tests_after"] = run_tests(cmd, root, timeout=timeout)
        out["sim_tests"] = sim_test_run(root, cmd, timeout)
    else:
        out["tests_before"] = out["tests_after"] = {"skipped": "no test command found (pass --test-cmd)"}
    from .sim.server import serve
    srv, sim, base = serve()
    try:
        out["sim_replay"] = replay(plan, base, SIM_KEY, cat)
    finally:
        srv.shutdown()
    if getattr(args, "live", False):
        key = os.environ.get("RAMP_ROUTER_API_KEY")
        if not key:
            out["live_replay"] = {"skipped": "RAMP_ROUTER_API_KEY not set"}
        else:
            out["live_replay"] = replay(plan, "https://api.router.com", key, Catalog.load(live=True, api_key=key), budget=getattr(args, "budget", 0.25), tag="live")
    save(root, "verify.json", out)
    sr = out["sim_replay"]
    st = out.get("sim_tests")
    print("verify: syntax %d/%d ok | tests before %s after %s | sim: %s/%s unique request shapes valid (static kwargs replayed)%s" % (
        out["syntax"]["checked"] - len(out["syntax"]["failed"]), out["syntax"]["checked"], _tc(out.get("tests_before")), _tc(out.get("tests_after")),
        sr["ok"], sr["unique_requests"],
        ("" if not st else "; repo tests with Router on: N/A (0 requests reached router-sim: the suite mocks the SDK/HTTP)" if not st["requests"]
         else "; repo tests sent %d requests to router-sim, %d valid" % (st["requests"], st["valid"]))))
    return out


def _tc(t):
    if not t or t.get("skipped"):
        return "n/a"
    if t.get("passed") is None:
        return "exit %s" % t.get("exit")
    return "%s passed/%s failed" % (t["passed"], (t.get("failed") or 0) + (t.get("errors") or 0))
