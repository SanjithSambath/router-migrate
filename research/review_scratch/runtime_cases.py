"""Adversarial review: drive the vendored Python runtime with the real openai SDK.

Part A: requests through router-sim (no real provider or Router calls; provider-bound requests are answered locally by a stub).
Part B: streaming translation against a crafted upstream (shapes router-sim never produces).
Usage: .venv/bin/python research/review_scratch/runtime_cases.py
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import httpx  # noqa: E402
import openai  # noqa: E402

from router_migrate.catalog import Catalog  # noqa: E402
from router_migrate.runtime import render  # noqa: E402
from router_migrate.sim.server import serve  # noqa: E402

SIM_KEY = "rm-review-sim-key"
os.environ.pop("RAMP_ROUTER_API_KEY", None)
os.environ["OPENAI_API_KEY"] = "sk-provider-test-not-real"


def load_runtime():
    p = os.path.join(tempfile.mkdtemp(prefix="rm-review-"), "router_runtime.py")
    open(p, "w").write(render("router_runtime.py", Catalog.load()))
    spec = importlib.util.spec_from_file_location("review_router_runtime", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rt = load_runtime()


class Recorder(httpx.BaseTransport):
    """Inner transport: records every request; provider hosts get a canned chat.completion (never leaves the machine)."""

    def __init__(self, upstream=None):
        self.seen = []
        self.real = httpx.HTTPTransport()
        self.upstream = upstream  # callable(request) -> httpx.Response for the Router side (Part B)

    def handle_request(self, req):
        body = None
        try:
            body = json.loads(req.content or b"null")
        except ValueError:
            pass
        self.seen.append({"host": req.url.host, "path": req.url.path, "headers": {k.lower(): v for k, v in req.headers.items()}, "body": body})
        if req.url.host in ("api.openai.com", "api.anthropic.com"):
            return httpx.Response(200, json={"id": "chatcmpl-provider", "object": "chat.completion", "created": 1, "model": "provider",
                                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "FROM-PROVIDER"}, "finish_reason": "stop"}]})
        if self.upstream:
            return self.upstream(req)
        return self.real.handle_request(req)


def client(rec, is_async=False):
    if is_async:
        return openai.AsyncOpenAI(http_client=httpx.AsyncClient(transport=rt.AsyncRouterTransport(inner=AsyncAdapter(rec))), max_retries=0)
    return openai.OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(inner=rec)), max_retries=0)


class AsyncAdapter(httpx.AsyncBaseTransport):
    def __init__(self, rec):
        self.rec = rec

    async def handle_async_request(self, req):
        await req.aread()
        r = self.rec.handle_request(req)
        return r


def show(title, fn):
    print("\n== " + title)
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print("   RAISED %s: %s" % (type(e).__name__, str(e)[:300]))


def last_router(rec):
    rs = [x for x in rec.seen if x["host"] not in ("api.openai.com", "api.anthropic.com")]
    return rs[-1] if rs else None


def part_a():
    srv, sim, base = serve()
    os.environ.update(RAMP_ROUTER_API_KEY=SIM_KEY, RAMP_ROUTER_BASE_URL=base)
    try:
        def a1():
            rec = Recorder()
            msgs = [
                {"role": "system", "content": "You are terse."},
                {"role": "system", "content": "Second system message."},
                {"role": "developer", "content": "dev note"},
                {"role": "user", "name": "alice", "content": "What is the weather in Paris? (asked by alice)"},
                {"role": "user", "name": "bob", "content": "and Tokyo (asked by bob)"},
                {"role": "assistant", "content": "Checking both.", "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Paris\"}"}},
                    {"id": "call_2", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Tokyo\"}"}}]},
                {"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "18C"}, {"type": "text", "text": " sunny"}]},
                {"role": "tool", "tool_call_id": "call_2", "content": "19C"},
                {"role": "assistant", "content": [{"type": "refusal", "refusal": "I won't share the exact address."}]},
                {"role": "user", "content": ""},
            ]
            r = client(rec).chat.completions.create(model="gpt-4o-mini", messages=msgs)
            sent = last_router(rec)["body"]
            print("   Router body input items:")
            for it in sent["input"]:
                print("     ", json.dumps(it)[:150])
            print("   -> names 'alice'/'bob' present in body:", "alice\"" in json.dumps(sent) and "\"name\": \"alice" in json.dumps(sent))
            print("   -> assistant refusal turn kept:", "won't share" in json.dumps(sent))
            print("   response finish_reason=%s content=%r" % (r.choices[0].finish_reason, r.choices[0].message.content))
        show("A1 message shapes (multi-system, developer, name, assistant text+tool_calls, list tool content, assistant refusal, empty user)", a1)

        def a2():
            rec = Recorder()
            client(rec).chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], seed=42,
                                                presence_penalty=0.5, frequency_penalty=0.1, user="u1", max_tokens=1)
            b = last_router(rec)["body"]
            print("   routed to Router: yes; keys sent:", sorted(b))
            print("   seed sent: %s (silently dropped, no fallback)   max_output_tokens=%s (caller asked max_tokens=1)" % ("seed" in b, b.get("max_output_tokens")))
            print("   presence_penalty forwarded:", b.get("presence_penalty"), "(live check measured only frequency_penalty on gpt-4o-mini)")
        show("A2 dropped / forwarded params", a2)

        def a3():
            rec = Recorder()
            meta = {"k%02d" % i: "v" for i in range(16)}
            client(rec).chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], metadata=meta, store=True)
            b = last_router(rec)["body"]
            print("   caller metadata keys=16, sent metadata keys=%d (OpenAI caps metadata at 16 keys); sim verdict valid=%s" % (
                len(b["metadata"]), sim.records[-1].get("valid")))
        show("A3 metadata key limit", a3)

        def a4():
            rec = Recorder()
            try:
                client(rec).chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
                                                    tools=[{"type": "function", "function": {"name": "a", "parameters": {"type": "object", "properties": {}}}},
                                                           {"type": "function", "function": {"name": "b", "parameters": {"type": "object", "properties": {}}}}],
                                                    tool_choice={"type": "allowed_tools", "allowed_tools": {"mode": "auto", "tools": [{"type": "function", "function": {"name": "a"}}]}})
            except Exception as e:  # noqa: BLE001
                print("   RAISED %s (no provider fallback): %s" % (type(e).__name__, str(e)[:160]))
            b = last_router(rec)["body"]
            print("   tool_choice sent to Router:", b.get("tool_choice"), "| provider requests:", sum(1 for x in rec.seen if x["host"] == "api.openai.com"))
        show("A4 tool_choice allowed_tools (valid Chat Completions shape)", a4)

        def a5():
            print("   resolve_model('gpt-5.3-codex') ->", rt.resolve_model("gpt-5.3-codex"),
                  "(live: 403 data_residency_mismatch on both surfaces; the runtime would route it and has no fallback on 4xx)")
        show("A5 catalog id the account cannot call", a5)

        def a6():
            rec = Recorder()
            c = client(rec)
            c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
            h = last_router(rec)["headers"]
            print("   headers sent to Router:", sorted(h))
            print("   provider key present in Router request:", any("sk-provider-test" in v for v in h.values()))
            os.environ.pop("RAMP_ROUTER_API_KEY")
            rec2 = Recorder()
            c2 = client(rec2)
            c2.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
            print("   key unset -> hosts contacted:", [x["host"] for x in rec2.seen], "| wrap_openai returns same object:",
                  rt.wrap_openai(c2) is c2)
            os.environ["RAMP_ROUTER_API_KEY"] = SIM_KEY
        show("A6 key handling (Router key only to Router; inert when unset)", a6)

        def a7():
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:3128"
            try:
                c = openai.OpenAI()
                print("   before wrap: client._client._mounts =", {str(k.pattern): type(v).__name__ for k, v in c._client._mounts.items()})
                w = rt.wrap_openai(c)
                print("   after wrap:  client._client._mounts =", {str(k.pattern): type(v).__name__ for k, v in w._client._mounts.items()},
                      "-> HTTPS_PROXY ignored for Router AND provider-fallback traffic")
                c3 = openai.OpenAI(http_client=httpx.Client(proxy="http://127.0.0.1:3128"))
                w3 = rt.wrap_openai(c3)
                print("   explicit httpx.Client(proxy=...): mounts before=%d after=%d" % (len(c3._client._mounts), len(w3._client._mounts)))
            finally:
                os.environ.pop("HTTPS_PROXY", None)
        show("A7 proxy settings after wrap_openai", a7)

        def a8():
            rec = Recorder()
            r = client(rec).chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
            print("   non-stream request service_tier sent:", last_router(rec)["body"].get("service_tier"),
                  "| sim served tier:", r.service_tier, "(live: plain calls are served at flex by account default)")
        show("A8 non-streaming calls get the account default tier (flex)", a8)
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------------------ Part B
def sse(events, sep=b"\n\n"):
    return b"".join(b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + sep for e in events)


def resp_shell(status="completed", inc=None, usage=True):
    r = {"id": "resp_x", "object": "response", "created_at": 1, "model": "gpt-4o-mini", "status": status, "output": [],
         "incomplete_details": inc}
    if usage:
        r["usage"] = {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}
    return r


def stream_case(events, model="gpt-4o-mini", stop=None, sep=b"\n\n", is_async=False, tools=True):
    os.environ.update(RAMP_ROUTER_API_KEY=SIM_KEY, RAMP_ROUTER_BASE_URL="https://router.mock")
    payload = sse(events, sep)

    def upstream(req):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(payload))
    rec = Recorder(upstream=upstream)
    kw = dict(model=model, messages=[{"role": "user", "content": "go"}], stream=True, stream_options={"include_usage": True})
    if stop:
        kw["stop"] = stop
    if tools:
        kw["tools"] = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}]
    if is_async:
        async def go():
            c = client(rec, is_async=True)
            return [ch async for ch in await c.chat.completions.create(**kw)]
        chunks = asyncio.run(go())
    else:
        chunks = list(client(rec).chat.completions.create(**kw))
    text, calls, finish, usage = "", {}, None, None
    for ch in chunks:
        if ch.usage:
            usage = ch.usage.total_tokens
        for c in ch.choices:
            text += c.delta.content or ""
            if c.finish_reason:
                finish = c.finish_reason
            for tc in c.delta.tool_calls or []:
                d = calls.setdefault(tc.index, {"id": None, "name": None, "args": ""})
                d["id"] = tc.id or d["id"]
                d["name"] = (tc.function.name if tc.function else None) or d["name"]
                d["args"] += (tc.function.arguments or "") if tc.function else ""
    return {"text": text, "calls": calls, "finish": finish, "usage": usage, "n_chunks": len(chunks)}


def base_events():
    return [{"type": "response.created", "response": resp_shell("in_progress", usage=False)}]


def part_b():
    def b1():
        ev = base_events() + [
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1", "role": "assistant", "content": []}},
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "Let me check."},
            {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_A", "name": "f", "arguments": ""}},
            {"type": "response.output_item.added", "output_index": 2, "item": {"type": "function_call", "id": "fc_2", "call_id": "call_B", "name": "f", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{\"x\":"},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_2", "delta": "{\"y\":2}"},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "1}"},
            {"type": "response.completed", "response": resp_shell()}]
        print("  ", stream_case(ev))
    show("B1 text + two interleaved tool calls + include_usage (expected: both calls intact, finish=tool_calls, usage=12)", b1)

    def b2():
        ev = base_events() + [
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_A", "name": "f", "arguments": ""}},
            {"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": "{\"x\":1}"},
            {"type": "response.output_item.done", "output_index": 0, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_A", "name": "f", "arguments": "{\"x\":1}"}},
            {"type": "response.completed", "response": resp_shell()}]
        print("  ", stream_case(ev))
    show("B2 tool-call arguments delivered only in *.done events (no deltas): args lost?", b2)

    def b3():
        ev = base_events() + [
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1", "role": "assistant", "content": []}},
            {"type": "response.refusal.delta", "item_id": "msg_1", "delta": "I can't help with that."},
            {"type": "response.refusal.done", "item_id": "msg_1", "refusal": "I can't help with that."},
            {"type": "response.completed", "response": resp_shell()}]
        print("  ", stream_case(ev, tools=False))
    show("B3 streamed refusal (non-stream path maps it to message.refusal)", b3)

    def b4():
        ev = base_events() + [
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "abcE"},
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "ND tail"},
            {"type": "response.completed", "response": resp_shell()}]
        print("  ", stream_case(ev, stop=["END"], tools=False))
    show("B4 stop string split across deltas (expected text 'abc', finish=stop)", b4)

    def b5():
        ev = base_events() + [
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "partial"},
            {"type": "response.incomplete", "response": resp_shell("incomplete", {"reason": "content_filter"})}]
        print("  ", stream_case(ev, tools=False))
    show("B5 incomplete/content_filter while streaming (non-stream path returns 'content_filter')", b5)

    def b6():
        ev = base_events() + [
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_A", "name": "f", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{\"x\": \"trunc"},
            {"type": "response.incomplete", "response": resp_shell("incomplete", {"reason": "max_output_tokens"})}]
        print("  ", stream_case(ev))
    show("B6 tool call truncated by max_output_tokens (Chat Completions would say finish_reason=length)", b6)

    def b7():
        ev = base_events() + [
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "hello"},
            {"type": "response.completed", "response": resp_shell()}]
        print("   LF framing:  ", stream_case(ev, tools=False))
        print("   CRLF framing:", stream_case(ev, tools=False, sep=b"\r\n\r\n"))
    show("B7 SSE framing with CRLF line endings (valid per the SSE spec)", b7)

    def b8():
        ev = base_events() + [
            {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "hello"},
            {"type": "response.completed", "response": resp_shell()}]
        payload_tail_missing = True  # last event not followed by a blank line (connection closed right after it)
        os.environ.update(RAMP_ROUTER_API_KEY=SIM_KEY, RAMP_ROUTER_BASE_URL="https://router.mock")
        body = sse(ev)[:-2]

        def upstream(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body))
        for is_async in (False, True):
            rec = Recorder(upstream=upstream)
            kw = dict(model="gpt-4o-mini", messages=[{"role": "user", "content": "go"}], stream=True)
            if is_async:
                async def go():
                    c = client(rec, is_async=True)
                    return [ch async for ch in await c.chat.completions.create(**kw)]
                chunks = asyncio.run(go())
            else:
                chunks = list(client(rec).chat.completions.create(**kw))
            fin = [c.finish_reason for ch in chunks for c in ch.choices if c.finish_reason]
            print("   %s: chunks=%d finish_reasons=%s" % ("async" if is_async else "sync ", len(chunks), fin))
        assert payload_tail_missing
    show("B8 final event without trailing blank line: sync vs async", b8)


if __name__ == "__main__":
    part_a()
    part_b()
