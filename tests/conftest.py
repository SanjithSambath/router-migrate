import importlib.util
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from router_migrate.catalog import Catalog  # noqa: E402
from router_migrate.runtime import render  # noqa: E402
from router_migrate.sim.server import serve  # noqa: E402


@pytest.fixture(scope="session")
def sim():
    srv, s, base = serve()
    s.base = base
    yield s
    srv.shutdown()


@pytest.fixture()
def rt(tmp_path, monkeypatch, sim):
    """A freshly rendered router_runtime module, activated against router-sim."""
    p = tmp_path / "router_runtime.py"
    p.write_text(render("router_runtime.py", Catalog.load()))
    monkeypatch.setenv("RAMP_ROUTER_API_KEY", "sk-routgw-test-not-a-real-key")
    monkeypatch.setenv("RAMP_ROUTER_BASE_URL", sim.base)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    spec = importlib.util.spec_from_file_location("router_runtime_under_test", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sim.records.clear()
    return mod


class Split(httpx.BaseTransport):
    """Inner transport for tests: router-sim traffic goes over the network, provider traffic is recorded and faked."""

    def __init__(self):
        self.real = httpx.HTTPTransport()
        self.provider_calls = []

    def handle_request(self, request):
        if request.url.host == "127.0.0.1":
            return self.real.handle_request(request)
        self.provider_calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                                             "model": "text-embedding-3-small", "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "msg_p", "type": "message", "role": "assistant", "model": "provider", "content": [{"type": "text", "text": "from-provider"}],
                                             "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
        return httpx.Response(200, json={"id": "chatcmpl-p", "object": "chat.completion", "created": 0, "model": "provider",
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": "from-provider"}, "finish_reason": "stop"}]})


class AsyncSplit(httpx.AsyncBaseTransport):
    def __init__(self):
        self.real = httpx.AsyncHTTPTransport()
        self.provider_calls = []

    async def handle_async_request(self, request):
        if request.url.host == "127.0.0.1":
            return await self.real.handle_async_request(request)
        self.provider_calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"id": "chatcmpl-p", "object": "chat.completion", "created": 0, "model": "provider",
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": "from-provider"}, "finish_reason": "stop"}]})
