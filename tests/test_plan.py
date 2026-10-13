"""Planner rules on synthetic scans (no files needed)."""
from router_migrate.catalog import Catalog
from router_migrate import plan

CAT = Catalog.load()


def site(i, kind, sdk, surface, **kw):
    d = {"id": "py:f.py:%d:0:%s" % (i, kind), "lang": "py", "file": "f.py", "line": i, "col": 0, "kind": kind, "sdk": sdk, "surface": surface,
         "stream": False, "async": False, "model": None, "base_url": None, "receiver": "client", "prod": True, "snippet": ""}
    d.update(kw)
    return d


def outcomes(sites):
    p = plan.build({"sites": sites}, CAT)
    return {s["line"]: (s["outcome"], s["reason"]) for s in p["sites"]}, p


def test_native_router_client_counts_as_migrated():
    c = site(1, "client", "openai", "OpenAI", base_url={"value": "https://api.router.com/v1", "static": True}, target="client")
    a = site(2, "call", "openai", "responses", client_id=c["id"], client_link="same-file", model={"value": "gpt-4o-mini", "static": True})
    b = site(3, "call", "openai", "chat.completions", client_id=c["id"], client_link="same-file")
    o, p = outcomes([c, a, b])
    assert o[2][0] == "migrated" and "natively" in o[2][1]
    assert o[3][0] == "flagged" and "404" in o[3][1]
    assert p["patches"] == []  # never wrap a client that already targets Router


def test_env_driven_router_base():
    c = site(1, "client", "openai", "OpenAI", base_url={"value": 'os.environ["RAMP_ROUTER_BASE_URL"]', "static": False}, target="client")
    a = site(2, "call", "openai", "responses", client_id=c["id"], client_link="same-file")
    o, _ = outcomes([c, a])
    assert o[2][0] == "migrated"


def test_ambiguous_client_reason_names_both():
    c1 = site(1, "client", "openai", "OpenAI", target="self.client")
    c2 = site(2, "client", "azure-openai", "AzureOpenAI", target="self.client")
    call = site(3, "call", "openai", "chat.completions", client_candidates=[c1["id"], c2["id"]], client_link="ambiguous")
    o, _ = outcomes([c1, c2, call])
    assert o[3][0] == "migrated" and "AzureOpenAI" in o[3][1]


def test_other_vendor_with_router_model_is_handoff():
    a = site(1, "call", "other", "other.chat", model={"value": "grok-4.3", "static": True})
    b = site(2, "call", "other", "other.chat", model={"value": "some-local-model", "static": True})
    c = site(3, "call", "other", "other.chat", model={"value": "gemini-2.5-flash", "static": True})
    o, _ = outcomes([a, b, c])
    assert o[1][0] == "handoff" and "grok-4.3" in o[1][1]
    assert o[2][0] == "kept"
    assert o[3][0] == "kept" and "bring-your-own-key" in o[3][1]  # Gemini needs BYOK on this account [measured catalog]


def test_coverage_math():
    c = site(1, "client", "openai", "OpenAI", target="client")
    sites = [c, site(2, "call", "openai", "chat.completions", client_id=c["id"], client_link="same-file"),
             site(3, "call", "openai", "embeddings", client_id=c["id"], client_link="same-file"),
             site(4, "call", "openai", "chat.completions", prod=False)]
    _, p = outcomes(sites)
    sm = p["summary"]
    assert (sm["sites_found"], sm["migrated"], sm["kept"], sm["nonprod_sites"]) == (2, 1, 1, 1)
    assert sm["callsite_coverage"] == 0.5 and sm["weighted_coverage"] == 1.0


def test_unmapped_static_model_is_flagged_not_migrated():
    c = site(1, "client", "anthropic", "Anthropic", target="client")
    a = site(2, "call", "anthropic", "messages", client_id=c["id"], client_link="same-file", model={"value": "claude-3-5-sonnet-20241022", "static": True})
    o, _ = outcomes([c, a])
    assert o[2][0] == "flagged" and "not in the Router catalog" in o[2][1]


def test_catalog_resolution_rules():
    assert CAT.resolve("gpt-4o-mini-2024-07-18")[0] == "gpt-4o-mini"
    assert CAT.resolve("gpt-4-turbo-preview")[0] is None
    assert CAT.resolve("gpt-4o-2024-05-13")[0] is None
    assert CAT.resolve("openai:gpt-4o")[0] == "gpt-4o"
    assert CAT.resolve("sonnet-4.6")[0] == "claude-sonnet-4-6"
    assert CAT.resolve("global.anthropic.claude-sonnet-5-5")[0] is None  # requires BYOK
