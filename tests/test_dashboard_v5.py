"""The v5 dashboard contract: every call's status from route truth (ledger result + plan_v3 `verified`), the overview headline
counted from those same calls, the agent's needs-decision calls listed from its report (not as cards), card provenance
(agent proposal vs rule default), and the full test suite gating Apply in the bundle and in the server."""
import http.client
import json
import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from router_migrate import improve  # noqa: E402
from router_migrate.dashboard import render_report, serve  # noqa: E402
from router_migrate.dashboard import view  # noqa: E402


def site(sid, f, line, fn, outcome, **kw):
    return dict({"id": "py:%s:%d:1:call" % (f, line), "file": f, "line": line, "function": fn, "kind": "call", "prod": True, "lang": "py",
                 "sdk": "anthropic", "surface": "messages.create", "outcome": outcome, "reason": kw.pop("reason", ""), "stream": False}, **kw)


SITES = [
    site("a", "app/chat.py", 10, "answer", "migrated", verified=True),
    site("b", "app/jobs.py", 20, "digest", "migrated", verified=True),
    site("c", "app/agent.py", 30, "plan", "migrated", verified=False, reason="client patched; model chosen at runtime from config"),
    site("d", "app/user.py", 40, "reply", "kept", reason="stays on its provider: base_url comes from user settings"),
    site("e", "app/tag.py", 50, "tagged", "flagged", reason="agent: needs_decision tags the end user's id"),
    site("f", "app/local.py", 60, "local", "kept", reason="ollama SDK / endpoint against localhost:11434"),
]


def claim(f, line, outcome, **kw):
    return dict({"file": f, "line": line, "outcome": outcome, "origin": "report", "by": "agent", "evidence": [], "reason_code": None,
                 "why": "", "levers": None, "loses": None}, **kw)


def ledger(result=None, **extra):
    led = {"version": 5, "floor": {"tree": "0" * 40, "migrated": 0, "applied": False}, "baseline": {}, "finish": {"attempts": 0, "history": []},
           "claims": {
               "app/user.py:40": claim("app/user.py", 40, "migrated", why="Wrapped the client; requests now go through Router."),
               "app/tag.py:50": claim("app/tag.py", 50, "flagged", reason_code="needs_decision", why="It sends the end user's id to Anthropic.",
                                      levers=["drop_params:metadata.user_id"], loses="per-user attribution", latency="interactive",
                                      task={"value": "chat", "why": "streams"}),
               "app/extra.py:7": claim("app/extra.py", 7, "flagged", reason_code="needs_decision", why="A second product call."),
               "app/local.py:60": claim("app/local.py", 60, "kept", reason_code="cannot_route", why="Local model."),
           },
           "tradeoffs": [{"id": "T1", "site": "app/jobs.py:20", "file": "app/jobs.py", "line": 20, "feature": "app/jobs.py:digest",
                          "levers": {"drop_params": ["seed"]}, "loses": "replies are no longer reproducible", "why": "Router rejects seed",
                          "evidence": [], "undone": False}],
           "result": result if result is not None else {
               "moved_verified": ["app/chat.py:10", "app/jobs.py:20"], "moved_unverified": ["app/agent.py:30"],
               "claimed_not_routing": ["app/user.py:40"],
               "stays": [{"site": "app/user.py:40", "reason": "base_url comes from user settings"},
                         {"site": "app/local.py:60", "reason": "ollama SDK against localhost:11434"}],
               "tradeoffs": 1, "tests": {"baseline": {"passed": 5, "failed": 0}, "full": {"status": "passed", "counts": {"passed": 5, "failed": 0}}}}}
    led.update(extra)
    return led


def card(cid, rule, **kw):
    return dict({"id": cid, "rule": rule, "lever_group": "same_model", "title": cid, "scope": {"sites": [], "features": ["app/chat.py:answer"],
                 "latency_class": None}, "model_owner": None, "change": {"kind": "policy", "diff": json.dumps({"features": {"app/chat.py:answer": {}}})},
                 "loses": [], "rule_inputs": {}, "evidence": {"level": "L0", "results": []}, "impact": {"per_call_pct": None, "monthly_usd": None},
                 "risk": 1, "effort": "none", "options": [], "etag": cid, "state": "new", "applies": True}, **kw)


