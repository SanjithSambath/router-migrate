"""The vendored Python runtime, exercised through the real openai/anthropic SDKs against router-sim."""
import asyncio
import json

import anthropic
import httpx
import openai
import pydantic
import pytest

from conftest import AsyncSplit, Split

TOOLS = [{"type": "function", "function": {"name": "get_weather", "description": "weather",
                                           "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]


def client(rt, split=None):
    split = split or Split()
    return openai.OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(inner=split))), split


def last(sim):
    return sim.records[-1]


def test_inactive_is_identity(rt, monkeypatch):
    monkeypatch.delenv("RAMP_ROUTER_API_KEY")
    c = openai.OpenAI()
    assert rt.wrap_openai(c) is c
    a = anthropic.Anthropic()
    assert rt.wrap_anthropic(a) is a


def test_kill_switch(rt, monkeypatch):
    monkeypatch.setenv("ROUTER_MIGRATE_DISABLE", "1")
    c = openai.OpenAI()
    assert rt.wrap_openai(c) is c


def test_wrap_routes_chat_to_router(rt, sim):
    split = Split()
    base = openai.OpenAI(http_client=httpx.Client(transport=split))
    c = rt.wrap_openai(base)
    assert c is base  # wrapped in place: same client object
    r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}], max_tokens=50)
    assert r.choices[0].message.content == "pong"
    assert r.choices[0].finish_reason == "stop"
    assert r.usage.prompt_tokens > 0
    assert split.provider_calls == []
    rec = last(sim)
    assert rec["path"] == "/v1/responses" and rec["valid"], rec
    req = rec["request"]
    assert req["max_output_tokens"] == 50
    assert req["service_tier"] == "default"  # non-streaming calls too: the account's flex default adds latency [measured]
    assert req["input"][0] == {"role": "system", "content": "be brief"}
    assert req["metadata"]["feature"].endswith(":test_wrap_routes_chat_to_router")


def test_chat_stream_with_usage(rt, sim):
    c, split = client(rt)
    chunks = list(c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True,
                                            stream_options={"include_usage": True}))
    text = "".join((ch.choices[0].delta.content or "") for ch in chunks if ch.choices)
    assert text == "pong"
    assert chunks[0].choices[0].delta.role == "assistant"
    finals = [ch.choices[0].finish_reason for ch in chunks if ch.choices and ch.choices[0].finish_reason]
    assert finals == ["stop"]
    assert chunks[-1].usage is not None and chunks[-1].choices == []
    assert last(sim)["request"]["service_tier"] == "default" and last(sim)["valid"]
    assert split.provider_calls == []


def test_tools_round_trip(rt, sim):
    c, _ = client(rt)
    msgs = [{"role": "user", "content": "weather in Paris?"}]
    r = c.chat.completions.create(model="gpt-4o-mini", messages=msgs, tools=TOOLS, tool_choice={"type": "function", "function": {"name": "get_weather"}})
    m = r.choices[0].message
    assert r.choices[0].finish_reason == "tool_calls"
    assert m.tool_calls[0].function.name == "get_weather" and json.loads(m.tool_calls[0].function.arguments) == {"city": "x"}
    sent = last(sim)["request"]
    assert sent["tools"][0]["name"] == "get_weather" and "function" not in sent["tools"][0]
    assert sent["tool_choice"] == {"type": "function", "name": "get_weather"}
    msgs += [m.model_dump(exclude_none=True), {"role": "tool", "tool_call_id": m.tool_calls[0].id, "content": "18C"}]
    r2 = c.chat.completions.create(model="gpt-4o-mini", messages=msgs, tools=TOOLS)
    assert r2.choices[0].message.content == "pong", last(sim)
    items = last(sim)["request"]["input"]
    assert [i.get("type", "message") for i in items] == ["message", "function_call", "function_call_output"]


