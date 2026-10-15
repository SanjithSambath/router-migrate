"""Gate tests: fake run dirs in tmp_path, a fake pair(); no network, no agents."""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402

REPOS = ["r1", "r2", "r3", "r4", "r5"]


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(gate, "OUT", str(tmp_path / "out"))
    return tmp_path


def mkrun(arm, repo, ok=True, verified=3, invalid=False, rnd="t"):
    d = os.path.join(gate.RUNS, rnd, arm, repo)
    os.makedirs(d, exist_ok=True)
    json.dump({"arm": arm, "repo": repo, "invalid": invalid}, open(os.path.join(d, "run.json"), "w"))
    json.dump({"migration": {"ok": ok, "tests": {"status": "passed" if ok else "failed"},
                             "routes": {"verified": ["x"] * verified, "unverified": [], "not_routing": []}}},
              open(os.path.join(d, "validation.json"), "w"))


def fake_pair(results, calls=None):
    """results: {(repo, rubric): 'win'|'loss'|'tie'|None}; default win."""
    def pair(rnd, arm, ref, repo, rubric="base"):
        if calls is not None:
            calls.append((repo, rubric))
        r = results.get((repo, rubric), "win")
        return {"error": "no reply"} if r is None else {"result": r, "margin": "clear", "why": "because | reasons\nhere"}
    return pair


def gate_run(monkeypatch, results=None, calls=None, argv=()):
    monkeypatch.setattr(gate.pairwise, "pair", fake_pair(results or {}, calls))
    return gate.main(["--candidate", "c", "--baseline", "b", "--round", "t", "--repos", ",".join(REPOS), "--judge-only"] + list(argv))


def both(n=5, **kw):
    for repo in REPOS[:n]:
        mkrun("c", repo, **kw)
        mkrun("b", repo)


def test_ship(g, monkeypatch):
    both()
    d = gate_run(monkeypatch)
    assert d["verdict"] == "SHIP" and d["reasons"] == [] and d["tally"] == {"base": (5, 0, 0), "open": (5, 0, 0)}


def test_ties_count_as_neither(g, monkeypatch):
    both()
    res = {("r1", "base"): "tie", ("r2", "base"): "tie", ("r3", "base"): "tie", ("r4", "base"): "loss",
           ("r1", "open"): "loss", ("r2", "open"): "loss", ("r3", "open"): "tie"}  # base 1-3-1, open 2-1-2
    assert gate_run(monkeypatch, res)["verdict"] == "SHIP"


@pytest.mark.parametrize("rubric", ["base", "open"])
def test_hold_on_losses_either_rubric(g, monkeypatch, rubric):
    both()
    d = gate_run(monkeypatch, {("r1", rubric): "loss", ("r2", rubric): "loss", ("r3", rubric): "loss"})
    assert d["verdict"] == "HOLD" and d["reasons"] == ["%s rubric: 2 wins < 3 losses" % rubric]


def test_hold_on_outside_check(g, monkeypatch):
    both()
    mkrun("c", "r2", ok=False)
    d = gate_run(monkeypatch)
    assert d["verdict"] == "HOLD" and d["reasons"] == ["r2: candidate fails the outside check where the baseline passes"]


def test_failing_check_on_both_sides_is_not_a_reason(g, monkeypatch):
    both()
    mkrun("c", "r2", ok=False)
    mkrun("b", "r2", ok=False)
    assert gate_run(monkeypatch)["verdict"] == "SHIP"


def test_hold_on_fewer_verified_routes(g, monkeypatch):
    both()
    mkrun("c", "r4", verified=2)
    d = gate_run(monkeypatch)
    assert d["verdict"] == "HOLD" and d["reasons"] == ["r4: candidate verified routes 2 < baseline 3"]


def test_hold_lists_every_reason(g, monkeypatch):
    both()
    mkrun("c", "r1", ok=False, verified=1)
    d = gate_run(monkeypatch, {("r2", "open"): "loss", ("r3", "open"): "loss", ("r4", "open"): "loss"})
    assert d["verdict"] == "HOLD" and len(d["reasons"]) == 3


def test_invalid_runs_excluded_and_never_judged(g, monkeypatch):
    both()
    mkrun("c", "r1", invalid=True)
    mkrun("b", "r2", invalid=True)
    os.remove(os.path.join(gate.RUNS, "t", "b", "r3", "run.json"))  # missing run
    calls = []
    d = gate_run(monkeypatch, {("r1", "base"): "loss", ("r2", "base"): "loss"}, calls)
    assert {r for r, _ in calls} == {"r4", "r5"}
    assert [e[0] for e in d["excluded"]] == ["r1", "r2", "r3"]
    assert "candidate run invalid" in d["excluded"][0][1] and "baseline run missing" in d["excluded"][2][1]
    assert d["verdict"] == "INSUFFICIENT" and d["reasons"][0] == "only 2 valid judged pair(s); need 3"


