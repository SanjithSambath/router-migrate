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
    for m in re.finditer(r"^=+ (.*?(?:passed|failed|error|skipped|no tests ran).*?) in [\d.]+s", out, re.M):  # pytest
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


def replay(plan, base_url, key, catalog, budget=None, tag="sim"):
    """One synthetic request per migrated site through the vendored Python runtime and the real SDKs.

    Each request mirrors the site's surface, model and stream flag; message content is synthetic. Returns per-site results.
    """
    import httpx
    import openai
    import anthropic
    tmp = tempfile.mkdtemp(prefix="rm-replay-")
    p = os.path.join(tmp, "router_runtime.py")
    open(p, "w").write(render("router_runtime.py", catalog))
    old = {k: os.environ.get(k) for k in ("RAMP_ROUTER_API_KEY", "RAMP_ROUTER_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    os.environ.update(RAMP_ROUTER_API_KEY=key, RAMP_ROUTER_BASE_URL=base_url, OPENAI_API_KEY="unused-provider-key", ANTHROPIC_API_KEY="unused-provider-key")
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
    try:
        for s in plan["sites"]:
            if s["outcome"] != "migrated":
                continue
            fam = _family(s)
            m = s.get("model") or {}
            model = m.get("value") if m.get("static") and m.get("value") else DEFAULT_MODEL[fam]
            stream = bool(s.get("stream"))
            surface = s["surface"]
            sig = (fam, surface, model, stream)
            if sig in seen:  # identical request shape already checked: reuse the verdict
                results.append(dict(seen[sig], site_id=s["id"], reused=True))
                continue
            if budget is not None and spent >= budget:
                results.append({"site_id": s["id"], "ok": None, "detail": "budget reached", "surface": surface})
                continue
            r = {"site_id": s["id"], "surface": surface, "model": model, "stream": stream, "family": fam}
            try:
                if fam == "anthropic":
                    c = anthropic.Anthropic(http_client=httpx.Client(transport=rt.RouterTransport(inner=NoProvider())))
                    if surface == "messages.count_tokens":
                        out = c.messages.count_tokens(model=model, messages=[{"role": "user", "content": "Reply with exactly: pong"}])
                        r.update(ok=out.input_tokens > 0, detail="count_tokens")
                    elif stream:
                        with c.messages.stream(model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with exactly: pong"}]) as st:
                            text = "".join(st.text_stream)
                        r.update(ok=True, detail=text[:40])
                    else:
                        out = c.messages.create(model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with exactly: pong"}])
                        r.update(ok=out.stop_reason is not None, detail=(out.content[0].text if out.content and hasattr(out.content[0], "text") else "")[:40])
                else:
                    c = openai.OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(inner=NoProvider())))
                    if surface == "responses":
                        out = c.responses.create(model=model, input="Reply with exactly: pong", max_output_tokens=16, stream=stream)
                        if stream:
                            evs = [e.type for e in out]
                            r.update(ok="response.completed" in evs or "response.incomplete" in evs, detail="%d events" % len(evs))
                        else:
                            spent += float((out.usage.model_extra or {}).get("cost") or 0) if out.usage else 0
                            r.update(ok=True, detail=out.output_text[:40])
                    else:
                        kw = dict(model=model, messages=[{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=16)
                        if stream:
                            chunks = list(c.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kw))
                            text = "".join((ch.choices[0].delta.content or "") for ch in chunks if ch.choices)
                            usage = [ch.usage for ch in chunks if ch.usage]
                            spent += float((usage[-1].model_extra or {}).get("cost") or 0) if usage else 0
                            r.update(ok=any(ch.choices and ch.choices[0].finish_reason for ch in chunks), detail=text[:40])
                        else:
                            out = c.chat.completions.create(**kw)
                            spent += float((out.usage.model_extra or {}).get("cost") or 0) if out.usage else 0
                            r.update(ok=out.choices[0].finish_reason in ("stop", "length"), detail=(out.choices[0].message.content or "")[:40])
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
    return {"results": results, "spent_usd": round(spent, 6)}


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
    sr = [r for r in out["sim_replay"]["results"] if r.get("ok") is not None]
    print("verify: syntax %d/%d ok | tests before %s after %s | sim: %s/%s replayed sites valid%s" % (
        out["syntax"]["checked"] - len(out["syntax"]["failed"]), out["syntax"]["checked"], _tc(out.get("tests_before")), _tc(out.get("tests_after")),
        sum(1 for r in sr if r["ok"]), len(sr),
        ("; repo tests sent %d requests to router-sim, %d valid" % (out["sim_tests"]["requests"], out["sim_tests"]["valid"])) if out.get("sim_tests") else ""))
    return out


def _tc(t):
    if not t or t.get("skipped"):
        return "n/a"
    if t.get("passed") is None:
        return "exit %s" % t.get("exit")
    return "%s passed/%s failed" % (t["passed"], (t.get("failed") or 0) + (t.get("errors") or 0))
