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
