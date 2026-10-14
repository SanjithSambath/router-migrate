#!/usr/bin/env python3
"""Drive the live dashboard the way a person does, after `router-migrate run <repo> --agent claude` (migrate, gate, improve):
approve a card in the browser, click "Apply N changes", read the result panel, then run the full `finish` gate.

  .venv/bin/python eval/v3/e2e_dashboard.py <repo> <outdir> (--all | --approve <card-id-or-title-substring> ...) [--prefix P]

Serves the same dashboard server `run` serves (agent.serve_dashboard: 127.0.0.1, session token), drives it with headless
Chromium (network blocked except 127.0.0.1 and Google Fonts), and writes to <outdir>: apply.txt (the result panel's text),
finish.txt, policy_probe.json (the Router request body the compiled policy now produces for each routed feature, built
offline by the repo's own vendored runtime), and screenshots under eval/v3/screenshots/<prefix>-*.png."""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(ROOT, ".tools", "ms-playwright"))
from playwright.sync_api import sync_playwright  # noqa: E402
from router_migrate import agent  # noqa: E402

PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import router_runtime as rt
out = {}
for feat, model in json.loads(sys.argv[2]).items():
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    nb, _ = rt.chat_to_responses(dict(body), model, (rt.CATALOG.get(model) or {}).get("o") or "openai", feat)
    rt._apply_policy(nb, body, feat)
    out[feat] = {k: nb.get(k) for k in ("model", "models", "metadata", "service_tier", "allow_flex_tier", "prompt_cache_key") if nb.get(k) is not None}
