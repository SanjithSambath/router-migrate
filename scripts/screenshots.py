#!/usr/bin/env python3
"""Screenshot every dashboard tab of a REPORT.html (dark only): <outdir>/<tab>.png.

Usage: .venv/bin/python scripts/screenshots.py <REPORT.html> <outdir> [--width 1440] [--height 900] [--scale 2] [--fonts] [--drawer]

Needs Playwright in the project venv and Chromium under .tools/ms-playwright (never ~/Library or ~/.cache):
  .venv/bin/pip install playwright
  PLAYWRIGHT_BROWSERS_PATH=$PWD/.tools/ms-playwright .venv/bin/python -m playwright install chromium

Network is blocked by default (the report must work offline; system fonts are used). --fonts allows Google Fonts only.
--drawer also captures the Migration diff drawer (migration-drawer.png).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(ROOT, ".tools", "ms-playwright"))

TABS = ["overview", "migration", "improve", "setup", "unsupported"]
FONT_HOSTS = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com/")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report")
    ap.add_argument("outdir")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=float, default=2)
    ap.add_argument("--fonts", action="store_true", help="allow Google Fonts (otherwise all network is blocked)")
    ap.add_argument("--drawer", action="store_true", help="also capture the Migration diff drawer")
    a = ap.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright is not installed: .venv/bin/pip install playwright")

    report = os.path.abspath(a.report)
    if not os.path.isfile(report):
        sys.exit("no such file: %s" % report)
    os.makedirs(a.outdir, exist_ok=True)
    url = "file://" + report
    written = []

    def route(r):
        u = r.request.url
        if u.startswith("file://") or u.startswith("data:") or (a.fonts and u.startswith(FONT_HOSTS)):
            return r.continue_()
        return r.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for scheme in ("dark",):
                ctx = browser.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=a.scale,
                                          color_scheme=scheme, reduced_motion="reduce")
                ctx.route("**/*", route)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "#overview", wait_until="load")
                for tab in TABS:
                    page.evaluate("t => { location.hash = t; }", tab)
                    page.wait_for_selector('.tab[data-tab="%s"][aria-selected="true"]' % tab)
                    page.evaluate("() => window.scrollTo(0, 0)")
                    page.wait_for_timeout(150)
                    out = os.path.join(a.outdir, "%s.png" % tab)
                    page.screenshot(path=out, full_page=True)
                    written.append(out)
                    if tab == "migration" and a.drawer and page.query_selector('[data-act="drawer"]'):
                        page.click('[data-act="drawer"]')
                        page.wait_for_selector(".drawer")
                        out = os.path.join(a.outdir, "migration-drawer.png")
                        page.screenshot(path=out)
                        written.append(out)
                        page.keyboard.press("Escape")
                if errors:
                    sys.exit("page errors (%s): %s" % (scheme, "; ".join(errors)))
                ctx.close()
        finally:
            browser.close()
    for w in written:
        print(w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
