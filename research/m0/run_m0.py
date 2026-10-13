#!/usr/bin/env python3
"""M0 live measurements for router-migrate v2 (budget < $1). Raw results -> research/m0/raw/*.json, spend -> eval/live_spend_v2.jsonl.

  RAMP_ROUTER_API_KEY=... .venv/bin/python research/m0/run_m0.py

Checks (SPEC_V2 M0): (A) account strategy settings snapshot, read-only; (B) Flex discount via usage.cost, identical prompts,
flex vs default, n>=5 each; (C) `models` fallback list: raw HTTP, openai-python extra_body, openai-node; (D) service_tier
"default" vs Jev and (E) Switchyard from app code, each as far as the account's current settings allow (no setting changes).
Never prints or stores the key; request headers are not saved. Stops on 402/429.
"""
import json
import os
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "research", "m0", "raw")
SPEND = os.path.join(ROOT, "eval", "live_spend_v2.jsonl")
BASE = "https://api.router.com"
APP = "https://app.router.com"
KEY = os.environ.get("RAMP_ROUTER_API_KEY", "").strip()
UA = "router-migrate-m0/2.0"
BUDGET = 1.00
spent = 0.0
n = 0


class Stop(Exception):
    pass


def log_spend(check, model, cost, estimated, status):
    global spent
    spent += cost or 0.0
    with open(SPEND, "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": "M0", "check": check, "model": model,
                            "cost_usd": cost, "estimated": estimated, "status": status}) + "\n")


def save(name, obj):
    global n
    n += 1
    with open(os.path.join(RAW, "%03d_%s.json" % (n, name)), "w") as f:
        json.dump(obj, f, indent=1)


def call(check, method, path, body=None, base=BASE):
    if spent >= BUDGET:
        raise Stop("budget reached")
    t = time.time()
    r = httpx.request(method, base + path, json=body, timeout=120,
                      headers={"Authorization": "Bearer " + KEY, "User-Agent": UA, "Content-Type": "application/json"})
    ms = int((time.time() - t) * 1000)
    try:
        j = r.json()
    except ValueError:
        j = {"_text": r.text[:500]}
    usage = j.get("usage") if isinstance(j, dict) else None
    cost = (usage or {}).get("cost")
    model = (body or {}).get("model") or (body or {}).get("models")
    if base == BASE and method == "POST":
        log_spend(check, model, cost if cost is not None else 0.0, cost is None, r.status_code)
    save(check, {"request": {"method": method, "path": path, "body": body}, "status": r.status_code, "ms": ms,
                 "headers": {k: v for k, v in r.headers.items() if k.lower().startswith(("x-router", "x-request-id"))}, "body": j})
    if r.status_code in (402, 429):
        raise Stop("HTTP %d on %s" % (r.status_code, check))
    return r.status_code, j, ms, r.headers


