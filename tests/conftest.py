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


RENDERED = os.path.join(ROOT, "tests", "_rendered")


@pytest.fixture()
def rt(tmp_path, monkeypatch, sim):
    """A freshly rendered router_runtime module (fixed path so coverage can attribute it), activated against router-sim."""
    os.makedirs(RENDERED, exist_ok=True)
    p = os.path.join(RENDERED, "router_runtime.py")
    tmp = "%s.%d.tmp" % (p, os.getpid())
    open(tmp, "w").write(render("router_runtime.py", Catalog.load()))
    os.replace(tmp, p)  # atomic: concurrent test runs never see a half-written file
    monkeypatch.setenv("RAMP_ROUTER_API_KEY", "rm-test-key-not-a-real-key")
    monkeypatch.setenv("RAMP_ROUTER_BASE_URL", sim.base)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    spec = importlib.util.spec_from_file_location("router_runtime_under_test", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sim.records.clear()
    return mod


PROVIDER_RESPONSE = {"id": "resp_provider", "object": "response", "created_at": 0, "model": "provider", "status": "completed",
                     "output": [{"type": "message", "id": "msg_p", "role": "assistant", "status": "completed",
                                 "content": [{"type": "output_text", "text": "from-provider", "annotations": []}]}],
                     "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}


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
        if "/responses" in request.url.path:
            return httpx.Response(200, json=PROVIDER_RESPONSE)
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


def accept_proposals(repo, rm):
    """Accept every proposal's suggested edit, as an agent agreeing with the rules would (v3 applied these up front)."""
    import json as _json
    done = set()
    while True:
        L = _json.load(open(os.path.join(str(repo), ".router-migrate", "ledger.json")))
        todo = [p for p in L.get("proposals") or [] if (p.get("edit") or {}).get("at") and p["id"] not in done]
        if not todo:
            return
        done.add(todo[0]["id"])
        rm("wrap", todo[0]["edit"]["at"], cwd=repo)