def test_tools_stream(rt, sim):
    c, _ = client(rt)
    chunks = list(c.chat.completions.create(model="claude-haiku-4-5", messages=[{"role": "user", "content": "weather?"}], tools=TOOLS, stream=True))
    calls = [tc for ch in chunks if ch.choices for tc in (ch.choices[0].delta.tool_calls or [])]
    assert calls[0].id and calls[0].function.name == "get_weather"
    assert "".join(tc.function.arguments or "" for tc in calls) == json.dumps({"city": "x"})
    assert [ch.choices[0].finish_reason for ch in chunks if ch.choices and ch.choices[0].finish_reason] == ["tool_calls"]
    assert last(sim)["request"]["prompt_cache_key"]  # Claude via Responses needs it to cache [measured]


def test_structured_output_parse(rt, sim):
    class City(pydantic.BaseModel):
        name: str
        country: str
    c, _ = client(rt)
    parse = getattr(c.chat.completions, "parse", None) or c.beta.chat.completions.parse
    r = parse(model="gpt-4o-mini", messages=[{"role": "user", "content": "capital of France"}], response_format=City)
    assert r.choices[0].message.parsed == City(name="x", country="x")
    fmt = last(sim)["request"]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "City" and fmt["strict"] is True


def test_stop_emulated_for_openai_models(rt, sim):
    c, _ = client(rt)
    r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stop=["n"])
    assert r.choices[0].message.content == "po" and r.choices[0].finish_reason == "stop"
    assert "stop" not in last(sim)["request"] and last(sim)["valid"]
    s = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stop="ng", stream=True)
    assert "".join((ch.choices[0].delta.content or "") for ch in s if ch.choices) == "po"


def test_stop_forwarded_for_claude(rt, sim):
    c, _ = client(rt)
    c.chat.completions.create(model="claude-haiku-4-5", messages=[{"role": "user", "content": "hi"}], stop=["\n\n"])
    assert last(sim)["request"]["stop"] == ["\n\n"] and last(sim)["valid"]


def test_temperature_dropped_where_unsupported(rt, sim):
    c, _ = client(rt)
    r = c.chat.completions.create(model="gpt-5-nano", messages=[{"role": "user", "content": "hi"}], temperature=0.2, max_completion_tokens=200)
    assert r.choices[0].message.content == "pong"
    assert "temperature" not in last(sim)["request"] and last(sim)["valid"]


@pytest.mark.parametrize("kwargs", [
    {"model": "gpt-4-32k"},                                   # not in the catalog
    {"model": "gpt-4o-mini", "n": 2},                         # several choices
    {"model": "gpt-4o-mini", "logprobs": True},              # logprobs
    {"model": "gpt-4o-mini", "seed": 7},                      # seed: Router 400 unknown_parameter [measured]
    {"model": "gpt-4o-mini", "presence_penalty": 0.5},       # not measured as accepted (0, the default, is dropped and routes)
    {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": [{"type": "file", "file": {"file_id": "file-1"}}]}]},
    {"model": "gpt-4o-mini", "max_tokens": 5},               # below the Responses minimum of 16; clamping would change output
    {"model": "gpt-4o-mini", "tools": TOOLS, "tool_choice": {"type": "allowed_tools", "allowed_tools": {"mode": "auto", "tools": []}}},
])
def test_unservable_requests_stay_on_provider(rt, sim, kwargs):
    c, split = client(rt)
    r = c.chat.completions.create(**dict({"messages": [{"role": "user", "content": "hi"}]}, **kwargs))
    assert r.choices[0].message.content == "from-provider"
    assert split.provider_calls and sim.records == []


def test_audio_parts_stay_on_provider(rt, sim):
    c, split = client(rt)
    c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "AA", "format": "wav"}}]}])
    assert split.provider_calls and sim.records == []


def test_embeddings_stay_on_provider(rt, sim):
    c, split = client(rt)
    e = c.embeddings.create(model="text-embedding-3-small", input="x")
    assert e.data[0].embedding == [0.1] and split.provider_calls[0][1].endswith("/embeddings") and sim.records == []