print(json.dumps(out))
'''


class _Nothing(Exception):
    pass


def applicable(recs):
    """Cards a person could apply now: not applied yet, not a model change (those are recommendations only), and of an
    either/or pair (Flex vs fallbacks on one feature) only the suggested one, as the card's note says."""
    out, taken = [], set()
    pending = [c for c in recs["cards"] if c.get("applies", True) and c.get("state") in (None, "new")]
    for c in sorted(pending, key=lambda c: c["rule"] != "tier.flex_background"):
        if taken & set(c.get("conflicts_with") or []):
            continue
        out.append(c)
        taken.add(c["id"])
    return sorted(out, key=lambda c: pending.index(c))


def probe_requests(root, outdir, log=print):
    """What each feature touched by an applied card now sends to Router, built offline by the repo's own vendored runtime
    (nothing is sent). Chat-completions features only: a Messages-surface feature gets a note (policy keys don't apply)."""
    recs = json.load(open(os.path.join(root, ".router-migrate", "recommendations.json")))
    by_feat = {}
    for sg in recs["signals"].values():
        by_feat.setdefault(sg["feature"], sg.get("resolved_model"))
    feats, notes = {}, {}
    for c in recs["cards"]:
        if c.get("state") != "applied":
            continue
        for f in c["scope"]["features"]:
            if c["rule"] == "unlock.stated_loss":
                notes[f] = ("Anthropic Messages surface: Router serves it on /v1/messages with %s dropped; Router-only policy keys "
                            "are not applied there" % ", ".join(c.get("rule_inputs", {}).get("betas") or ["the beta header"]))
            elif by_feat.get(f) or (c.get("rule_inputs") or {}).get("model"):
                feats[f] = by_feat.get(f) or c["rule_inputs"]["model"]
    rt_dir = next((dp for dp, ds, fs in os.walk(root) if "router_runtime.py" in fs and ".router-migrate" not in dp), None)
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    pr = subprocess.run([sys.executable, "-I", "-c", PROBE, rt_dir or root, json.dumps(feats)], capture_output=True, text=True, env=env, timeout=60)
    probe = json.loads(pr.stdout.strip().splitlines()[-1]) if pr.returncode == 0 and pr.stdout.strip() else {"error": pr.stderr[-800:]}
    probe.update({f: {"note": n} for f, n in notes.items()})
    json.dump(probe, open(os.path.join(outdir, "policy_probe.json"), "w"), indent=1)
    for f, body in probe.items():
        log("request body for %s: %s" % (f.split(":")[-1], json.dumps(body)))
    return probe


def drive(root, outdir, approve=None, prefix="e2e", log=print):
    """approve: list of card id/title substrings, or None for every applicable card. Returns a summary dict."""
    root = os.path.abspath(root)
    os.makedirs(outdir, exist_ok=True)
    shots = os.path.join(HERE, "screenshots")
    recs = json.load(open(os.path.join(root, ".router-migrate", "recommendations.json")))
    if approve is None:
        cards = applicable(recs)
    else:
        cards = []
        for want in approve:
            c = next((c for c in recs["cards"] if want in c["id"] or want in c["title"]), None)
            if c is None:
                raise SystemExit("no card matches %r" % want)
            cards.append(c)
    srv, url = agent.serve_dashboard(root, open_browser=False)
    allowed = ("http://127.0.0.1:%d/" % srv.port, "https://fonts.googleapis.com/", "https://fonts.gstatic.com/", "data:")
    errors, text = [], ""
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1440, "height": 1100}, device_scale_factor=2, color_scheme="dark", reduced_motion="reduce")
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(allowed) else r.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url + "#improve", wait_until="load")
            page.wait_for_selector('.nav-item[data-tab="improve"][aria-current="page"]')
            for c in cards:
                sel = 'button[data-act="decide"][data-a="approve"][data-id="%s"]' % c["id"]
                page.locator(sel).scroll_into_view_if_needed()
                page.locator(sel).click()
                page.wait_for_selector('#imp-%s .pill.solar' % c["id"])  # "Approved · apply to go live"
                log("approved in the UI: %s  %s" % (c["id"], c["title"][:100]))
            page.evaluate("() => window.scrollTo(0, 0)")
            if not cards:  # nothing migrated, or nothing applicable: the page says so; Apply stays disabled
                page.screenshot(path=os.path.join(shots, "%s-nothing-to-apply.png" % prefix))
                b.close()
                raise _Nothing()
            page.screenshot(path=os.path.join(shots, "%s-approved.png" % prefix))
            page.locator('.sticky button[data-act="apply"]').click()
            page.wait_for_selector(".panel.result", timeout=300000)
            panel = page.locator(".panel.result")
            panel.scroll_into_view_if_needed()
            page.evaluate("() => window.scrollBy(0, -120)")
            page.wait_for_timeout(2500)  # let the toast fade
            page.screenshot(path=os.path.join(shots, "%s-applied.png" % prefix))
            text = panel.inner_text()
            b.close()
    except _Nothing:
        text = "no applicable card: nothing to approve or apply"
    finally:
        srv.shutdown()
    open(os.path.join(outdir, "apply.txt"), "w").write(text + "\n")
    log(text)
    if errors:
        raise SystemExit("page errors: %s" % errors)
    # the full gate, as the result panel says to run next
    import run_v3 as R
    fin = subprocess.run([R.RM, "finish", root], cwd=root, env=R.agent_env(), capture_output=True, text=True, timeout=1800)
    open(os.path.join(outdir, "finish.txt"), "w").write(fin.stdout + fin.stderr)
    log(fin.stdout.strip())
    probe = probe_requests(root, outdir, log)
    gate_line = next((l for l in text.splitlines() if l.startswith("quick gate")), "")
    return {"approved_in_ui": [c["id"] for c in cards], "apply_output": text, "quick_gate_ok": gate_line.endswith(": ok") or not cards,
            "finish_ok": fin.returncode == 0 and "finish: OK" in fin.stdout, "probe": probe}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("outdir")
    ap.add_argument("--approve", action="append", help="substring of a card id or title to approve in the UI")
    ap.add_argument("--all", action="store_true", help="approve every applicable card")
    ap.add_argument("--prefix", default="e2e")
    a = ap.parse_args()
    if not a.all and not a.approve:
        ap.error("--all or --approve")
    out = drive(a.repo, a.outdir, None if a.all else a.approve, a.prefix)
    return 0 if out["finish_ok"] and out["quick_gate_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
