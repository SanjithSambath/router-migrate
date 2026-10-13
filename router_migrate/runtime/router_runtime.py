# router_runtime.py (vendored by router-migrate; safe to read, edit or delete)
#
# Routes this codebase's OpenAI / Anthropic SDK traffic through Ramp's Router (https://router.com) when
# RAMP_ROUTER_API_KEY is set. When it is not set, every function here returns its input unchanged.
#
# How: an httpx transport placed under the SDK client. It sends requests Router can serve to Router and forwards
# everything else to the original provider untouched:
#   POST .../chat/completions  -> POST {router}/v1/responses   (request + response + SSE stream translated back)
#   POST .../responses         -> POST {router}/v1/responses   (store / previous_response_id handled, see below)
#   POST .../v1/messages       -> POST {router}/v1/messages    (Anthropic, model id mapped)
#   anything else (embeddings, images, audio, files, models, unknown models, n>1, logprobs) -> original provider
# Each Router request carries metadata.feature = "<file>:<function>" of the calling code.
# Env: RAMP_ROUTER_API_KEY (required to activate), RAMP_ROUTER_BASE_URL (default https://api.router.com),
#      ROUTER_MIGRATE_DISABLE=1 (kill switch), ROUTER_MIGRATE_MODEL_MAP='{"old-model":"router-id"}',
#      ROUTER_MIGRATE_SERVICE_TIER (default: "default" for streaming calls, account default otherwise).
import json
import os
import re
import sys
import time
import uuid

try:
    import httpx
except ImportError:  # pragma: no cover - the OpenAI and Anthropic SDKs both depend on httpx
    httpx = None

__all__ = ["active", "wrap_openai", "wrap_anthropic", "http_client", "async_http_client", "install_openai_module"]
VERSION = "0.1.0"

# --- catalog (generated from GET /v1/models by router-migrate; refresh with `router-migrate apply --live`) -------
# id -> [owned_by, temperature_supported, reasoning_supported]
CATALOG = {}  # filled below by _load_catalog()
ALIASES = {}
DATED = {}  # dated provider snapshot -> catalog id, only where Router itself reported the equivalence
_CATALOG_JSON = r"""__ROUTER_CATALOG__"""

_OPENAI_HOSTS = ("api.openai.com",)
_ANTHROPIC_HOSTS = ("api.anthropic.com",)
_SKIP_FRAMES = ("site-packages", "dist-packages", os.sep + "lib" + os.sep + "python", "router_runtime", "<frozen")


def _load_catalog():
    try:
        d = json.loads(_CATALOG_JSON)
    except ValueError:
        d = {}
    for mid, row in (d.get("models") or {}).items():
        CATALOG[mid] = row
    ALIASES.update(d.get("aliases") or {})
    DATED.update(d.get("dated") or {})


_load_catalog()


def active():
    return bool(os.environ.get("RAMP_ROUTER_API_KEY")) and os.environ.get("ROUTER_MIGRATE_DISABLE") not in ("1", "true", "yes")


def _base():
    b = (os.environ.get("RAMP_ROUTER_BASE_URL") or "https://api.router.com").rstrip("/")
    return b[:-3] if b.endswith("/v1") else b


def resolve_model(name):
    """Router id for a model name, or None when Router cannot serve that exact model (then the provider keeps it)."""
    if not isinstance(name, str) or not name:
        return None
    try:
        override = json.loads(os.environ.get("ROUTER_MIGRATE_MODEL_MAP") or "{}")
    except ValueError:
        override = {}
    if name in override:
        return override[name] or None
    if name in CATALOG:
        return name
    if name in ALIASES:
        return ALIASES[name]
    return DATED.get(name)


def _feature():
    """'<relative file>:<function>' of the first stack frame that belongs to this codebase."""
    f = sys._getframe(1)
    cwd = os.getcwd()
    while f is not None:
        fn = f.f_code.co_filename
        if not any(s in fn for s in _SKIP_FRAMES):
            rel = os.path.relpath(fn, cwd) if fn.startswith(cwd) else os.path.basename(fn)
            return ("%s:%s" % (rel, f.f_code.co_name))[:200]
        f = f.f_back
    return None


# ------------------------------------------------------------------------------------------------ translation
class Untranslatable(Exception):
    """Raised when a request uses something Router cannot serve; the request then goes to the original provider."""


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text"))
    return "" if content is None else str(content)


