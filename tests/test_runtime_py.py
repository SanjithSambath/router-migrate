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
    c = rt.wrap_openai(openai.OpenAI())
    assert c is not None
    r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}], max_tokens=5)
    assert r.choices[0].message.content == "pong"
    assert r.choices[0].finish_reason == "stop"
    assert r.usage.prompt_tokens > 0
    rec = last(sim)
    assert rec["path"] == "/v1/responses" and rec["valid"], rec
    req = rec["request"]
    assert req["max_output_tokens"] == 16  # clamped to the Responses minimum
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
])
def test_unservable_requests_stay_on_provider(rt, sim, kwargs):
    c, split = client(rt)
    r = c.chat.completions.create(messages=[{"role": "user", "content": "hi"}], **kwargs)
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
    r = c.responses.create(model="gpt-4o-mini", input="hi", store=True)
    assert r.output_text == "pong" and "store" not in last(sim)["request"] and last(sim)["valid"]
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