def test_dated_model_mapped(rt, sim):
    c, _ = client(rt)
    c.chat.completions.create(model="gpt-4o-mini-2024-07-18", messages=[{"role": "user", "content": "hi"}])
    assert last(sim)["request"]["model"] == "gpt-4o-mini" and last(sim)["valid"]


def test_images_and_multimodal(rt, sim):
    c, _ = client(rt)
    c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": [
        {"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}}]}])
    parts = last(sim)["request"]["input"][0]["content"]
    assert parts == [{"type": "input_text", "text": "what is this"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"}]
    assert last(sim)["valid"]


def test_responses_api_passthrough_sanitized(rt, sim):
    c, split = client(rt)
    r = c.responses.create(model="gpt-4o-mini", input="hi", store=False)
    assert r.output_text == "pong" and "store" not in last(sim)["request"] and last(sim)["valid"]
    assert c.responses.create(model="gpt-4o-mini", input="keep this", store=True).output_text == "from-provider"  # asked for storage
    c.responses.create(model="gpt-4o-mini", input="again", previous_response_id="resp_123")
    assert split.provider_calls  # needs provider-side state: stays on OpenAI


def test_router_errors_surface_as_sdk_errors(rt, sim):
    c, _ = client(rt)
    with pytest.raises(openai.BadRequestError):
        c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "tool", "tool_call_id": "nope", "content": "x"}])


def test_legacy_functions_api(rt, sim):
    c, _ = client(rt)
    r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "weather"}],
                                  functions=[TOOLS[0]["function"]], function_call={"name": "get_weather"})
    assert r.choices[0].message.function_call.name == "get_weather" and r.choices[0].finish_reason == "function_call"


def test_async_client_and_stream(rt, sim):
    async def go():
        split = AsyncSplit()
        c = openai.AsyncOpenAI(http_client=httpx.AsyncClient(transport=rt.AsyncRouterTransport(inner=split)))
        r = await c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
        s = await c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True)
        text = "".join([(ch.choices[0].delta.content or "") async for ch in s if ch.choices])
        return r.choices[0].message.content, text, split.provider_calls
    content, text, calls = asyncio.run(go())
    assert content == "pong" and text == "pong" and calls == []


def test_async_wrap(rt, sim):
    async def go():
        c = rt.wrap_openai(openai.AsyncOpenAI())
        return (await c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])).choices[0].message.content
    assert asyncio.run(go()) == "pong"


def test_module_level_client(rt, sim, monkeypatch):
    monkeypatch.setattr(openai, "http_client", None, raising=False)
    monkeypatch.setattr(openai, "_client", None, raising=False)
    rt.install_openai_module()
    r = openai.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert r.choices[0].message.content == "pong" and last(sim)["valid"]


def test_anthropic_wrap(rt, sim):
    a = rt.wrap_anthropic(anthropic.Anthropic())
    m = a.messages.create(model="claude-haiku-4-5-20251001", max_tokens=16, messages=[{"role": "user", "content": "hi"}])
    assert m.content[0].text == "pong"
    rec = last(sim)
    assert rec["path"] == "/v1/messages" and rec["request"]["model"] == "claude-haiku-4-5" and rec["valid"]
    with a.messages.stream(model="claude-sonnet-4-5", max_tokens=16, messages=[{"role": "user", "content": "hi"}]) as s:
        assert "".join(s.text_stream) == "pong"


def test_anthropic_unknown_model_stays_on_provider(rt, sim):
    split = Split()
    a = anthropic.Anthropic(http_client=httpx.Client(transport=rt.RouterTransport(inner=split)))
    m = a.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=16, messages=[{"role": "user", "content": "hi"}])
    assert m.content[0].text == "from-provider" and sim.records == []


def test_non_openai_base_url_untouched(rt, sim):
    split = Split()
    c = openai.OpenAI(base_url="http://localhost:11434/v1", http_client=httpx.Client(transport=rt.RouterTransport(inner=split)))
    c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert split.provider_calls and sim.records == []