def summary_of(j):
    if not isinstance(j, dict):
        return {}
    u = j.get("usage") or {}
    return {"model": j.get("model"), "service_tier": j.get("service_tier"), "status": j.get("status"), "cost": u.get("cost"),
            "in": u.get("input_tokens"), "out": u.get("output_tokens"), "error": (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else None}


def main():
    if not KEY:
        sys.exit("RAMP_ROUTER_API_KEY not set")
    os.makedirs(RAW, exist_ok=True)
    out = {"ran_at": time.strftime("%Y-%m-%d %H:%M %Z")}
    try:
        # (A) read-only settings snapshot
        st, s, _, _ = call("settings_strategies", "GET", "/session-usage/strategies", base=APP)
        out["settings"] = {k: s.get(k) for k in ("allow_flex_tier_default", "auto_routing_enabled", "switchyard_routing_enabled")} if st == 200 else {"status": st}

        # (B) Flex discount: identical prompt, gpt-5-nano and gpt-5.4-mini; flex (allow_flex_tier true) vs default (service_tier default)
        prompt = "Reply with the single word: pong"
        flex = {}
        for model in ("gpt-5-nano", "gpt-5.4-mini"):
            for arm, extra in (("default", {"service_tier": "default"}), ("flex", {"allow_flex_tier": True})):
                rows = []
                for i in range(5):
                    body = dict({"model": model, "input": prompt, "max_output_tokens": 16, "reasoning": {"effort": "minimal"} if model == "gpt-5-nano" else {"effort": "none"}}, **extra)
                    st, j, ms, _ = call("flex_%s_%s_%d" % (model, arm, i), "POST", "/v1/responses", body)
                    rows.append(dict(summary_of(j), http=st, ms=ms))
                flex["%s/%s" % (model, arm)] = rows
        out["flex"] = flex

        # (C) `models` fallback list
        fb = {}
        st, j, ms, _ = call("models_list_raw_first_ok", "POST", "/v1/responses", {"models": ["gpt-5-nano", "gpt-5.4-mini"], "input": prompt, "max_output_tokens": 16})
        fb["raw_first_ok"] = dict(summary_of(j), http=st)
        st, j, ms, _ = call("models_list_raw_first_403", "POST", "/v1/responses", {"models": ["gpt-5.3-codex", "gpt-5-nano"], "input": prompt, "max_output_tokens": 16})
        fb["raw_first_no_permission"] = dict(summary_of(j), http=st)
        st, j, ms, _ = call("models_list_raw_first_unknown", "POST", "/v1/responses", {"models": ["not-a-real-model-xyz", "gpt-5-nano"], "input": prompt, "max_output_tokens": 16})
        fb["raw_first_unknown_model"] = dict(summary_of(j), http=st)
        st, j, ms, _ = call("models_list_with_service_tier_default", "POST", "/v1/responses",
                            {"models": ["gpt-5-nano", "gpt-5.4-mini"], "input": prompt, "max_output_tokens": 16, "service_tier": "default"})
        fb["raw_with_service_tier_default"] = dict(summary_of(j), http=st)
        st, j, ms, _ = call("models_list_with_allow_flex", "POST", "/v1/responses",
                            {"models": ["gpt-5-nano", "gpt-5.4-mini"], "input": prompt, "max_output_tokens": 16, "allow_flex_tier": True})
        fb["raw_with_allow_flex_tier"] = dict(summary_of(j), http=st)
        st, j, ms, _ = call("models_list_with_provider_timeout", "POST", "/v1/responses",
                            {"models": ["gpt-5-nano", "claude-haiku-4-5"], "input": prompt, "max_output_tokens": 16, "provider_timeout": 60})
        fb["raw_cross_vendor_with_provider_timeout"] = dict(summary_of(j), http=st)
        # openai-python: responses.create without model, models via extra_body
        py = subprocess.run([sys.executable, "-c", (
            "import os, json, openai\n"
            "c = openai.OpenAI(api_key=os.environ['RAMP_ROUTER_API_KEY'], base_url='%s/v1')\n"
            "try:\n"
            "    r = c.responses.create(input='%s', max_output_tokens=16, extra_body={'models': ['gpt-5.3-codex', 'gpt-5-nano']})\n"
            "    u = r.usage.model_extra if r.usage else {}\n"
            "    print(json.dumps({'ok': True, 'model': r.model, 'cost': (u or {}).get('cost'), 'sdk': openai.__version__}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'ok': False, 'error': type(e).__name__ + ': ' + str(e)[:300], 'sdk': openai.__version__}))\n") % (BASE, prompt)],
            capture_output=True, text=True, timeout=120, env=dict(os.environ))
        res = json.loads((py.stdout.strip().splitlines() or ["{}"])[-1] or "{}")
        fb["openai_python_extra_body"] = res
        log_spend("models_list_openai_python", ["gpt-5.3-codex", "gpt-5-nano"], res.get("cost") or 0.0, res.get("cost") is None, 200 if res.get("ok") else 0)
        save("models_list_openai_python", res)
        js = ("const OpenAI = require('openai'); const c = new OpenAI({apiKey: process.env.RAMP_ROUTER_API_KEY, baseURL: '%s/v1'});"
              "c.responses.create({input: '%s', max_output_tokens: 16, models: ['gpt-5.3-codex', 'gpt-5-nano']})"
              ".then(r => console.log(JSON.stringify({ok: true, model: r.model, cost: r.usage && r.usage.cost})))"
              ".catch(e => console.log(JSON.stringify({ok: false, error: String(e).slice(0, 300)})));") % (BASE, prompt)
        node = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=120, cwd=ROOT, env=dict(os.environ))
        res = json.loads((node.stdout.strip().splitlines() or ["{}"])[-1] or "{}")
        fb["openai_node_unknown_param"] = res
        log_spend("models_list_openai_node", ["gpt-5.3-codex", "gpt-5-nano"], res.get("cost") or 0.0, res.get("cost") is None, 200 if res.get("ok") else 0)
        save("models_list_openai_node", res)
        out["fallback"] = fb

        # (D) service_tier "default" vs Jev: only observable when auto routing is enabled on the account
        out["jev"] = {"auto_routing_enabled": out["settings"].get("auto_routing_enabled"),
                      "measured": False, "why": "Jev is off on this account and enabling it is an account-setting change (not allowed)"}
        # (E) Switchyard from app code
        st, j, ms, _ = call("switchyard_model_from_app", "POST", "/v1/responses", {"model": "switchyard", "input": prompt, "max_output_tokens": 16})
        out["switchyard"] = dict(summary_of(j), http=st, switchyard_routing_enabled=out["settings"].get("switchyard_routing_enabled"))
    except Stop as e:
        out["stopped"] = str(e)
    out["spent_usd"] = round(spent, 6)
    out["calls_saved"] = n
    with open(os.path.join(ROOT, "research", "m0", "results.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
