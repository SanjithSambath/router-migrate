"""Repro: chat_anthropic_kwargs() (used for LangChain ChatAnthropic) swaps base_url+api_key wholesale, bypassing the
runtime's per-request planning. A model Router does not serve (dated id) or an anthropic-beta request now fails at
Router instead of staying on Anthropic. Uses the anthropic SDK the same way ChatAnthropic does (base_url + api_key).
Run: .venv/bin/python research/review_scratch/chat_anthropic_case.py
"""
import importlib.util, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import anthropic, httpx
from router_migrate.catalog import Catalog
from router_migrate.runtime import render
from router_migrate.sim.server import serve

srv, sim, base = serve()
os.environ.update(RAMP_ROUTER_API_KEY="rm-review-sim-key", RAMP_ROUTER_BASE_URL=base, ANTHROPIC_API_KEY="sk-ant-test-not-real")
p = os.path.join(tempfile.mkdtemp(), "router_runtime.py"); open(p, "w").write(render("router_runtime.py", Catalog.load()))
spec = importlib.util.spec_from_file_location("rr", p); rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)

class ProviderStub(httpx.BaseTransport):
    def __init__(self): self.real = httpx.HTTPTransport()
    def handle_request(self, req):
        if req.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"id": "msg_p", "type": "message", "role": "assistant", "model": "provider", "content": [{"type": "text", "text": "FROM-ANTHROPIC"}],
                                             "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
        return self.real.handle_request(req)

model = "claude-3-5-sonnet-20240620"
print("chat_anthropic_kwargs() keys:", sorted(rt.chat_anthropic_kwargs()))
kw = rt.chat_anthropic_kwargs()
c = anthropic.Anthropic(base_url=kw["base_url"], api_key=kw["api_key"], max_retries=0)  # what ChatAnthropic(**kwargs, **_router_chat_anthropic_kwargs()) builds
try:
    c.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    print("ChatAnthropic-style key swap:", "ok")
except Exception as e:
    print("ChatAnthropic-style key swap -> %s %s" % (type(e).__name__, str(e)[:140]))
w = rt.wrap_anthropic(anthropic.Anthropic(http_client=httpx.Client(transport=ProviderStub()), max_retries=0))
out = w.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": "hi"}])
print("wrap_anthropic (transport) path ->", out.content[0].text, "(stays on Anthropic, as DECISIONS D2 promises)")
srv.shutdown()
