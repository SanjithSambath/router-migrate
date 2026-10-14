#!/usr/bin/env python3
"""Viewport screenshots of a REPORT.html (what a person sees on opening each tab), for the docs.

  .venv/bin/python eval/v3/shoot_dashboard.py <REPORT.html> <outdir> <prefix> [--width 1440 --height 1100]

Writes <prefix>-overview.png, -improve.png (scrolled to the lever grid), -improve-card.png (the first card with agent
rationale), -setup.png, -migration.png (scrolled to the agent ledger). Network blocked except Google Fonts."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(ROOT, ".tools", "ms-playwright"))
from playwright.sync_api import sync_playwright  # noqa: E402

report, outdir, prefix = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]
w = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 1440
h = int(sys.argv[sys.argv.index("--height") + 1]) if "--height" in sys.argv else 1100
os.makedirs(outdir, exist_ok=True)
FONTS = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com/")
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, color_scheme="dark", reduced_motion="reduce")
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file://", "data:") + FONTS) else r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("file://" + report + "#overview", wait_until="load")
    page.wait_for_timeout(400)

    def tab(t):
        page.evaluate("t => { location.hash = t; }", t)
        page.wait_for_selector('.tab[data-tab="%s"][aria-selected="true"]' % t)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)

    def shot(name, selector=None, offset=-90):
        if selector and page.query_selector(selector):
            page.evaluate("([s, o]) => { const e = document.querySelector(s); window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY + o); }", [selector, offset])
            page.wait_for_timeout(150)
        out = os.path.join(outdir, "%s-%s.png" % (prefix, name))
        page.screenshot(path=out)
        print(out)

    tab("overview"); shot("overview")
    tab("improve"); shot("improve", ".levers", -230)
    shot("improve-card", ".why2", -420)
    tab("setup"); shot("setup")
    tab("migration"); shot("migration", "h2", -40) if page.query_selector("h2") else shot("migration")
    b.close()
if errors:
    sys.exit("page errors: %s" % errors)
