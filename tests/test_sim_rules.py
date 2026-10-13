"""Every router-sim rule, one request each. [measured] rules mirror live Router results from router-live-check."""
import json
import urllib.request

import pytest

OK = {"model": "gpt-4o-mini", "input": "hi"}
CASES = [
    # (name, path, body, headers, expected status, expected code/substring)
    ("ok", "/v1/responses", OK, {}, 200, None),
    ("reasoning item without encrypted_content", "/v1/responses", dict(OK, input=[{"type": "reasoning", "id": "rs_1", "summary": []}, {"role": "user", "content": "go"}]),
     {}, 400, "invalid_request_error"),
    ("reasoning item with encrypted_content", "/v1/responses", dict(OK, input=[{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAA"},
                                                                          {"role": "user", "content": "go"}]), {}, 200, None),
    ("file_search vector store", "/v1/responses", dict(OK, tools=[{"type": "file_search", "vector_store_ids": ["vs_x"]}]), {}, 404, None),
    ("chat leftovers: messages", "/v1/responses", dict(OK, messages=[]), {}, 400, "unsupported_parameter"),
    ("chat leftovers: max_tokens", "/v1/responses", dict(OK, max_tokens=5), {}, 400, "unknown_parameter"),
    ("chat leftovers: response_format", "/v1/responses", dict(OK, response_format={}), {}, 400, "unsupported_parameter"),
    ("chat leftovers: seed", "/v1/responses", dict(OK, seed=1), {}, 400, "unknown_parameter"),
    ("chat leftovers: n", "/v1/responses", dict(OK, n=1), {}, 400, "unknown_parameter"),
    ("chat leftovers: logprobs", "/v1/responses", dict(OK, logprobs=True), {}, 400, "unknown_parameter"),
    ("unknown field", "/v1/responses", dict(OK, foo=1), {}, 400, "unknown_parameter"),
    ("model and models", "/v1/responses", dict(OK, models=["openai:gpt-4o-mini"]), {}, 400, "invalid_request"),
    ("models fallback skips bad", "/v1/responses", {"models": ["openai:nope", "openai:gpt-4o-mini"], "input": "x"}, {}, 200, None),
    ("models none valid", "/v1/responses", {"models": ["openai:nope"], "input": "x"}, {}, 404, "model_not_found"),
    ("dated id 404", "/v1/responses", dict(OK, model="gpt-4o-mini-2024-07-18"), {}, 404, "model_not_found"),
    ("prefixed id 404", "/v1/responses", dict(OK, model="openai:gpt-4o-mini"), {}, 404, "model_not_found"),
    ("alias ok", "/v1/responses", dict(OK, model="sonnet-4.6"), {}, 200, None),
    ("store true", "/v1/responses", dict(OK, store=True), {}, 400, "invalid_request"),
    ("previous_response_id openai", "/v1/responses", dict(OK, previous_response_id="resp_1"), {}, 400, "invalid_request"),
    ("previous_response_id claude ok", "/v1/responses", dict(OK, model="claude-haiku-4-5", previous_response_id="r"), {}, 200, None),
    ("conversation", "/v1/responses", dict(OK, conversation="c"), {}, 400, "invalid_request"),
    ("background", "/v1/responses", dict(OK, background=True), {}, 501, "not_implemented"),
    ("stop on openai", "/v1/responses", dict(OK, stop=["x"]), {}, 501, "not_implemented"),
    ("stop on claude", "/v1/responses", dict(OK, model="claude-haiku-4-5", stop=["x"]), {}, 200, None),
    ("temperature unsupported", "/v1/responses", dict(OK, model="gpt-5-nano", temperature=0.2), {}, 400, "invalid_request_error"),
    ("effort on non-reasoning", "/v1/responses", dict(OK, reasoning={"effort": "low"}), {}, 400, "unsupported_parameter"),
    ("effort on claude small budget", "/v1/responses", dict(OK, model="claude-haiku-4-5", reasoning={"effort": "low"}, max_output_tokens=400), {}, 400, "budget_tokens"),
    ("effort on claude room", "/v1/responses", dict(OK, model="claude-haiku-4-5", reasoning={"effort": "low"}, max_output_tokens=2000), {}, 200, None),
    ("allow_flex_tier bad type", "/v1/responses", dict(OK, allow_flex_tier="yes"), {}, 400, "invalid_request"),
    ("fallback_timeout needs stream", "/v1/responses", dict(OK, fallback_timeout=2), {}, 400, "invalid_request"),
    ("provider_timeout max", "/v1/responses", dict(OK, provider_timeout=99999), {}, 400, "invalid_request"),
    ("nested chat tool", "/v1/responses", dict(OK, tools=[{"type": "function", "function": {"name": "f"}}]), {}, 400, "missing_required_parameter"),
    ("strict default loose schema", "/v1/responses", dict(OK, tools=[{"type": "function", "name": "f", "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}]), {}, 400, "invalid_function_parameters"),
    ("strict false loose ok", "/v1/responses", dict(OK, tools=[{"type": "function", "name": "f", "strict": False, "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}]), {}, 200, None),
    ("tool_choice nested", "/v1/responses", dict(OK, tool_choice={"type": "function", "function": {"name": "f"}}), {}, 400, "missing_required_parameter"),
    ("tool role", "/v1/responses", dict(OK, input=[{"role": "tool", "content": "x"}]), {}, 400, "invalid_value"),
    ("assistant input_text", "/v1/responses", dict(OK, input=[{"role": "assistant", "content": [{"type": "input_text", "text": "x"}]}]), {}, 400, "invalid_value"),
    ("orphan function output", "/v1/responses", dict(OK, input=[{"type": "function_call_output", "call_id": "c", "output": "x"}]), {}, 400, "invalid_request_error"),
    ("bad text.format", "/v1/responses", dict(OK, text={"format": {"type": "xml"}}), {}, 400, "invalid_value"),
    ("json_schema missing name", "/v1/responses", dict(OK, text={"format": {"type": "json_schema", "schema": {}}}), {}, 400, "missing_required_parameter"),
    ("no input", "/v1/responses", {"model": "gpt-4o-mini"}, {}, 400, "missing_required_parameter"),
    ("byok-only model unavailable", "/v1/responses", dict(OK, model="gemini-2.5-flash"), {}, 200, None),
    ("messages ok", "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}, {"anthropic-version": "2023-06-01"}, 200, None),
    ("messages no version", "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}, {}, 400, "anthropic-version"),
    ("messages tool_choice names a missing tool", "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}],
                                                                     "tool_choice": {"type": "tool", "name": "point_at"}}, {"anthropic-version": "2023-06-01"}, 400, "not found"),
    ("messages extra field claude", "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}], "provider_timeout": 30}, {"anthropic-version": "2023-06-01"}, 400, "Extra inputs"),
    ("messages extra field gpt ok", "/v1/messages", {"model": "gpt-4o-mini", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}], "provider_timeout": 30}, {"anthropic-version": "2023-06-01"}, 200, None),
    ("messages stop_sequences gpt", "/v1/messages", {"model": "gpt-4o-mini", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}], "stop_sequences": [","]}, {"anthropic-version": "2023-06-01"}, 501, "Stop sequences"),
    ("messages system role", "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": [{"role": "system", "content": "hi"}]}, {"anthropic-version": "2023-06-01"}, 400, "role"),
    ("count_tokens", "/v1/messages/count_tokens", {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": "hi"}]}, {"anthropic-version": "2023-06-01"}, 200, None),
    ("chat completions 404", "/v1/chat/completions", {"model": "gpt-4o-mini", "messages": []}, {}, 404, "404 page not found"),
    ("embeddings 404", "/v1/embeddings", {"model": "x", "input": "x"}, {}, 404, "404 page not found"),
]


def post(base, path, body, headers=None, ua="router-migrate-tests", auth=True):
    h = {"Content-Type": "application/json", "User-Agent": ua}
    if auth:
        h["Authorization"] = "Bearer rm-test-key-not-a-real-key"
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@pytest.mark.parametrize("name,path,body,headers,status,needle", CASES, ids=[c[0] for c in CASES])
def test_rule(sim, name, path, body, headers, status, needle):
    if name == "byok-only model unavailable":
        pytest.skip("BYOK models are listed in the catalog; Router behavior without BYOK was not measured, so the sim does not invent one")
    code, text = post(sim.base, path, body, headers)
    assert code == status, (name, text[:300])
    if needle:
        assert needle in text, (name, text[:300])
    if code >= 400:
        rec = sim.records[-1]
        assert rec["valid"] is False and rec.get("rule_source", "").startswith("["), rec


def test_urllib_default_user_agent_blocked(sim):
    code, text = post(sim.base, "/v1/responses", OK, ua="Python-urllib/3.11")
    assert code == 403 and "1010" in text


def test_missing_key(sim):
    code, _ = post(sim.base, "/v1/responses", OK, auth=False)
    assert code == 401


def test_streams_are_well_formed(sim):
    code, text = post(sim.base, "/v1/responses", dict(OK, stream=True))
    events = [l[7:] for l in text.splitlines() if l.startswith("event: ")]
    assert code == 200 and events[0] == "response.created" and events[-1] == "response.completed"
    code, text = post(sim.base, "/v1/messages", {"model": "claude-haiku-4-5", "max_tokens": 16, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                      {"anthropic-version": "2023-06-01"})
    events = [l[7:] for l in text.splitlines() if l.startswith("event: ")]
    assert code == 200 and events[0] == "message_start" and events[-1] == "message_stop"


def test_get_routes(sim):
    req = urllib.request.Request(sim.base + "/v1/models", headers={"Authorization": "Bearer rm-test-key-not-a-real-key", "User-Agent": "t"})
    with urllib.request.urlopen(req) as r:
        assert len(json.load(r)["data"]) > 90
    req = urllib.request.Request(sim.base + "/v1/models/gpt-4o", headers={"Authorization": "Bearer rm-test-key-not-a-real-key", "User-Agent": "t"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404
