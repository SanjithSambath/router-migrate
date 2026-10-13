"""Render the dashboard page with a bundle embedded (stdlib only).

`render_report(bundle)` returns the frozen REPORT.html: the page plus `window.__RM__` inlined, no network needed, action
buttons disabled. `render_page(bundle, mode)` is shared with the local server (mode "live").
"""
import json
import os

PLACEHOLDER = "/*__RM_BOOT__*/"
_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
MODES = ("frozen", "live")


def page_template():
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def safe_json(value):
    """JSON that is safe to inline in a <script> element.

    `<`, `>` and `&` are escaped as \\u003c etc. so no `</script>`, `<!--` or `<script` sequence can appear;
    ensure_ascii also escapes U+2028/U+2029 (JS line terminators in older engines).
    """
    s = json.dumps(value, ensure_ascii=True, sort_keys=False, default=str, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_page(bundle, mode="frozen"):
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    page = page_template()
    if page.count(PLACEHOLDER) != 1:
        raise RuntimeError("page.html must contain the boot placeholder exactly once")
    boot = "window.__RM__ = %s; window.__RM_MODE__ = %s;" % (safe_json(bundle), safe_json(mode))
    return page.replace(PLACEHOLDER, boot, 1)


def render_report(bundle):
    """The frozen REPORT.html (self-contained; decisions need `router-migrate up`)."""
    return render_page(bundle, "frozen")


def write_report(bundle, path):
    html = render_report(bundle)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)
    return path
