"""Compatibility smoke for the Python runtime under whatever openai/anthropic/httpx(2) versions the current interpreter has.
Run with another environment's python:  PYTHONPATH=<router-migrate> <env>/bin/python tests/compat_check.py"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from router_migrate.catalog import Catalog  # noqa: E402
from router_migrate.runtime import render  # noqa: E402
from router_migrate.sim.server import serve  # noqa: E402

srv, sim, base = serve()
os.environ.update(RAMP_ROUTER_API_KEY="rm-test-key-not-a-real-key", RAMP_ROUTER_BASE_URL=base, OPENAI_API_KEY="sk-provider", ANTHROPIC_API_KEY="sk-ant")
p = os.path.join(tempfile.mkdtemp(), "router_runtime.py")
open(p, "w").write(render("router_runtime.py", Catalog.load()))
spec = importlib.util.spec_from_file_location("rr", p)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
import openai  # noqa: E402

out = {"openai": openai.__version__, "lib": rt._sdk_lib("openai")}
c = rt.wrap_openai(openai.OpenAI())
out["wrapped"] = type(c._client._transport).__name__  # RouterTransport, installed in place under the SDK's own client
r = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
out["chat"] = r.choices[0].message.content
s = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True)
out["stream"] = "".join((ch.choices[0].delta.content or "") for ch in s if ch.choices)
t = c.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "w"}],
                              tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}])
out["tools"] = t.choices[0].message.tool_calls[0].function.name


async def go():
    ac = rt.wrap_openai(openai.AsyncOpenAI())
    a = await ac.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    st = await ac.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True)
    return a.choices[0].message.content, "".join([(ch.choices[0].delta.content or "") async for ch in st if ch.choices])


out["async"], out["async_stream"] = asyncio.run(go())
out["sim_valid"] = "%d/%d" % (sum(1 for x in sim.records if x["valid"]), len(sim.records))
try:
    import anthropic
    a = rt.wrap_anthropic(anthropic.Anthropic())
    out["anthropic"] = a.messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[{"role": "user", "content": "hi"}]).content[0].text
except ImportError:
    out["anthropic"] = "not installed"
try:
    from langchain_openai import ChatOpenAI
    out["langchain_openai"] = rt.wrap_langchain(ChatOpenAI(model="gpt-4o-mini")).invoke("hi").content
except ImportError:
    out["langchain_openai"] = "not installed"
srv.shutdown()
print(json.dumps(out))
ok = (out["chat"] == out["stream"] == out["async"] == out["async_stream"] == "pong" and out["tools"] == "f" and out["wrapped"] == "RouterTransport"
      and out["anthropic"] in ("pong", "not installed") and out["langchain_openai"] in ("pong", "not installed"))
sys.exit(0 if ok else 1)