def state(tmp_path, led, cards=(), considered=()):
    st = tmp_path / ".router-migrate"
    st.mkdir(parents=True, exist_ok=True)
    (st / "ledger.json").write_text(json.dumps(led))
    (st / "plan_v3.json").write_text(json.dumps({"sites": SITES, "summary": {}}))
    sig = {s["id"]: {"feature": "%s:%s" % (s["file"], s["function"]), "resolved_model": "claude-sonnet-4-6"} for s in SITES}
    rec = {"cards": list(cards) + improve.tradeoff_cards(led) + improve.agent_cards(led), "considered_not_recommended": list(considered), "signals": sig}
    (st / "recommendations.json").write_text(json.dumps(rec))
    return str(tmp_path)


def test_every_call_gets_one_status_from_route_truth_and_the_headline_counts_them(tmp_path):
    b = improve.bundle(state(tmp_path, ledger()))
    assert b.get("view_error") is None
    calls = {c["fn"]: c for c in b["view"]["calls"]}
    assert calls["answer"]["status"] == "moved" and calls["answer"]["verified"] and calls["answer"]["by"] == "agent"
    assert calls["digest"]["status"] == "tradeoff" and calls["digest"]["tradeoffs"][0]["card"] == "tradeoff:T1"
    assert calls["digest"]["tradeoffs"][0]["levers"] == "drops seed" and calls["digest"]["tradeoffs"][0]["loses"].startswith("replies")
    assert calls["plan"]["status"] == "unverified" and calls["plan"]["verified"] is False and "run time" in calls["plan"]["why"]
    # the agent said moved; the check sees it on its provider: said honestly, with the check's reason
    assert calls["reply"]["status"] == "not_routing" and not calls["reply"]["routes"]
    assert calls["reply"]["why"] == "The agent reported this as moved, but it doesn't route: base_url comes from user settings."
    # the agent's needs-decision call: its reasoning, the levers moving would take, and its latency/task reading
    tag = calls["tagged"]
    assert tag["status"] == "decide" and tag["why"] == "It sends the end user's id to Anthropic."
    assert tag["agent"]["levers"] == "drops metadata.user_id" and tag["agent"]["loses"] == "per-user attribution"
    assert (tag["agent"]["latency"], tag["agent"]["task"]) == ("interactive", "chat")
    assert calls["local"]["status"] == "left" and calls["local"]["stay_label"] == "local model"
    # a call only the agent's report names still shows up, so nothing the headline counts is hidden
    extra = [c for c in b["view"]["calls"] if c.get("synthetic")]
    assert [(c["file"], c["status"]) for c in extra] == [("app/extra.py", "decide")]
    m = b["moved"]
    assert (m["total"], m["moved"], m["verified"], m["unverified"], m["with_tradeoffs"], m["needs_decision"], m["not_routing"], m["stay"]) == \
        (7, 3, 2, 1, 1, 2, 1, 1)
    assert m["stay_reasons"] == [{"reason": "local model", "n": 1}]
    assert sum(1 for c in b["view"]["calls"] if c["routes"]) == m["moved"]
    # needs-decision is the ledger's, not a card: nothing to approve or apply for it
    assert not [c for c in b["recommendations"]["cards"] if c["rule"].startswith("decision.")]
    html = render_report(b)
    assert "Calls for your judgment" in html and "Reported moved, doesn" in html


def test_older_v5_result_names_still_render(tmp_path):
    old = {"moved": 2, "moved_sites": ["app/chat.py:10", "app/jobs.py:20"], "claimed_unverified": ["app/agent.py:30"], "tradeoffs": 1}
    sites = [dict(s) for s in SITES]
    for s in sites:
        s.pop("verified", None)  # an older plan_v3 without the per-site flag
    root = state(tmp_path, ledger(result=old))
    (tmp_path / ".router-migrate" / "plan_v3.json").write_text(json.dumps({"sites": sites, "summary": {}}))
    b = improve.bundle(root)
    calls = {c["fn"]: c for c in b["view"]["calls"]}
    assert calls["plan"]["status"] == "unverified" and calls["answer"]["status"] == "moved"
    assert b["tests_status"] is None and b["apply_blocked"] is None