def test_wrap_keeps_existing_transport(rt, sim):
    """A client built with a custom transport keeps it underneath the router transport."""
    class Marker(httpx.HTTPTransport):
        used = 0

        def handle_request(self, request):
            Marker.used += 1
            return super().handle_request(request)
    base = openai.OpenAI(http_client=httpx.Client(transport=Marker()))
    c = rt.wrap_openai(base)
    assert c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "pong"
    assert Marker.used == 1 and last(sim)["valid"]


def test_message_name_stays_on_provider(rt, sim):
    c, split = client(rt)
    c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "name": "alice", "content": "hi"}])
    assert split.provider_calls and sim.records == []


def test_metadata_at_16_keys_gets_no_feature_tag(rt, sim):
    c, _ = client(rt)
    meta = {"k%d" % i: "v" for i in range(16)}
    c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], metadata=meta, store=False)
    assert last(sim)["request"]["metadata"] == meta


def test_feature_tag_opt_out(rt, sim, monkeypatch):
    monkeypatch.setenv("ROUTER_MIGRATE_FEATURE_TAG", "off")
    c, _ = client(rt)
    c.chat.completions.create(model="claude-haiku-4-5", messages=[{"role": "user", "content": "hi"}])
    req = last(sim)["request"]
    assert "metadata" not in req and "prompt_cache_key" not in req


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "on", "yes"])
def test_kill_switch_case_insensitive(rt, monkeypatch, value):
    monkeypatch.setenv("ROUTER_MIGRATE_DISABLE", value)
    assert not rt.active()


def test_runtime_never_mutates_environment(tmp_path):
    """Importing the runtime with the Router key set must not invent a provider key (it would shadow load_dotenv())."""
    import os
    import subprocess
    import sys
    from conftest import RENDERED
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    env["RAMP_ROUTER_API_KEY"] = "rm-test-key-not-a-real-key"
    code = "import sys, os; sys.path.insert(0, %r); import router_runtime; print(os.environ.get('OPENAI_API_KEY'))" % RENDERED
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "None", out.stderr


def test_plan_request_never_raises(rt):
    """Odd but legal-looking bodies must never escape the transport: the provider gets them unchanged."""
    for body in ({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "metadata": ["x"]},
                 {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "stop": 5},
                 {"model": "gpt-4o-mini", "messages": "not a list"}):
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json=body)
        rt.plan_request(req)  # returns a route or None; must not raise
    assert rt.plan_request(httpx.Request("POST", "https://api.openai.com/v1/chat/completions", content=b"{not json")) is None


# ---- Responses API state (C1/H2)
def test_previous_response_id_chain_continues_on_router(rt, sim):
    c, split = client(rt)
    r1 = c.responses.create(model="gpt-4o-mini", input="my name is Ada")
    r2 = c.responses.create(model="gpt-4o-mini", input="what is my name?", previous_response_id=r1.id)
    assert r2.output_text == "pong" and split.provider_calls == []
    req = last(sim)["request"]
    assert "previous_response_id" not in req and last(sim)["valid"]
    assert req["input"][0] == {"role": "user", "content": "my name is Ada"}
    assert req["input"][-1] == {"role": "user", "content": "what is my name?"} and len(req["input"]) >= 3
    assert c.responses.retrieve(r1.id).id == r1.id and split.provider_calls == []  # Router-issued id answered locally


def test_provider_issued_previous_response_id_stays_on_provider(rt, sim):
    c, split = client(rt)
    r = c.responses.create(model="gpt-4o-mini", input="again", previous_response_id="resp_from_openai")
    assert r.output_text == "from-provider" and split.provider_calls and sim.records == []


def test_router_id_missing_from_memory_is_a_clear_error_not_a_provider_404(rt, sim):
    c, split = client(rt)
    r1 = c.responses.create(model="gpt-4o-mini", input="hi")
    rt._CHAINS.clear()
    with pytest.raises(openai.BadRequestError, match="served by Router"):
        c.responses.create(model="gpt-4o-mini", input="again", previous_response_id=r1.id)
    assert split.provider_calls == []


