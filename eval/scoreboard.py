"""Write eval/scoreboard.json, eval/SCOREBOARD.md and eval/scoreboard.html from per-repo rows."""
import html
import json
import os
import time

KEYS = ["repo", "commit", "sites_found", "migrated", "handoff", "kept", "flagged", "callsite_coverage", "weighted_coverage",
        "tests_before", "tests_after", "sim_valid_pct", "live_success_pct", "diff_lines", "runtime_s"]


def pct(x):
    return "-" if x is None else "%.0f%%" % (100 * x)


def tcount(t):
    if not isinstance(t, dict):
        return "-"
    if "not_run" in t:
        return "n/a"
    if t.get("typecheck"):
        return "tsc: %s errors" % t.get("ts_errors")
    s = "%d passed" % t["passed"]
    bad = (t.get("failed") or 0) + (t.get("errors") or 0)
    if bad:
        s += ", %d failed" % bad
    return s


def verdict(r):
    b, a = r.get("tests_before"), r.get("tests_after")
    if not isinstance(b, dict) or not isinstance(a, dict) or "not_run" in b or "not_run" in a:
        tc = r.get("typecheck")
        if tc:
            return ("same", "typecheck %d -> %d errors" % (tc["before_errors"], tc["after_errors"])) if tc["after_errors"] <= tc["before_errors"] else \
                ("worse", "typecheck %d -> %d errors" % (tc["before_errors"], tc["after_errors"]))
        return ("na", "no runnable tests")
    if b.get("typecheck"):
        same = (a.get("ts_errors") or 0) <= (b.get("ts_errors") or 0)
        return ("same" if same else "worse", "typecheck: %s -> %s errors" % (b.get("ts_errors"), a.get("ts_errors")))
    bb = (b.get("failed") or 0) + (b.get("errors") or 0)
    ab = (a.get("failed") or 0) + (a.get("errors") or 0)
    if a["passed"] >= b["passed"] and ab <= bb:
        return ("same", "%d/%d passing before and after" % (a["passed"], a["passed"] + ab))
    return ("worse", "%d -> %d passing" % (b["passed"], a["passed"]))


def totals(rows):
    ok = [r for r in rows if "error" not in r]
    t = {k: sum(r.get(k) or 0 for r in ok) for k in ("sites_found", "migrated", "handoff", "kept", "flagged", "router_eligible")}
    t["callsite_coverage"] = round(t["migrated"] / t["sites_found"], 4) if t["sites_found"] else None
    t["weighted_coverage"] = round(t["migrated"] / t["router_eligible"], 4) if t["router_eligible"] else None
    t["repos"] = len(rows)
    t["repos_tests_same"] = sum(1 for r in ok if verdict(r)[0] == "same")
    t["repos_tests_worse"] = sum(1 for r in ok if verdict(r)[0] == "worse")
    t["repos_no_tests"] = sum(1 for r in ok if verdict(r)[0] == "na")
    sv = [r["sim_valid_pct"] for r in ok if r.get("sim_valid_pct") is not None]
    t["sim_valid_pct_mean"] = round(sum(sv) / len(sv), 1) if sv else None
    return t


def write_all(rows, out_dir):
    t = totals(rows)
    gen = time.strftime("%Y-%m-%d %H:%M %Z")
    json.dump({"generated": gen, "definitions": DEFS, "totals": t, "repos": rows}, open(os.path.join(out_dir, "scoreboard.json"), "w"), indent=1, default=str)
    open(os.path.join(out_dir, "SCOREBOARD.md"), "w").write(markdown(rows, t, gen))
    open(os.path.join(out_dir, "scoreboard.html"), "w").write(page(rows, t, gen))


DEFS = {
    "sites_found": "production LLM sites: API calls (chat, responses, messages, embeddings, images...) and framework model constructions; tests/examples/evals excluded",
    "migrated": "sites whose traffic goes to Router when RAMP_ROUTER_API_KEY is set (client patched, surface served by Router, model in catalog or chosen at runtime)",
    "handoff": "sites that need an agent-made code change (other-vendor SDK whose model Router serves, raw HTTP, other gateways)",
    "kept": "sites that stay on their provider by design (no Router endpoint, e.g. embeddings; or another vendor's endpoint)",
    "flagged": "sites Router could serve but the tool does not move automatically (unmapped model, beta API, server-side state)",
    "callsite_coverage": "migrated / sites_found",
    "weighted_coverage": "migrated / Router-eligible sites (generation surfaces Router can serve)",
    "tests_before/after": "the repo's own offline test suite (or typecheck where it has none), Router off, before vs after migrating",
    "sim_valid_pct": "valid / total requests at router-sim from (a) the repo's tests run with Router on and (b) one synthetic replay per migrated site",
    "live_success_pct": "same replay against api.router.com (null when not run)",
}


