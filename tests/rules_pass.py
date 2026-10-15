"""Test driver for the codemods `wrap` uses (router_migrate/apply_py.py, router_migrate/ts/tool.mjs): scan a fixture repo,
plan it with the rules, apply every patch the plan asks for, and write .router-migrate/{scan,plan,apply}.json, so the golden
before/after fixtures cover each codemod. This was the v1 `scan -> plan -> apply` pipeline (router_migrate/cli.py before
the v7 cleanup; see archive/README.md); the product no longer runs it, only these tests do."""
import json
import os
import tempfile

from router_migrate import apply_py, cli, scan_py
from router_migrate import plan as planmod
from router_migrate.catalog import Catalog
from router_migrate.runtime import render

STATE = ".router-migrate"


def _save(root, name, obj):
    os.makedirs(os.path.join(root, STATE), exist_ok=True)
    with open(os.path.join(root, STATE, name), "w") as f:
        json.dump(obj, f, indent=2, default=str)


def scan(root):
    res = scan_py.scan(root)
    ts = cli.run_ts(["scan", root]) if cli.node_ok() else {"files_scanned": 0, "sites": [], "errors": []}
    out = {"root": os.path.abspath(root), "sites": res["sites"] + ts.get("sites", []), "errors": res["errors"] + ts.get("errors", [])}
    _save(root, "scan.json", out)
    return out


def plan(root):
    p = planmod.build(scan(root), Catalog.load())
    _save(root, "plan.json", p)
    return p


def _reconcile(p, patched_ids, attempted, why):
    """A site whose client patch failed is not migrated: downgrade it, with the reason."""
    failed = attempted - patched_ids
    if not failed:
        return p
    for s in p["sites"]:
        cid = s["id"] if s["kind"] == "client" and s["id"] in failed else s.get("via_client")
        if s["outcome"] == "migrated" and cid in failed:
            s["outcome"], s["reason"] = "flagged", "client construction left unchanged: %s" % why.get(cid, "patch could not be applied safely")
    p["summary"] = planmod.summarize(p["sites"])
    return p


def migrate(root):
    """scan -> plan -> apply every patch. Returns the apply log ({edits, skipped, files_changed, runtime_files, ...})."""
    root = str(root)
    p = plan(root)
    cat = Catalog.load()
    py_rt, ts_rt = render("router_runtime.py", cat), render("routerRuntime.ts", cat)
    log = {"edits": [], "runtime_files": [], "files_changed": [], "skipped": []}
    by_file, installs = {}, {}
    for pt in p["patches"]:
        if pt["lang"] == "py":
            by_file.setdefault(pt["file"], {})[(pt["line"], pt["col"])] = (pt["patch"], pt.get("opts"))
    for ins in p["installs"]:
        installs.setdefault(ins["file"], set()).add(ins["kind"])
    for f in sorted(set(by_file) | set(installs)):
        try:
            changed, done, skipped, rt_rel = apply_py.patch_file(root, f, by_file.get(f, {}), installs.get(f, set()))
        except Exception as e:
            log["skipped"].append({"file": f, "reason": "libcst failed: %s" % e})
            continue
        for (line, col), kind in done.items():
            log["edits"].append({"lang": "py", "file": f, "line": line, "col": col, "action": kind, "ok": True})
        for (line, col), why in skipped.items():
            log["skipped"].append({"lang": "py", "file": f, "line": line, "col": col, "reason": why})
        if changed:
            log["files_changed"].append(f)
        if rt_rel:
            rtp = os.path.join(root, rt_rel)
            if not os.path.exists(rtp) or open(rtp).read() != py_rt:
                open(rtp, "w").write(py_rt)
            if rt_rel not in log["runtime_files"]:
                log["runtime_files"].append(rt_rel)
    ts_ids = [pt["site_id"] for pt in p["patches"] if pt["lang"] == "ts"]
    if ts_ids and cli.node_ok():
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as rf:
            rf.write(ts_rt)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
            json.dump(ts_ids, sf)
        try:
            r = cli.run_ts(["apply", root, "--runtime", rf.name, "--sites", sf.name])
            for e in r.get("edits", []):
                (log["edits"] if e.get("ok") else log["skipped"]).append(dict(e, lang="ts", reason=e.get("reason")))
            log["files_changed"] += [f for f in r.get("files_changed", []) if f not in log["files_changed"]]
            log["runtime_files"] += r.get("runtime_files", [])
        except Exception as e:
            log["skipped"].append({"lang": "ts", "file": "*", "reason": str(e)})
    patched = {e.get("site_id") for e in log["edits"] if e.get("site_id")} | {
        pt["site_id"] for pt in p["patches"] for e in log["edits"] if e["lang"] == "py" and e["file"] == pt["file"] and e["line"] == pt["line"]
        and e["col"] == pt["col"]}
    log["patched_site_ids"] = sorted(x for x in patched if x)
    why = {}
    for pt in p["patches"]:
        for sk in log["skipped"]:
            if sk.get("site_id") == pt["site_id"] or (sk.get("file") == pt["file"] and sk.get("line") == pt["line"] and sk.get("col") == pt["col"]):
                why[pt["site_id"]] = sk.get("reason")
    p = _reconcile(p, set(log["patched_site_ids"]), {pt["site_id"] for pt in p["patches"]}, why)
    _save(root, "apply.json", log)
    _save(root, "plan.json", p)
    return log
