"""v6 contracts: report validation names the exact field, item status covers every kind, requests and apply results."""
import json
import os
import shutil

import pytest

from router_migrate import report_v6 as R

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "v6_state")


def fixture(name):
    return json.load(open(os.path.join(FIX, name)))


def test_fixture_report_is_valid_and_covers_every_kind_and_status():
    doc = fixture("MIGRATION.json")
    assert R.validate_report(doc) == []
    assert {i["kind"] for i in doc["items"]} == set(R.KINDS)
    st = {i["id"]: R.item_status(i, fixture("requests.json"), fixture("apply_log.json"), fixture("validation.json"))
          for i in doc["items"]}
    assert set(st.values()) == {"in code", "undo requested", "info", "postponed", "proposed", "approved", "applied",
                                "check failed", "declined", "undone", "dismissed"}


@pytest.mark.parametrize("mutate,field", [
    (lambda d: d.update(version=5), "version"),
    (lambda d: d.update(reviewed=""), "reviewed"),
    (lambda d: d["items"][0].update(kind="migrated"), "items[m1].kind"),
    (lambda d: d["items"][1].update(loses="nothing"), "items[t1].loses"),
    (lambda d: d["items"][5].update(branch="feature/x"), "items[i1].branch"),
    (lambda d: d["items"][0].update(evidence=[{"check": "vibes", "result": "pass"}]), "items[m1].evidence[0]"),
    (lambda d: d["items"][5].update(depends_on=["nope"]), "items[i1].depends_on"),
    (lambda d: d["calls"][0].update(item="nope"), "calls[0].item"),
    (lambda d: d["items"].append(dict(d["items"][0])), "is used twice"),
])
def test_validation_names_the_field(mutate, field):
    doc = fixture("MIGRATION.json")
    mutate(doc)
    errs = R.validate_report(doc)
    assert any(field in e for e in errs), errs


def test_requests_respect_what_each_kind_allows(tmp_path):
    st = tmp_path / ".router-migrate"
    shutil.copytree(FIX, st)
    (st / "requests.json").write_text('{"requests": {}}')
    (st / "apply_log.json").write_text('{"sessions": []}')
    root = str(tmp_path)
    assert R.record_request(root, "i1", "apply")["action"] == "apply"
    assert R.record_request(root, "m1", "undo")["action"] == "undo"
    with pytest.raises(ValueError):
        R.record_request(root, "m1", "apply")  # only improvements are applied
    with pytest.raises(ValueError):
        R.record_request(root, "i2", "undo")  # not in the code yet
    with pytest.raises(ValueError):
        R.record_request(root, "d1", "postpone")  # needs a date
    with pytest.raises(ValueError):
        R.record_request(root, "zz", "apply")
    assert set(R.pending_requests(root)) == {"i1", "m1"}


def test_apply_results_must_cover_every_request():
    ok = {"summary": "x", "results": [{"item": "i1", "action": "apply", "status": "done", "commits": ["a"]},
                                      {"item": "m1", "action": "undo", "status": "declined", "note": "later work depends on it"}]}
    assert R.validate_apply(ok, ["i1", "m1"]) == []
    assert any("no result" in e for e in R.validate_apply(ok, ["i1", "m1", "p1"]))
    bad = {"summary": "x", "results": [{"item": "i1", "action": "apply", "status": "failed"}]}
    assert any("note" in e for e in R.validate_apply(bad, ["i1"]))


def test_apply_blocked_reasons():
    v = fixture("validation.json")
    assert R.apply_blocked(v) is None
    assert "still running" in R.apply_blocked({"migration": {"ok": True, "tests": {"status": "running"}}})
    assert "check failed" in R.apply_blocked({"migration": {"ok": False, "failures": [{"gate": "tests"}]}})
    assert "finish" in R.apply_blocked({})


def test_migration_key_reads_env_then_file_and_is_never_in_state(tmp_path, monkeypatch):
    monkeypatch.delenv("ROUTER_MIGRATE_KEY", raising=False)
    f = tmp_path / "key"
    f.write_text("sk-test-123\n")
    monkeypatch.setenv("ROUTER_MIGRATE_KEY_FILE", str(f))
    assert R.migration_key() == "sk-test-123"
    monkeypatch.setenv("ROUTER_MIGRATE_KEY", "sk-env")
    assert R.migration_key() == "sk-env"
    monkeypatch.delenv("ROUTER_MIGRATE_KEY")
    monkeypatch.setenv("ROUTER_MIGRATE_KEY_FILE", str(tmp_path / "missing"))
    assert R.migration_key() is None