def _user_parts(content):
    if isinstance(content, str) or content is None:
        return content or ""
    parts = []
    for p in content:
        t = p.get("type") if isinstance(p, dict) else None
        if t == "text":
            parts.append({"type": "input_text", "text": p.get("text", "")})
        elif t == "image_url":
            iu = p.get("image_url")
            url = iu.get("url") if isinstance(iu, dict) else iu
            part = {"type": "input_image", "image_url": url}
            if isinstance(iu, dict) and iu.get("detail"):
                part["detail"] = iu["detail"]
            parts.append(part)
        elif t == "file":
            f = p.get("file") or {}
            part = {"type": "input_file"}
            for k in ("file_data", "file_id", "filename"):
                if f.get(k):
                    part[k] = f[k]
            parts.append(part)
        else:  # input_audio and anything newer: Router cannot take it
            raise Untranslatable("content part %r" % t)
    return parts


def chat_to_responses(body, router_model, owned_by, feature):
    """Chat Completions request -> Router Responses request. Returns (new_body, context for the reply)."""
    unsupported = [k for k in ("audio", "modalities", "prediction", "web_search_options", "logit_bias", "functions_call_mode") if body.get(k)]
    if (body.get("n") or 1) != 1:
        unsupported.append("n")
    if body.get("logprobs") or body.get("top_logprobs"):
        unsupported.append("logprobs")
    if unsupported:
        raise Untranslatable(",".join(unsupported))
    info = CATALOG.get(router_model) or {}
    items = []
    call_ids = {}
    for i, m in enumerate(body.get("messages") or []):
        role = m.get("role")
        if role in ("system", "developer"):
            items.append({"role": role, "content": _text_of(m.get("content"))})
        elif role == "user":
            items.append({"role": "user", "content": _user_parts(m.get("content"))})
        elif role == "assistant":
            text = _text_of(m.get("content"))
            if text:
                items.append({"role": "assistant", "content": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append({"type": "function_call", "call_id": tc.get("id"), "name": fn.get("name"),
                              "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str) else json.dumps(fn.get("arguments") or {})})
            fc = m.get("function_call")  # legacy functions API
            if fc:
                cid = "call_legacy_%d" % i
                call_ids[fc.get("name")] = cid
                items.append({"type": "function_call", "call_id": cid, "name": fc.get("name"), "arguments": fc.get("arguments") or "{}"})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"), "output": _text_of(m.get("content"))})
        elif role == "function":
            items.append({"type": "function_call_output", "call_id": call_ids.get(m.get("name"), "call_legacy"), "output": _text_of(m.get("content"))})
        else:
            raise Untranslatable("role %r" % role)
    out = {"model": router_model, "input": items}
    mot = body.get("max_completion_tokens") or body.get("max_tokens")
    if mot is not None:
        out["max_output_tokens"] = max(16, int(mot))  # Responses minimum is 16
    temp_ok = info.get("t") is not False
    for k in ("temperature", "top_p"):
        if body.get(k) is not None and temp_ok:
            out[k] = body[k]
    for k in ("frequency_penalty", "presence_penalty", "parallel_tool_calls", "user", "service_tier", "prompt_cache_key", "safety_identifier"):
        if body.get(k) is not None:
            out[k] = body[k]
    tools = []
    for t in body.get("tools") or []:
        if t.get("type") == "function":
            fn = t.get("function") or {}
            nt = {"type": "function", "name": fn.get("name"), "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}
            if fn.get("description"):
                nt["description"] = fn["description"]
            nt["strict"] = bool(fn.get("strict"))  # Responses defaults tools to strict; Chat Completions does not
            tools.append(nt)
        else:
            raise Untranslatable("tool type %r" % t.get("type"))
    for fn in body.get("functions") or []:  # legacy functions API
        tools.append({"type": "function", "name": fn.get("name"), "description": fn.get("description", ""),
                      "parameters": fn.get("parameters") or {"type": "object", "properties": {}}, "strict": False})
    if tools:
        out["tools"] = tools
    tc = body.get("tool_choice", body.get("function_call"))
    if isinstance(tc, dict):
        name = (tc.get("function") or {}).get("name") or tc.get("name")
        out["tool_choice"] = {"type": "function", "name": name}
    elif tc in ("auto", "none", "required"):
        out["tool_choice"] = tc
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema"):
        if rf["type"] == "json_object":
            fmt = {"type": "json_object"}
        else:
            js = rf.get("json_schema") or {}
            fmt = {"type": "json_schema", "name": js.get("name") or "response", "schema": js.get("schema") or {}}
            for k in ("strict", "description"):
                if js.get(k) is not None:
                    fmt[k] = js[k]
        out["text"] = {"format": fmt}
    if body.get("verbosity"):
        out.setdefault("text", {})["verbosity"] = body["verbosity"]
    effort = body.get("reasoning_effort")
    if effort and info.get("r"):
        if not (owned_by == "anthropic" and out.get("max_output_tokens", 4096) < 1100):  # Claude needs room for thinking [measured]
            out["reasoning"] = {"effort": effort}
    stops = body.get("stop")
    stops = [stops] if isinstance(stops, str) else [s for s in (stops or []) if s]
    if stops and owned_by != "openai":
        out["stop"] = stops  # honored by non-OpenAI models [measured]; OpenAI models 501, so emulated client-side below
    meta = dict(body.get("metadata") or {})
    if feature and "feature" not in meta:
        meta["feature"] = feature
    if meta:
        out["metadata"] = {k: str(v)[:512] for k, v in meta.items()}
    if owned_by == "anthropic" and "prompt_cache_key" not in out and feature:
        out["prompt_cache_key"] = feature  # Claude via Responses does not cache without it [measured]
    if body.get("stream"):
        out["stream"] = True
        out.setdefault("service_tier", os.environ.get("ROUTER_MIGRATE_SERVICE_TIER") or "default")
    elif os.environ.get("ROUTER_MIGRATE_SERVICE_TIER"):
        out.setdefault("service_tier", os.environ["ROUTER_MIGRATE_SERVICE_TIER"])
    ctx = {"stops": stops if owned_by == "openai" else [], "include_usage": bool((body.get("stream_options") or {}).get("include_usage")),
           "model": body.get("model"), "legacy_functions": bool(body.get("functions"))}
    return out, ctx


def _usage(u):
    if not u:
        return None
    d = {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
         "total_tokens": u.get("total_tokens", (u.get("input_tokens", 0) + u.get("output_tokens", 0))),
         "prompt_tokens_details": {"cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0)},
         "completion_tokens_details": {"reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}}
    if "cost" in u:
        d["cost"] = u["cost"]
    return d


def _cut(text, stops):
    hit = None
    for s in stops:
        k = text.find(s)
        if k >= 0 and (hit is None or k < hit):
            hit = k
    return (text, False) if hit is None else (text[:hit], True)


def responses_to_chat(resp, ctx):
    text, refusal, calls = "", None, []
    for it in resp.get("output") or []:
        if it.get("type") == "message":
            for c in it.get("content") or []:
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                elif c.get("type") == "refusal":
                    refusal = (refusal or "") + c.get("refusal", "")
        elif it.get("type") == "function_call":
            calls.append({"id": it.get("call_id"), "type": "function", "function": {"name": it.get("name"), "arguments": it.get("arguments") or ""}})
    text, stopped = _cut(text, ctx.get("stops") or [])
    inc = (resp.get("incomplete_details") or {}).get("reason")
    finish = "tool_calls" if calls else ("length" if inc == "max_output_tokens" and not stopped else "content_filter" if inc == "content_filter" else "stop")
    msg = {"role": "assistant", "content": text if (text or not calls) else None, "refusal": refusal}
    if calls:
        if ctx.get("legacy_functions"):
            msg["function_call"] = calls[0]["function"]
            finish = "function_call"
        else:
            msg["tool_calls"] = calls
    out = {"id": "chatcmpl-" + str(resp.get("id", uuid.uuid4().hex)), "object": "chat.completion",
           "created": int(resp.get("created_at") or time.time()), "model": resp.get("model") or ctx.get("model"),
           "system_fingerprint": None, "service_tier": resp.get("service_tier"),
           "choices": [{"index": 0, "message": msg, "finish_reason": finish, "logprobs": None}]}
    u = _usage(resp.get("usage"))
    if u:
        out["usage"] = u
    if resp.get("provider"):
        out["provider"] = resp["provider"]
    return out


class _ChunkWriter:
    """Turns Router Responses SSE events into chat.completion.chunk SSE lines."""

    def __init__(self, ctx):
        self.ctx, self.buf, self.done, self.id, self.created, self.model = ctx, b"", False, None, int(time.time()), ctx.get("model")
        self.tool_index, self.text_tail, self.emitted_role, self.stopped, self.saw_call = {}, "", False, False, False
        self.maxstop = max([len(s) for s in ctx.get("stops") or []] or [0])

    def _chunk(self, delta, finish=None, usage=None, choices=True):
        d = {"id": "chatcmpl-" + str(self.id or "router"), "object": "chat.completion.chunk", "created": self.created, "model": self.model,
             "system_fingerprint": None, "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}] if choices else []}
        if usage is not None:
            d["usage"] = usage
        return ("data: " + json.dumps(d) + "\n\n").encode()

    def _text(self, s, final=False):
        if self.stopped:
            return []
        if not self.maxstop:
            return [self._chunk({"content": s})] if s else []
        self.text_tail += s
        cut, hit = _cut(self.text_tail, self.ctx["stops"])
        if hit:
            self.stopped = True
            return [self._chunk({"content": cut})] if cut else []
        keep = 0 if final else self.maxstop - 1
        emit, self.text_tail = (self.text_tail[:len(self.text_tail) - keep], self.text_tail[len(self.text_tail) - keep:]) if keep else (self.text_tail, "")
        return [self._chunk({"content": emit})] if emit else []

    def feed(self, data):
        self.buf += data
        out = []
        while b"\n\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n\n", 1)
            out.extend(self._event(raw))
        return out

    def _event(self, raw):
        lines = raw.decode("utf-8", "replace").splitlines()
        data = "".join(l[5:].lstrip() for l in lines if l.startswith("data:"))
        if not data or data == "[DONE]":
            return []
        try:
            ev = json.loads(data)
        except ValueError:
            return []
        t = ev.get("type", "")
        out = []
        if not self.emitted_role and t in ("response.created", "response.in_progress", "response.output_item.added", "response.output_text.delta"):
            r = ev.get("response") or {}
            self.id = r.get("id") or self.id
            self.model = r.get("model") or self.model
            self.emitted_role = True
            out.append(self._chunk({"role": "assistant", "content": ""}))
        if t == "response.output_text.delta":
            out.extend(self._text(ev.get("delta", "")))
        elif t == "response.output_item.added" and (ev.get("item") or {}).get("type") == "function_call":
            it = ev["item"]
            idx = len(self.tool_index)
            self.tool_index[it.get("id")] = idx
            self.saw_call = True
            out.append(self._chunk({"tool_calls": [{"index": idx, "id": it.get("call_id"), "type": "function",
                                                    "function": {"name": it.get("name"), "arguments": ""}}]}))
        elif t == "response.function_call_arguments.delta":
            idx = self.tool_index.get(ev.get("item_id"), 0)
            out.append(self._chunk({"tool_calls": [{"index": idx, "function": {"arguments": ev.get("delta", "")}}]}))
        elif t in ("response.completed", "response.incomplete"):
            out.extend(self._text("", final=True))
            r = ev.get("response") or {}
            inc = (r.get("incomplete_details") or {}).get("reason")
            finish = "tool_calls" if self.saw_call else ("length" if inc == "max_output_tokens" and not self.stopped else "stop")
            out.append(self._chunk({}, finish=finish))
            if self.ctx.get("include_usage"):
                out.append(self._chunk({}, usage=_usage(r.get("usage")), choices=False))
            out.append(b"data: [DONE]\n\n")
            self.done = True
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or {"message": ev.get("message", "Router stream error")}
            out.append(("data: " + json.dumps({"error": err}) + "\n\n").encode())
            out.append(b"data: [DONE]\n\n")
            self.done = True
        return out


# ------------------------------------------------------------------------------------------------ transports
def _router_headers(req, anthropic=False):
    h = {"Authorization": "Bearer " + os.environ["RAMP_ROUTER_API_KEY"], "Content-Type": "application/json",
         "User-Agent": req.headers.get("User-Agent") or ("router-migrate-runtime/" + VERSION), "Accept": req.headers.get("Accept", "application/json")}
    if anthropic:
        h["anthropic-version"] = req.headers.get("anthropic-version", "2023-06-01")
        if req.headers.get("anthropic-beta"):
            h["anthropic-beta"] = req.headers["anthropic-beta"]
    return h


def plan_request(req):
    """Decide where a provider-bound request goes. Returns None (send to provider unchanged) or a routing tuple."""
    if not active() or req.method != "POST":
        return None
    host, path = req.url.host, req.url.path
    if host in _OPENAI_HOSTS and (path.endswith("/chat/completions") or path.endswith("/responses")):
        try:
            body = json.loads(req.content or b"{}")
        except ValueError:
            return None
        rid = resolve_model(body.get("model"))
        if not rid:
            return None
        owned = (CATALOG.get(rid) or {}).get("o")
        feature = _feature()
        if path.endswith("/chat/completions"):
            try:
                nb, ctx = chat_to_responses(body, rid, owned, feature)
            except Untranslatable:
                return None
            return ("chat", nb, ctx)
        if body.get("previous_response_id") or body.get("conversation") or body.get("background"):
            return None  # needs server-side state Router does not keep [measured]; provider keeps this call
        nb = dict(body, model=rid)
        nb.pop("store", None)
        meta = dict(nb.get("metadata") or {})
        if feature and "feature" not in meta:
            meta["feature"] = feature
            nb["metadata"] = meta
        if owned == "anthropic" and "prompt_cache_key" not in nb and feature:
            nb["prompt_cache_key"] = feature
        return ("responses", nb, {})
    if host in _ANTHROPIC_HOSTS and path.rstrip("/").endswith("/v1/messages") or (host in _ANTHROPIC_HOSTS and path.endswith("/v1/messages/count_tokens")):
        if req.headers.get("anthropic-beta"):
            return None  # beta features are unmeasured on Router; keep on Anthropic
        try:
            body = json.loads(req.content or b"{}")
        except ValueError:
            return None
        rid = resolve_model(body.get("model"))
        if not rid:
            return None
        nb = dict(body, model=rid)
        return ("messages", nb, {"count": path.endswith("count_tokens")})
    return None


def _router_request(req, route):
    kind, nb, ctx = route
    url = _base() + ("/v1/messages/count_tokens" if kind == "messages" and ctx.get("count") else "/v1/messages" if kind == "messages" else "/v1/responses")
    return httpx.Request("POST", url, headers=_router_headers(req, anthropic=(kind == "messages")), content=json.dumps(nb).encode(),
                         extensions=req.extensions)


def _translate_json(resp_bytes, status, ctx):
    if status >= 400:
        return resp_bytes
    try:
        return json.dumps(responses_to_chat(json.loads(resp_bytes), ctx)).encode()
    except ValueError:
        return resp_bytes


if httpx is not None:
    class _SyncChatStream(httpx.SyncByteStream):
        def __init__(self, upstream, ctx):
            self.up, self.w = upstream, _ChunkWriter(ctx)

        def __iter__(self):
            try:
                for data in self.up.iter_raw():
                    for out in self.w.feed(data):
                        yield out
                    if self.w.done:
                        break
                if not self.w.done:
                    for out in self.w.feed(b"\n\n"):
                        yield out
                    if not self.w.done:
                        yield b"data: [DONE]\n\n"
            finally:
                self.up.close()

        def close(self):
            self.up.close()

    class _AsyncChatStream(httpx.AsyncByteStream):
        def __init__(self, upstream, ctx):
            self.up, self.w = upstream, _ChunkWriter(ctx)

        async def __aiter__(self):
            try:
                async for data in self.up.aiter_raw():
                    for out in self.w.feed(data):
                        yield out
                    if self.w.done:
                        break
                if not self.w.done:
                    yield b"data: [DONE]\n\n"
            finally:
                await self.up.aclose()

        async def aclose(self):
            await self.up.aclose()

    class RouterTransport(httpx.BaseTransport):
        """Sync httpx transport: Router for what it can serve, the wrapped transport (provider) for the rest."""

        def __init__(self, inner=None):
            self.inner = inner or httpx.HTTPTransport()

        def handle_request(self, request):
            route = plan_request(request)
            if route is None:
                return self.inner.handle_request(request)
            up = self.inner.handle_request(_router_request(request, route))
            kind, nb, ctx = route
            if kind != "chat":
                return up
            if nb.get("stream") and up.status_code < 400:
                return httpx.Response(up.status_code, headers={"content-type": "text/event-stream", "x-router-provider": up.headers.get("x-router-provider", "")},
                                      stream=_SyncChatStream(up, ctx), extensions=up.extensions)
            body = up.read()
            up.close()
            ctype = up.headers.get("content-type", "application/json")
            return httpx.Response(up.status_code, headers={"content-type": ctype, "x-request-id": up.headers.get("x-request-id", ""),
                                                           "x-router-provider": up.headers.get("x-router-provider", "")},
                                  content=_translate_json(body, up.status_code, ctx))

        def close(self):
            self.inner.close()

    class AsyncRouterTransport(httpx.AsyncBaseTransport):
        def __init__(self, inner=None):
            self.inner = inner or httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            route = plan_request(request)
            if route is None:
                return await self.inner.handle_async_request(request)
            up = await self.inner.handle_async_request(_router_request(request, route))
            kind, nb, ctx = route
            if kind != "chat":
                return up
            if nb.get("stream") and up.status_code < 400:
                return httpx.Response(up.status_code, headers={"content-type": "text/event-stream", "x-router-provider": up.headers.get("x-router-provider", "")},
                                      stream=_AsyncChatStream(up, ctx), extensions=up.extensions)
            body = await up.aread()
            await up.aclose()
            ctype = up.headers.get("content-type", "application/json")
            return httpx.Response(up.status_code, headers={"content-type": ctype, "x-request-id": up.headers.get("x-request-id", ""),
                                                           "x-router-provider": up.headers.get("x-router-provider", "")},
                                  content=_translate_json(body, up.status_code, ctx))

        async def aclose(self):
            await self.inner.aclose()


# ------------------------------------------------------------------------------------------------ public helpers
def http_client(**kwargs):
    """httpx.Client that routes through Router when active (pass as http_client= to OpenAI/Anthropic/LangChain...)."""
    if not active():
        return kwargs.pop("_default", None)
    try:
        from openai import DefaultHttpxClient as _C  # keeps the SDK's default timeouts/limits
    except Exception:  # openai < 1.17 has no DefaultHttpxClient: match its defaults by hand
        kwargs.setdefault("timeout", httpx.Timeout(600.0, connect=5.0))
        kwargs.setdefault("follow_redirects", True)
        _C = httpx.Client
    return _C(transport=RouterTransport(), **kwargs)


def async_http_client(**kwargs):
    if not active():
        return kwargs.pop("_default", None)
    try:
        from openai import DefaultAsyncHttpxClient as _C
    except Exception:
        kwargs.setdefault("timeout", httpx.Timeout(600.0, connect=5.0))
        kwargs.setdefault("follow_redirects", True)
        _C = httpx.AsyncClient
    return _C(transport=AsyncRouterTransport(), **kwargs)


def _with_client(client, is_async):
    hc = async_http_client() if is_async else http_client()
    try:
        return client.with_options(http_client=hc)
    except Exception:
        return client  # unknown client shape (e.g. a test double): leave it exactly as it was


def wrap_openai(client):
    """OpenAI / AsyncOpenAI client -> same client routed through Router (unchanged when RAMP_ROUTER_API_KEY is unset)."""
    if not active() or httpx is None:
        return client
    name = type(client).__name__
    if name not in ("OpenAI", "AsyncOpenAI"):
        return client  # Azure clients and test doubles stay on their configured endpoint
    return _with_client(client, name.startswith("Async"))


def wrap_anthropic(client):
    if not active() or httpx is None:
        return client
    name = type(client).__name__
    if name not in ("Anthropic", "AsyncAnthropic"):
        return client  # Bedrock / Vertex clients stay put
    return _with_client(client, name.startswith("Async"))


def install_openai_module():
    """For code that calls the module-level client (openai.chat.completions.create)."""
    if not active() or httpx is None:
        return
    try:
        import openai
        if getattr(openai, "http_client", None) is None:
            openai.http_client = http_client()
    except Exception:
        pass


if active():  # SDK constructors require a provider key even when Router serves the request
    os.environ.setdefault("OPENAI_API_KEY", "unused-router-serves-this")


def chat_anthropic_kwargs():
    """Extra kwargs for LangChain ChatAnthropic (it has no http_client hook; a base_url + key swap works [measured])."""
    if not active():
        return {}
    return {"base_url": _base(), "api_key": os.environ["RAMP_ROUTER_API_KEY"]}


def install_litellm():
    """Route LiteLLM's OpenAI-provider calls through the transport (LiteLLM builds OpenAI SDK clients on these sessions)."""
    if not active() or httpx is None:
        return
    try:
        import litellm
        if getattr(litellm, "client_session", None) is None:
            litellm.client_session = http_client()
        if getattr(litellm, "aclient_session", None) is None:
            litellm.aclient_session = async_http_client()
    except Exception:
        pass