def test_client_level_responses_opt_out(rt, sim):
    split = Split()
    c = openai.OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(inner=split, opts={"responses": False})))
    assert c.responses.create(model="gpt-4o-mini", input="hi").output_text == "from-provider"
    assert c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "pong"


@pytest.mark.parametrize("extra", [
    {"input": [{"type": "reasoning", "id": "rs_1", "summary": []}, {"role": "user", "content": "go on"}]},
    {"input": [{"type": "item_reference", "id": "msg_1"}]},
    {"input": "find it", "tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}]},
    {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file-1"}]}]},
])
def test_provider_side_state_stays_on_provider(rt, sim, extra):
    c, split = client(rt)
    assert c.responses.create(model="gpt-4o-mini", **extra).output_text == "from-provider"
    assert sim.records == []


def test_reasoning_models_get_encrypted_reasoning(rt, sim):
    c, _ = client(rt)
    c.responses.create(model="gpt-5-nano", input="hi")
    assert "reasoning.encrypted_content" in last(sim)["request"]["include"] and last(sim)["valid"]


# ---- provider fallback when Router refuses before generating (M4)
def test_fallback_on_router_unknown_parameter(rt, sim):
    c, split = client(rt)
    r = c.responses.create(model="gpt-4o-mini", input="hi", extra_body={"made_up_field": 1})
    assert r.output_text == "from-provider" and len(split.provider_calls) == 1
    assert sim.records[-1]["status"] == 400


def test_fallback_can_be_disabled(rt, sim, monkeypatch):
    monkeypatch.setenv("ROUTER_MIGRATE_FALLBACK", "0")
    c, split = client(rt)
    with pytest.raises(openai.BadRequestError):
        c.responses.create(model="gpt-4o-mini", input="hi", extra_body={"made_up_field": 1})
    assert split.provider_calls == []


def test_model_not_found_falls_back_and_is_remembered(rt, sim, monkeypatch):
    monkeypatch.setenv("ROUTER_MIGRATE_MODEL_MAP", '{"gpt-4o-mini": "not-a-router-model"}')
    c, split = client(rt)
    for _ in range(2):
        assert c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "from-provider"
    assert len(sim.records) == 1 and len(split.provider_calls) == 2  # second call skips Router


def test_generation_errors_are_not_retried_on_provider(rt, sim):
    c, split = client(rt)
    with pytest.raises(openai.BadRequestError):
        c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "tool", "tool_call_id": "nope", "content": "x"}])
    assert split.provider_calls == []


# ---- in-place wrapping keeps the client's own configuration (H3)
def test_in_place_wrap_keeps_mounts_headers_and_hooks(rt, sim):
    seen = []

    class Marker(httpx.HTTPTransport):
        def handle_request(self, request):
            seen.append((request.url.host, request.headers.get("x-team")))
            return super().handle_request(request)
    hooks = []
    hc = httpx.Client(mounts={"all://": Marker()}, headers={"x-team": "search"}, event_hooks={"request": [lambda r: hooks.append(1)]})
    c = openai.OpenAI(http_client=hc)
    assert rt.wrap_openai(c) is c and c._client is hc
    assert rt.wrap_openai(c) is c  # idempotent
    assert c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "pong"
    assert seen == [("127.0.0.1", None)] and hooks == [1] and last(sim)["valid"]  # through the mount; provider headers not sent to Router


def test_wrap_langchain_chat_models(rt, sim):
    pytest.importorskip("langchain_openai")
    pytest.importorskip("langchain_anthropic")
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI
    llm = rt.wrap_langchain(ChatOpenAI(model="gpt-4o-mini"))
    assert llm.invoke("hi").content == "pong" and last(sim)["path"] == "/v1/responses" and last(sim)["valid"]
    llm = rt.wrap_langchain(ChatAnthropic(model="claude-haiku-4-5-20251001"))
    assert llm.invoke("hi").content == "pong"
    assert last(sim)["path"] == "/v1/messages" and last(sim)["request"]["model"] == "claude-haiku-4-5"


