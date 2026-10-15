"""Checks the agent runs in parallel can't drop or corrupt each other's ledger records, and finish rejects a report
whose change points at a commit that isn't on the migration branch (undo would revert the wrong thing)."""
import json
import multiprocessing as mp

from router_migrate import agent as AG
from test_harness_v6 import git, migrate, repo, rm, state  # noqa: F401  (fixture and helpers)


def _append(root, n):
    for i in range(n):
        with AG.ledger_lock(root):
            led = AG.load_ledger(root)
            led.setdefault("live_probes", []).append({"i": i})
            AG.save_ledger(root, led)


def test_parallel_writers_keep_every_record(tmp_path):
    root = str(tmp_path)
    AG.save_ledger(root, {"live_probes": []})
    ps = [mp.Process(target=_append, args=(root, 25)) for _ in range(8)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    assert len(AG.load_ledger(root)["live_probes"]) == 200  # nothing lost, and the file still parses


def test_finish_rejects_a_change_whose_commit_is_not_on_the_branch(repo):
    assert rm("start", ".", "--test-cmd", "true", cwd=repo).returncode == 0
    migrate(repo)
    doc = state(repo, "MIGRATION.json")
    doc["items"][0]["commit"] = "0123456789abcdef0123456789abcdef01234567"
    (repo / ".router-migrate" / "MIGRATION.json").write_text(json.dumps(doc))
    r = rm("finish", ".", "--no-dashboard", cwd=repo)
    assert r.returncode != 0 and "is not a commit on router-migrate/migration" in json.dumps(state(repo, "validation.json"))