def test_three_valid_pairs_is_enough(g, monkeypatch):
    both()
    mkrun("c", "r1", invalid=True)
    mkrun("c", "r2", invalid=True)
    d = gate_run(monkeypatch)
    assert d["verdict"] == "SHIP" and d["pairs"] == 3


def test_failed_judgment_is_missing_not_scored(g, monkeypatch):
    both(3)
    d = gate_run(monkeypatch, {("r1", "open"): None})
    assert d["missing"] == [("r1", "open")] and d["verdict"] == "INSUFFICIENT"


def test_report_reads_cache_only(g, monkeypatch):
    both()
    os.makedirs(os.path.join(gate.RUNS, "t", "pairs"))
    for repo in REPOS[:4]:
        for rub in gate.RUBRICS:
            json.dump({"result": "win", "margin": "slight", "why": "w"}, open(gate.pair_path("t", "c", "b", repo, rub), "w"))
    json.dump({"error": "timeout"}, open(gate.pair_path("t", "c", "b", "r5", "base"), "w"))  # an errored cache entry
    def boom(*a, **k):
        raise AssertionError("--report must never call the judge")
    monkeypatch.setattr(gate.pairwise, "pair", boom)
    d = gate.main(["--candidate", "c", "--baseline", "b", "--round", "t", "--repos", ",".join(REPOS), "--report"])
    assert d["verdict"] == "SHIP" and d["pairs"] == 4 and d["missing"] == [("r5", "base"), ("r5", "open")]


def test_gate_md(g, monkeypatch):
    both()
    mkrun("c", "r5", invalid=True)
    mkrun("c", "r4", verified=1)
    gate_run(monkeypatch, {("r1", "open"): "tie"})
    md = open(os.path.join(gate.OUT, "t", "GATE.md")).read()
    assert "**HOLD: c vs b on t (W-T-L base 4-0-0, open 3-1-0; 4 valid pairs)**" in md
    assert "- r4: candidate verified routes 1 < baseline 3" in md
    assert "## Head-to-head, base rubric (W-T-L 4-0-0)" in md and "## Head-to-head, open rubric (W-T-L 3-1-0)" in md
    assert "| r1 | tie | clear | because / reasons here |" in md
    assert "| r4 | yes / yes | 1 / 3 | 0 / 0 | 0 / 0 | passed / passed |" in md
    assert "## Excluded\n\n- r5: candidate run invalid (outage or quota)" in md


def test_run_mode_runs_only_what_is_missing_or_invalid(g, monkeypatch):
    both(4)
    mkrun("b", "r2", invalid=True)
    ran = []
    def one(rnd, arm, repo, model):
        ran.append((arm, repo))
        mkrun(arm, repo, rnd=rnd)
        return arm, repo, 0, 1
    fake = types.SimpleNamespace(load_arms=lambda: {"c": {}, "b": {}}, one=one, _stop=lambda *a: None)
    monkeypatch.setitem(sys.modules, "run_arms", fake)
    monkeypatch.setattr(gate.signal, "signal", lambda *a: None)
    monkeypatch.delenv("RM_PREP_TAG", raising=False)
    monkeypatch.setattr(gate.pairwise, "pair", fake_pair({}))
    d = gate.main(["--candidate", "c", "--baseline", "b", "--round", "t", "--repos", ",".join(REPOS)])
    assert sorted(ran) == [("b", "r2"), ("b", "r5"), ("c", "r5")]
    assert os.environ["RM_PREP_TAG"] == "_t" and d["verdict"] == "SHIP" and d["pairs"] == 5


def test_unknown_arm_refused(g, monkeypatch):
    monkeypatch.setitem(sys.modules, "run_arms", types.SimpleNamespace(load_arms=lambda: {"b": {}}))
    with pytest.raises(SystemExit):
        gate.main(["--candidate", "nope", "--baseline", "b", "--round", "t", "--repos", "r1"])


def test_repo_sets():
    assert gate.repo_list("working") == gate.WORKING and gate.repo_list("heldout") == gate.HELDOUT
    assert len(gate.repo_list("all")) == 10 and gate.repo_list("a,b") == ["a", "b"]