def test_chat_anthropic_kwargs_no_longer_swaps_wholesale(rt):
    assert rt.chat_anthropic_kwargs() == {}


# ---- streaming translation edge cases (M3)
def _sse(*events, sep=b"\n\n"):
    return b"".join(b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + sep for e in events)


def _run(rt, raw, ctx=None, split_at=None):
    w = rt._ChunkWriter(dict({"model": "gpt-4o-mini", "stops": []}, **(ctx or {})))
    out = []
    chunks = [raw[i:i + split_at] for i in range(0, len(raw), split_at)] if split_at else [raw]
    for ch in chunks:
        out += w.feed(ch)
    out += w.finish()
    evs = [json.loads(l[6:]) for l in b"".join(out).decode().split("\n\n") if l.startswith("data: {")]
    return evs, b"".join(out)


def _deltas(evs, key):
    return [e["choices"][0]["delta"].get(key) for e in evs if e.get("choices") and e["choices"][0]["delta"].get(key) is not None]


def _finish(evs):
    return [e["choices"][0]["finish_reason"] for e in evs if e.get("choices") and e["choices"][0]["finish_reason"]]


CREATED = {"type": "response.created", "response": {"id": "resp_1", "model": "gpt-4o-mini"}}
DONE = {"type": "response.completed", "response": {"id": "resp_1", "usage": {"input_tokens": 1, "output_tokens": 1}}}


def test_stream_crlf_framing(rt):
    evs, raw = _run(rt, _sse(CREATED, {"type": "response.output_text.delta", "delta": "hello"}, DONE, sep=b"\r\n\r\n"), split_at=7)
    assert "".join(_deltas(evs, "content")) == "hello" and _finish(evs) == ["stop"] and raw.endswith(b"data: [DONE]\n\n")


def test_stream_refusal_delta(rt):
    evs, _ = _run(rt, _sse(CREATED, {"type": "response.refusal.delta", "delta": "I can't"}, DONE))
    assert _deltas(evs, "refusal") == ["I can't"]


def test_stream_tool_args_only_in_done_events(rt):
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "f", "arguments": ""}
    evs, _ = _run(rt, _sse(CREATED, {"type": "response.output_item.added", "item": item},
                           {"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": '{"x":1}'},
                           {"type": "response.output_item.done", "item": dict(item, arguments='{"x":1}')}, DONE))
    args = "".join(tc[0]["function"].get("arguments") or "" for tc in _deltas(evs, "tool_calls"))
    assert args == '{"x":1}' and _finish(evs) == ["tool_calls"]


