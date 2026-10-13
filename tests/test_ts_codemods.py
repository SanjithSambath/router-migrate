"""Golden before/after fixtures for the TS scanner + planner + codemods (tool.mjs), plus idempotency and a load check of every
vendored runtime copy in its module format (.ts via tsc elsewhere; .mjs import, CommonJS require). Regenerate with
`.venv/bin/python tests/ts_regen_goldens.py` and review the diff."""
import json
import os
import shutil
import subprocess

import pytest

from ts_regen_goldens import FIX, RUNTIMES, migrate, outcomes, tree

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
CASES = sorted(c for c in os.listdir(FIX) if os.path.isdir(os.path.join(FIX, c, "after")))


def read_tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = open(p).read()
    return out


@pytest.mark.parametrize("case", CASES)
def test_ts_golden(tmp_path, case):
    work = migrate(case, str(tmp_path))
    assert tree(work) == read_tree(os.path.join(FIX, case, "after"))
    assert outcomes(work) == json.load(open(os.path.join(FIX, case, "expect.json")))["outcomes"]


@pytest.mark.parametrize("case", CASES)
def test_ts_idempotent(tmp_path, case):
    work = migrate(case, str(tmp_path))
    first, first_out = tree(work), outcomes(work)
    from router_migrate import cli
    cli.do_scan(work, quiet=True)
    cli.do_plan(work, quiet=True)
    cli.do_apply(work, quiet=True)
    assert tree(work) == first
    # a rescan of patched code sees the inserted import, so line numbers shift by one; the outcomes must not change
    assert sorted(outcomes(work).values()) == sorted(first_out.values())


@pytest.mark.parametrize("case", [c for c in CASES if c in ("mjs_no_tsconfig", "openai_require_cjs", "node16_js_suffix")])
def test_vendored_js_runtime_loads_in_its_module_format(tmp_path, case):
    """H4: a .mjs/.js file must import a runtime Node can actually load, and the runtime is inert without RAMP_ROUTER_API_KEY."""
    work = migrate(case, str(tmp_path))
    found = [os.path.join(d, f) for d, _, fs in os.walk(work) for f in fs if f in RUNTIMES and not f.endswith(".ts") and "node_modules" not in d]
    if case == "node16_js_suffix":
        assert not found  # TS source importing './lib/routerRuntime.js' gets a .ts runtime that tsc emits as .js
        return
    assert found
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    for p in found:
        is_esm = p.endswith(".mjs") or json.load(open(os.path.join(work, "package.json"))).get("type") == "module"
        code = ("import(%r).then(m => { const o = m.routerOptions(); console.log(JSON.stringify(Object.keys(o))); })" % p if is_esm else
                "const m = require(%r); console.log(JSON.stringify(Object.keys(m.routerOptions())));" % p)
        r = subprocess.run(["node", "-e", code], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout.strip()) == []  # inert: no options when the key is unset