def markdown(rows, t, gen):
    L = ["# router-migrate eval scoreboard", "", "Generated %s by `make eval` (eval/run_eval.py). %d pinned open-source repos." % (gen, len(rows)), "",
         "**Totals:** %d production LLM sites, %d migrated (%s of all sites, %s of Router-eligible sites), %d handoff, %d kept, %d flagged. "
         "Tests: %d repos unchanged, %d worse, %d without runnable tests." % (
             t["sites_found"], t["migrated"], pct(t["callsite_coverage"]), pct(t["weighted_coverage"]), t["handoff"], t["kept"], t["flagged"],
             t["repos_tests_same"], t["repos_tests_worse"], t["repos_no_tests"]), "",
         "| repo | commit | sites | migrated | handoff | kept | flagged | call-site cov. | eligible cov. | tests before | tests after | sim valid | live | diff lines | runtime |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            L.append("| %s | %s | error: %s |" % (r["repo"], r.get("commit"), r["error"]))
            continue
        L.append("| %s | `%s` | %d | %d | %d | %d | %d | %s | %s | %s | %s | %s | %s | %s | %ss |" % (
            r["repo"], r["commit"], r["sites_found"], r["migrated"], r["handoff"], r["kept"], r["flagged"], pct(r["callsite_coverage"]),
            pct(r["weighted_coverage"]), tcount(r["tests_before"]), tcount(r["tests_after"]),
            "-" if r.get("sim_valid_pct") is None else "%.0f%%" % r["sim_valid_pct"],
            "-" if r.get("live_success_pct") is None else "%.0f%%" % r["live_success_pct"], r.get("diff_lines"), r.get("runtime_s")))
    L += ["", "## Definitions", ""] + ["- **%s**: %s" % (k, v) for k, v in DEFS.items()]
    L += ["", "## Per-repo notes", ""]
    for r in rows:
        if "error" in r:
            continue
        v = verdict(r)
        L.append("- **%s** (%s; test cmd `%s`): %s. Outcomes by sdk/surface: %s" % (
            r["repo"], r.get("test_kind", "unit"), r.get("test_cmd") or "-", v[1],
            "; ".join("%s %s" % (k, ", ".join("%d %s" % (n, o) for o, n in sorted(d.items()))) for k, d in sorted((r.get("outcomes_by_sdk") or {}).items()))))
    L.append("")
    return "\n".join(L)


CSS = """
:root{color-scheme:light;--bg:#fcfcfb;--panel:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--ink3:#7a7974;--line:#e6e5e0;--track:#efeeea;
--bar:#2a78d6;--ok:#008300;--bad:#e34948;--na:#7a7974}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--panel:#222221;--ink:#ffffff;--ink2:#c3c2b7;
--ink3:#9a998f;--line:#3a3936;--track:#2e2d2b;--bar:#3987e5;--ok:#3fae3f;--bad:#e66767;--na:#9a998f}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--panel:#222221;--ink:#ffffff;--ink2:#c3c2b7;--ink3:#9a998f;--line:#3a3936;--track:#2e2d2b;
--bar:#3987e5;--ok:#3fae3f;--bad:#e66767;--na:#9a998f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--ink2);margin:0 0 24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:24px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .k{color:var(--ink2);font-size:12.5px}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:980px}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;color:var(--ink2);font-weight:600;white-space:nowrap}td.n{font-variant-numeric:tabular-nums;text-align:right}
tr:last-child td{border-bottom:0}tr:hover td{background:color-mix(in srgb,var(--bar) 6%,transparent)}
.repo{font-weight:600}.commit{color:var(--ink3);font:12px ui-monospace,Menlo,monospace}
.meter{display:flex;align-items:center;gap:8px;min-width:150px}.track{flex:1;height:8px;background:var(--track);border-radius:4px;overflow:hidden}
.fill{height:100%;background:var(--bar);border-radius:0 4px 4px 0}.meter span{width:38px;text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;white-space:nowrap}.pill i{font-style:normal;font-weight:700}
.same i{color:var(--ok)}.worse i{color:var(--bad)}.na i{color:var(--na)}
details{margin-top:24px;color:var(--ink2)}dt{font-weight:600;color:var(--ink);margin-top:8px}dd{margin:2px 0 0 0}
.tip{position:relative}.tip:hover::after{content:attr(data-tip);position:absolute;left:0;top:100%;z-index:2;margin-top:4px;background:var(--ink);color:var(--bg);
padding:6px 8px;border-radius:6px;font-size:12px;white-space:pre;max-width:520px}
"""


def page(rows, t, gen):
    esc = html.escape
    tiles = [("%d" % t["sites_found"], "production LLM sites in %d repos" % t["repos"]), ("%d" % t["migrated"], "migrated to Router"),
             (pct(t["callsite_coverage"]), "call-site coverage"), (pct(t["weighted_coverage"]), "coverage of Router-eligible sites"),
             ("%d / %d" % (t["repos_tests_same"], t["repos"] - t["repos_no_tests"]), "repos with tests unchanged (of repos with runnable checks)"),
             ("-" if t["sim_valid_pct_mean"] is None else "%.0f%%" % t["sim_valid_pct_mean"], "requests valid at router-sim (mean)")]
    trs = []
    for r in rows:
        if "error" in r:
            trs.append("<tr><td class=repo>%s</td><td colspan=11>error: %s</td></tr>" % (esc(r["repo"]), esc(r["error"])))
            continue
        v = verdict(r)
        icon = {"same": "&#10003;", "worse": "&#10005;", "na": "&ndash;"}[v[0]]
        label = {"same": "unchanged", "worse": "regressed", "na": "not measured"}[v[0]]
        tip = "before: %s\nafter: %s\ncmd: %s" % (tcount(r.get("tests_before")), tcount(r.get("tests_after")), r.get("test_cmd") or "-")
        trs.append("<tr><td><div class=repo>%s</div><div class=commit>%s</div></td><td class=n>%d</td><td class=n>%d</td><td class=n>%d</td><td class=n>%d</td>"
                   "<td class=n>%d</td><td>%s</td><td>%s</td><td><span class='pill tip %s' data-tip='%s'><i>%s</i>%s</span><div class=commit>%s</div></td>"
                   "<td class=n>%s</td><td class=n>%s</td><td class=n>%ss</td></tr>" % (
                       esc(r["repo"]), esc(r["commit"]), r["sites_found"], r["migrated"], r["handoff"], r["kept"], r["flagged"],
                       meter(r["callsite_coverage"]), meter(r["weighted_coverage"]), v[0], esc(tip), icon, label, esc(v[1]),
                       "-" if r.get("sim_valid_pct") is None else "%.0f%%" % r["sim_valid_pct"], r.get("diff_lines"), r.get("runtime_s")))
    defs = "".join("<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(v)) for k, v in DEFS.items())
    return """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>router-migrate scoreboard</title><style>%s</style></head><body><main>
<h1>router-migrate scoreboard</h1><p class=sub>%d pinned open-source repos &middot; generated %s &middot; every number measured by eval/run_eval.py</p>
<div class=tiles>%s</div>
<div class=wrap><table><thead><tr><th>repo</th><th>sites</th><th>migrated</th><th>handoff</th><th>kept</th><th>flagged</th><th>call-site coverage</th>
<th>Router-eligible coverage</th><th>tests before &rarr; after</th><th>sim valid</th><th>diff lines</th><th>runtime</th></tr></thead><tbody>%s</tbody></table></div>
<details><summary>Definitions</summary><dl>%s</dl></details></main></body></html>""" % (
        CSS, len(rows), esc(gen), "".join("<div class=tile><div class=v>%s</div><div class=k>%s</div></div>" % (esc(a), esc(b)) for a, b in tiles),
        "".join(trs), defs)


def meter(x):
    if x is None:
        return "<span class=commit>-</span>"
    return "<div class=meter><div class=track><div class=fill style='width:%.1f%%'></div></div><span>%s</span></div>" % (100 * x, pct(x))
