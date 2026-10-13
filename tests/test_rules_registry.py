"""Rule coverage: every rule in router_migrate/rules.py must name tests that exist (by function name, parametrize id,
node-harness scenario name, or golden fixture case)."""
import os

from router_migrate.rules import RULES

HERE = os.path.dirname(os.path.abspath(__file__))


def corpus():
    text = ""
    for d, _, files in os.walk(HERE):
        if "fixtures" in d.split(os.sep) and "before" in d:
            continue
        for f in files:
            if f.endswith((".py", ".mjs")):
                text += open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
    cases = os.listdir(os.path.join(HERE, "fixtures", "py"))
    return text, set(cases)


def covered(ref, text, cases):
    return ref in cases or ("def %s(" % ref) in text or ('"%s"' % ref) in text or ("t(\"%s\"" % ref) in text or ref + ".py" in os.listdir(HERE)


def test_every_rule_has_existing_tests():
    text, cases = corpus()
    missing = {rid: [r for r in refs if not covered(r, text, cases)] for rid, _, _, refs in RULES}
    missing = {k: v for k, v in missing.items() if v}
    total = len(RULES)
    ok = sum(1 for rid, _, _, refs in RULES if any(covered(r, text, cases) for r in refs))
    print("\nrule coverage: %d/%d (%.0f%%)" % (ok, total, 100.0 * ok / total))
    assert ok / total >= 0.9, missing
    assert not missing, missing