def test_full_suite_status_gates_apply_in_bundle_and_server(tmp_path):
    led = ledger()
    led["result"]["tests"]["full"] = {"status": "running", "counts": {"passed": 2}}
    b = improve.bundle(state(tmp_path, led))
    assert b["tests_status"] == "running" and "still running" in b["apply_blocked"]
    led["result"]["tests"]["full"] = {"status": "failed", "counts": {"passed": 3, "failed": 2},
                                      "failures": [{"gate": "tests", "files": None, "detail": "test_x failed"}]}
    b = improve.bundle(state(tmp_path, led))
    assert b["tests_status"] == "failed" and "(2 failing)" in b["apply_blocked"]
    assert b["tests"]["full"]["failures"][0]["detail"] == "test_x failed"
    for st, blocked in (("passed", None), ("skipped", None)):
        assert improve.apply_blocked(improve.tests_state({"result": {"tests": {"full": {"status": st}}}})) is blocked

    current = {"b": b}
    applied = []
    server, url = serve(lambda: current["b"], lambda p: p, lambda: applied.append(1) or {"command": "x"}, open_browser=False)
    try:
        port = urlsplit(url).port

        def apply():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/apply", body=b"{}", headers={"Host": "127.0.0.1:%d" % port, "X-RM-Token": server.token,
                                                                     "Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, json.loads(r.read())

        st, body = apply()
        assert st == 409 and "full test suite failed" in body["error"] and applied == []
        current["b"] = dict(b, apply_blocked=None)
        assert apply()[0] == 200 and applied == [1]
    finally:
        server.shutdown()


def test_cards_carry_provenance_and_agent_levers_read_like_their_rules(tmp_path):
    cards = [
        card("rec_flex", "agent.flex", origin="agent", rationale="nobody waits on it", gains="half price", validated=True,
             change={"kind": "policy", "diff": json.dumps({"features": {"app/chat.py:answer": {"allow_flex_tier": True}}})}),
        card("rec_tag", "attr.metadata", origin="agent+rule", rationale="spend per feature", gains="spend per feature", validated=True,
             change={"kind": "policy", "diff": json.dumps({"defaults": {"metadata": {"product": "app"}}})}, rule_inputs={"product": "app"}),
        card("rec_rule", "cache.key", origin="rule", validated=True),
        card("rec_port", "agent.vendor_port", lever_group="decision", applies=False, origin="agent", rationale="port it",
             change={"kind": "note", "diff": "{}"}),
        card("rec_bad", "agent.cache_key", origin="agent", rationale="same prompt", validated="the prompt changes per call"),
    ]
    considered = [{"rule": "agent.fallbacks", "scope": {"features": ["app/chat.py:answer"], "sites": []}, "origin": "agent",
                   "reason": "proposed by the migration agent; not valid: fallbacks must be active Router catalog ids (x)"}]
    b = improve.bundle(state(tmp_path, ledger(), cards, considered))
    imps = {i["id"]: i for i in b["view"]["improvements"]}
    flex = imps["rec_flex"]
    assert (flex["origin"], flex["rationale"], flex["gain"], flex["lever"]) == ("agent", "nobody waits on it", "half price", "flex")
    assert flex["impact"] == {"label": "Half price per call", "tone": "save"} and {"op": "add", "key": "allow_flex_tier", "v": "true"} in flex["request"]
    assert imps["rec_tag"]["origin"] == "agent+rule" and imps["rec_tag"]["rationale"] == "spend per feature"
    assert imps["rec_rule"]["origin"] == "rule" and imps["rec_rule"]["rationale"] == "" and imps["rec_rule"]["validated"]
    assert imps["rec_port"]["kind"] == "decision"
    assert imps["rec_bad"]["rejected"] == "the prompt changes per call" and not imps["rec_bad"]["validated"]
    (c,) = [x for x in b["view"]["considered"] if x["origin"] == "agent"]
    assert c["rule"] == "agent.fallbacks" and c["lever"] == "fallback" and c["fn"] == "answer"
    tr = imps["tradeoff:T1"]
    assert tr["kind"] == "tradeoff" and tr["origin"] == "rule"  # a trade-off's provenance is shown as the agent's move by the page


def test_levers_and_stay_labels_in_words():
    assert view.levers_text(["wrap:a.py:1", "drop_betas:x", "model_map:a=b"]) == "drops beta x; a → b"
    assert view.levers_text({"client": "a.py:1", "drop_params": ["seed"], "unlock_hosts": ["api.x.ai"], "operator_key_only": True}) == \
        "drops seed; api.x.ai served through Router; operator's key only"
    assert view.stay_label("agent: R_PER_TENANT each user's key") == "users' own keys"
    assert view.stay_label("embeddings: Router has no embeddings endpoint") == "no Router endpoint"
    assert view.stay_label("") == "no reason given"
