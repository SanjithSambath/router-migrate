"""v2 local dashboard: one self-contained page (page.html), the frozen REPORT.html renderer and a hardened stdlib server."""
from .render import render_page, render_report, write_report  # noqa: F401
from .server import serve  # noqa: F401