def test_stream_content_filter_and_truncated_tool_call(rt):
    cf = {"type": "response.incomplete", "response": {"id": "r", "incomplete_details": {"reason": "content_filter"}}}
    evs, _ = _run(rt, _sse(CREATED, {"type": "response.output_text.delta", "delta": "x"}, cf))
    assert _finish(evs) == ["content_filter"]
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "f", "arguments": ""}
    cut = {"type": "response.incomplete", "response": {"id": "r", "incomplete_details": {"reason": "max_output_tokens"}}}
    evs, _ = _run(rt, _sse(CREATED, {"type": "response.output_item.added", "item": item},
                           {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"x": "tr'}, cut))
    assert _finish(evs) == ["length"]


def test_stream_last_event_without_blank_line_is_flushed(rt):
    raw = _sse(CREATED, {"type": "response.output_text.delta", "delta": "hi"}) + b"data: " + json.dumps(DONE).encode() + b"\n"
    evs, raw_out = _run(rt, raw)
    assert _finish(evs) == ["stop"] and raw_out.count(b"[DONE]") == 1


def test_nonstream_truncated_tool_call_is_length(rt):
    resp = {"id": "r", "output": [{"type": "function_call", "call_id": "c", "name": "f", "arguments": '{"x": "tr'}],
            "incomplete_details": {"reason": "max_output_tokens"}}
    assert rt.responses_to_chat(resp, {"stops": []})["choices"][0]["finish_reason"] == "length"


def test_default_presence_penalty_and_legacy_function_names_still_route(rt, sim):
    c, split = client(rt)
    c.chat.completions.create(model="gpt-4o-mini", presence_penalty=0, messages=[
        {"role": "user", "content": "weather"}, {"role": "assistant", "content": None, "function_call": {"name": "get_weather", "arguments": "{}"}},
        {"role": "function", "name": "get_weather", "content": "sunny"}], functions=[TOOLS[0]["function"]])
    assert split.provider_calls == [] and "presence_penalty" not in last(sim)["request"] and last(sim)["valid"]


def test_legacy_functions_stream_uses_function_call_deltas(rt):
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "f", "arguments": ""}
    evs, _ = _run(rt, _sse(CREATED, {"type": "response.output_item.added", "item": item},
                           {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{}"}, DONE), ctx={"legacy_functions": True})
    assert _deltas(evs, "function_call") == [{"name": "f", "arguments": ""}, {"arguments": "{}"}] and _finish(evs) == ["function_call"]


def test_router_chain_turn_that_cannot_route_errors_locally(rt, sim):
    c, split = client(rt)
    r1 = c.responses.create(model="gpt-4o-mini", input="hi")
    with pytest.raises(openai.BadRequestError, match="served by Router"):
        c.responses.create(model="gpt-4-32k", input="again", previous_response_id=r1.id)
    with pytest.raises(openai.BadRequestError, match="provider-side state"):
        c.responses.create(model="gpt-4o-mini", input="again", previous_response_id=r1.id,
                           tools=[{"type": "file_search", "vector_store_ids": ["vs_1"]}])
    c.responses.delete(r1.id)
    assert split.provider_calls == [] and r1.id not in rt._CHAINS


# ---- v2 unlocks (customer-approved, per-request safe)
def test_unlock_vendor_host_routes_only_catalog_models(rt, sim):
    split = Split()
    c = openai.OpenAI(base_url="https://api.x.ai/v1", http_client=httpx.Client(transport=split))
    rt.wrap_openai(c, unlock_hosts=["api.x.ai"])
    r = c.chat.completions.create(model="grok-4.3", messages=[{"role": "user", "content": "hi"}])
    assert r.choices[0].message.content == "pong" and split.provider_calls == []
    assert last(sim)["request"]["model"] == "grok-4.3" and last(sim)["valid"]
    sim.records.clear()
    r = c.chat.completions.create(model="grok-2-latest", messages=[{"role": "user", "content": "hi"}])  # not a catalog id
    assert r.choices[0].message.content == "from-provider" and sim.records == [] and "api.x.ai" in split.provider_calls[-1][1]


def test_vendor_host_untouched_without_unlock(rt, sim):
    split = Split()
    c = openai.OpenAI(base_url="https://api.x.ai/v1", http_client=httpx.Client(transport=split))
    rt.wrap_openai(c)
    c.chat.completions.create(model="grok-4.3", messages=[{"role": "user", "content": "hi"}])
    assert sim.records == [] and split.provider_calls


def test_drop_betas_routes_only_when_every_flag_is_approved(rt, sim):
    split = Split()
    a = anthropic.Anthropic(http_client=httpx.Client(transport=split))
    rt.wrap_anthropic(a, drop_betas=["prompt-caching-2024-07-31"])
    m = a.messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[{"role": "user", "content": "hi"}],
                          extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"})
    assert m.content[0].text == "pong" and split.provider_calls == [] and last(sim)["valid"]
    sim.records.clear()
    m = a.messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[{"role": "user", "content": "hi"}],
                          extra_headers={"anthropic-beta": "prompt-caching-2024-07-31,computer-use-2025-01-24"})
    assert m.content[0].text == "from-provider" and sim.records == []
