from router_migrate.verify import parse_counts


def test_pytest_single():
    assert parse_counts("....\n===== 12 passed, 1 skipped in 3.2s =====\n")["passed"] == 12


def test_pytest_two_runs_summed():
    out = "== 3 passed in 1.0s ==\nother\n== 2 passed, 1 failed in 0.5s ==\n"
    c = parse_counts(out)
    assert (c["passed"], c["failed"]) == (5, 1)


def test_jest_two_runs_summed():
    out = "Tests:       204 passed, 204 total\n...\nTests:       39 passed, 39 total\n"
    assert parse_counts(out)["passed"] == 243


def test_jest_failures_and_skips():
    c = parse_counts("Test Suites: 2 failed, 1 passed\nTests:       1 failed, 2 passed, 3 total\n")
    assert (c["passed"], c["failed"]) == (2, 1)


def test_node_test():
    c = parse_counts("# tests 4\n# pass 4\n# fail 0\n")
    assert (c["passed"], c["failed"]) == (4, 0)


def test_manten():
    assert parse_counts("✔ a\n79 tests passed\n")["passed"] == 79


def test_unknown_output():
    assert parse_counts("hello")["passed"] is None


def test_node24_spec_reporter():
    c = parse_counts("\u2139 tests 4\n\u2139 pass 4\n\u2139 fail 0\n")
    assert (c["passed"], c["failed"]) == (4, 0)


def test_manten_bare():
    c = parse_counts("\u2714 x (3ms)\n\n4s\n79 passed\n\n")
    assert c["passed"] == 79


def test_pytest_quiet_no_banner():
    c = parse_counts("..\n2 passed, 1 skipped in 0.12s\n")
    assert (c["passed"], c["skipped"]) == (2, 1)
    c = parse_counts("E ...\n1 error in 0.38s\n")
    assert c["errors"] == 1 and c["passed"] == 0


# ---- H5: replay counts unique request shapes and replays each site's static kwargs
def _msite(i, surface="messages", model="claude-haiku-4-5", **kw):
    return {"id": "py:a.py:%d:0:call" % i, "file": "a.py", "line": i, "kind": "call", "sdk": "anthropic" if surface.startswith("messages") else "openai",
            "surface": surface, "outcome": "migrated", "prod": True, "stream": False, "model": {"value": model, "static": True}, **kw}


def test_replay_counts_unique_shapes_and_replays_static_kwargs():
    from router_migrate.catalog import Catalog
    from router_migrate.sim.server import serve
    from router_migrate.verify import replay, SIM_KEY
    plan = {"sites": [
        _msite(1), _msite(2),  # identical shape: one request
        _msite(3, static_kwargs={"output_config": {"effort": "high"}, "max_tokens": 64}),  # not a valid Router request for Claude
        _msite(4, model="claude-3-opus-20240229"),  # literal does not resolve: replayed with the default model -> same shape as 1
    ]}
    srv, sim, base = serve()
    try:
        rep = replay(plan, base, SIM_KEY, Catalog.load(), tag="unit-unique")
    finally:
        srv.shutdown()
    by = {r["site_id"].split(":")[2]: r for r in rep["results"]}
    assert rep["unique_requests"] == 2 and rep["ok"] == 1, rep
    assert by["2"].get("reused") and by["4"].get("reused")
    # the site's real shape is not served: router-sim rejects it, or the runtime itself keeps it on the provider (599 here)
    assert by["3"]["ok"] is False
    assert "output_config" in by["3"]["kwargs"]


def test_replay_kwargs_skip_stateful_and_transport_options():
    from router_migrate.verify import replay_kwargs
    s = {"static_kwargs": {"temperature": 0.2, "timeout": 5, "previous_response_id": "x", "tools": [{"type": "function", "name": "f"}],
                           "extra_headers": {"x-a": "1"}}}
    assert replay_kwargs(s) == {"temperature": 0.2, "tools": [{"type": "function", "name": "f"}], "extra_headers": {"x-a": "1"}}


def test_scoreboard_router_on_na_and_legacy_rows(tmp_path):
    """H5: 0 sim requests from the Router-on test run renders as N/A; rows from old result files (no replay_unique_requests) are
    marked legacy and left out of the mean; new rows show ok/unique."""
    import importlib.util
    import json
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("scoreboard_ut", os.path.join(here, "eval", "scoreboard.py"))
    sb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sb)
    base = {"commit": "abc", "sites_found": 2, "migrated": 1, "handoff": 0, "kept": 1, "flagged": 0, "router_eligible": 1, "callsite_coverage": 0.5,
            "weighted_coverage": 1.0, "tests_before": {"passed": 3}, "tests_after": {"passed": 3}, "diff_lines": 4, "runtime_s": 1}
    old = dict(base, repo="old/repo", sim_valid_pct=100.0, tests_router_on={"passed": 3}, sim_detail={"repo_test_requests": 0})
    new = dict(base, repo="new/repo", sim_valid_pct=50.0, replay_unique_requests=2, replay_ok=1, sim_requests_from_tests=0,
               tests_router_on={"passed": 3})
    live = dict(new, repo="live/repo", sim_requests_from_tests=4)
    sb.write_all([old, new, live], str(tmp_path))
    md = open(tmp_path / "SCOREBOARD.md").read()
    row = {ln.split("|")[1].strip(): ln for ln in md.splitlines() if ln.startswith("| ") and "/repo" in ln}
    assert sb.MOCKED in row["old/repo"] and "(legacy def.)" in row["old/repo"]
    assert sb.MOCKED in row["new/repo"] and "50% (1/2)" in row["new/repo"]
    assert sb.MOCKED not in row["live/repo"] and "3 passed" in row["live/repo"]
    d = json.load(open(tmp_path / "scoreboard.json"))
    assert d["totals"]["sim_valid_pct_mean"] == 50.0 and all("sim_valid_pct" in r for r in d["repos"])
    assert "replay_ok / replay_unique_requests" in d["definitions"]["sim_valid_pct"]
