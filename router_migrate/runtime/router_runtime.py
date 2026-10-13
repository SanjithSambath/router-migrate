# router_runtime.py (vendored by router-migrate; safe to read, edit or delete)
#
# Routes this codebase's OpenAI / Anthropic SDK traffic through Ramp's Router (https://router.com) when
# RAMP_ROUTER_API_KEY is set. When it is not set, every function here returns its input unchanged.
#
# How: an httpx transport placed under the SDK client (installed in place, so the client's proxies, mounts, headers and
# hooks all stay). It sends requests Router can serve to Router and forwards everything else to the original provider
# untouched:
#   POST .../chat/completions  -> POST {router}/v1/responses   (request + response + SSE stream translated back)
#   POST .../responses         -> POST {router}/v1/responses   (store dropped; previous_response_id chains kept in-process)
#   POST .../v1/messages       -> POST {router}/v1/messages    (Anthropic, model id mapped)
#   anything else (embeddings, images, audio, files, models, unknown models, n>1, logprobs, seed, beta headers,
#   provider-side vector stores/files) -> original provider
# If Router rejects a request before generating (unknown/unsupported parameter, model not available to this key, 501),
# the original request is sent to the provider instead, once.
# Each Router request carries metadata.feature = "<file>:<function>" of the calling code (ROUTER_MIGRATE_FEATURE_TAG=off
# disables it; it sends a repo-relative path and function name).
# Env: RAMP_ROUTER_API_KEY (required to activate), RAMP_ROUTER_BASE_URL (default https://api.router.com),
#      ROUTER_MIGRATE_DISABLE=1 (kill switch), ROUTER_MIGRATE_MODEL_MAP='{"old-model":"router-id"}',
#      ROUTER_MIGRATE_SERVICE_TIER (default "default"; "auto" leaves the account default, which may be flex),
#      ROUTER_MIGRATE_FALLBACK=0 (surface Router errors instead of retrying on the provider).
# The provider key (OPENAI_API_KEY / ANTHROPIC_API_KEY) must stay configured: SDK constructors require it, and requests
# Router cannot serve still go to the provider.
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict

import importlib

# openai-python <= 2.x and anthropic use `httpx`; openai-python >= 3 uses `httpx2` (same API). The transport classes are built
# for whichever library each client actually uses.
_LIBS = {}


def _lib(name):
    if name not in _LIBS:
        try:
            _LIBS[name] = importlib.import_module(name)
        except ImportError:
            _LIBS[name] = None
    return _LIBS[name]


httpx = _lib("httpx") or _lib("httpx2")

__all__ = ["active", "wrap_openai", "wrap_anthropic", "wrap_langchain", "http_client", "async_http_client", "install_openai_module"]
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
    off = (os.environ.get("ROUTER_MIGRATE_DISABLE") or "").strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("RAMP_ROUTER_API_KEY")) and not off


def _feature_tags_on():
    return (os.environ.get("ROUTER_MIGRATE_FEATURE_TAG") or "").strip().lower() not in ("0", "off", "false", "no")


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
    """'<relative file>:<function>' of the first stack frame that belongs to this codebase (ROUTER_MIGRATE_FEATURE_TAG=off disables)."""
    if not _feature_tags_on():
        return None
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
            if f.get("file_id"):
                raise Untranslatable("file_id")  # files live on the provider account; Router cannot see them
            for k in ("file_data", "filename"):
                if f.get(k):
                    part[k] = f[k]
            parts.append(part)
        else:  # input_audio and anything newer: Router cannot take it
            raise Untranslatable("content part %r" % t)
    return parts


def chat_to_responses(body, router_model, owned_by, feature):
    """Chat Completions request -> Router Responses request. Returns (new_body, context for the reply)."""
    unsupported = [k for k in ("audio", "modalities", "prediction", "web_search_options", "logit_bias", "functions_call_mode") if body.get(k)]
    # not measured as accepted by Router, or no exact Responses equivalent: keep these calls on the provider unchanged
    unsupported += ["seed"] if body.get("seed") is not None else []
    unsupported += ["presence_penalty"] if body.get("presence_penalty") not in (None, 0) else []  # 0 is the default: dropped
    mot = body.get("max_completion_tokens") or body.get("max_tokens")
    if mot is not None and int(mot) < 16:
        unsupported.append("max_tokens<16")  # Responses minimum is 16; clamping would change output length
    if any(isinstance(m, dict) and m.get("name") and m.get("role") != "function" for m in body.get("messages") or []):
        unsupported.append("message name")
    tc0 = body.get("tool_choice")
    if isinstance(tc0, dict) and tc0.get("type") not in (None, "function"):
        unsupported.append("tool_choice %s" % tc0.get("type"))
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
            refusal = m.get("refusal") or "".join(p.get("refusal", "") for p in (m.get("content") if isinstance(m.get("content"), list) else [])
                                                  if isinstance(p, dict) and p.get("type") == "refusal")
            if refusal:
                parts = ([{"type": "output_text", "text": text}] if text else []) + [{"type": "refusal", "refusal": refusal}]
                items.append({"role": "assistant", "content": parts})
            elif text:
                items.append({"role": "assistant", "content": text})
            for tc in m.get("tool_calls") or []:
                if tc.get("type", "function") != "function":
                    raise Untranslatable("assistant tool_call type %r" % tc.get("type"))
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
    if mot is not None:
        out["max_output_tokens"] = int(mot)
    temp_ok = info.get("t") is not False
    for k in ("temperature", "top_p"):
        if body.get(k) is not None and temp_ok:
            out[k] = body[k]
    for k in ("frequency_penalty", "parallel_tool_calls", "user", "service_tier", "prompt_cache_key", "safety_identifier"):
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
    if feature and "feature" not in meta and len(meta) < 16:  # metadata is capped at 16 keys
        meta["feature"] = feature
    if meta:
        out["metadata"] = {k: str(v)[:512] for k, v in meta.items()}
    if owned_by == "anthropic" and "prompt_cache_key" not in out and feature:
        out["prompt_cache_key"] = feature  # Claude via Responses does not cache without it [measured]
    if body.get("stream"):
        out["stream"] = True
    _service_tier(out)
    ctx = {"stops": stops if owned_by == "openai" else [], "include_usage": bool((body.get("stream_options") or {}).get("include_usage")),
           "model": body.get("model"), "legacy_functions": bool(body.get("functions"))}
    return out, ctx


def _service_tier(out):
    """Default tier unless the caller chose one: the account-level flex default adds latency the original call never had [measured].
    ROUTER_MIGRATE_SERVICE_TIER overrides; 'auto' leaves it to the account default."""
    tier = (os.environ.get("ROUTER_MIGRATE_SERVICE_TIER") or "default").strip()
    if "service_tier" not in out and tier != "auto":
        out["service_tier"] = tier


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
    finish = ("content_filter" if inc == "content_filter" else "length" if inc == "max_output_tokens" and not stopped
              else "tool_calls" if calls else "stop")
    msg = {"role": "assistant", "content": text if (text or not calls) else None, "refusal": refusal}
    if calls:
        if ctx.get("legacy_functions"):
            msg["function_call"] = calls[0]["function"]
            finish = "function_call" if finish == "tool_calls" else finish
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


class _SSE:
    """Incremental SSE parser: bytes in, decoded JSON events out. Handles \r\n framing and events split across reads."""

    def __init__(self):
        self.buf = b""

    def feed(self, data, final=False):
        self.buf = (self.buf + data).replace(b"\r\n", b"\n")
        if final and self.buf.strip():
            self.buf += b"\n\n"
        out = []
        while b"\n\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n\n", 1)
            lines = raw.decode("utf-8", "replace").split("\n")
            data_ = "\n".join(l[5:].lstrip() if l.startswith("data:") else "" for l in lines if l.startswith("data:"))
            if not data_ or data_ == "[DONE]":
                continue
            try:
                ev = json.loads(data_)
            except ValueError:
                continue
            if isinstance(ev, dict):
                ev.setdefault("type", next((l[6:].strip() for l in lines if l.startswith("event:")), ""))
                out.append(ev)
        return out


class _PassThrough:
    """Stream writer that forwards bytes unchanged and lets an observer see the events."""

    def __init__(self, observer):
        self.obs, self.done = observer, False

    def feed(self, data):
        try:
            self.obs.feed(data)
        except Exception:
            pass
        return [data]

    def finish(self):
        return []


class _ChunkWriter:
    """Turns Router Responses SSE events into chat.completion.chunk SSE lines."""

    def __init__(self, ctx):
        self.ctx, self.sse, self.done, self.id, self.created, self.model = ctx, _SSE(), False, None, int(time.time()), ctx.get("model")
        self.tool_index, self.args_sent, self.text_tail, self.emitted_role, self.stopped = {}, {}, "", False, False
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
        out = []
        for ev in self.sse.feed(data):
            out.extend(self._event(ev))
        return out

    def finish(self):
        """End of the upstream body: flush a final event without a trailing blank line, then close the stream properly."""
        out = [] if self.done else [x for ev in self.sse.feed(b"", final=True) for x in self._event(ev)]
        if not self.done:
            out.extend(self._text("", final=True))
            out.append(b"data: [DONE]\n\n")
            self.done = True
        return out

    def _tool(self, item, out):
        key = item.get("id") or item.get("call_id")
        if key not in self.tool_index:
            self.tool_index[key] = len(self.tool_index)
            self.args_sent[key] = ""
            out.append(self._call_delta(key, {"name": item.get("name"), "arguments": ""}, item.get("call_id")))
        return key

    def _call_delta(self, key, fn, call_id=None):
        if self.ctx.get("legacy_functions"):
            return self._chunk({"function_call": fn})
        tc = {"index": self.tool_index[key], "function": fn}
        if call_id is not None:
            tc.update(id=call_id, type="function")
        return self._chunk({"tool_calls": [tc]})

    def _args(self, key, full, out):
        """Emit whatever part of the final arguments was not streamed as deltas (some providers only send .done)."""
        sent = self.args_sent.get(key, "")
        if full and full.startswith(sent) and len(full) > len(sent):
            out.append(self._call_delta(key, {"arguments": full[len(sent):]}))
            self.args_sent[key] = full

    def _event(self, ev):
        if self.done:
            return []
        t = ev.get("type", "")
        out = []
        if not self.emitted_role and t.startswith("response."):
            r = ev.get("response") or {}
            self.id = r.get("id") or self.id
            self.model = r.get("model") or self.model
            self.emitted_role = True
            out.append(self._chunk({"role": "assistant", "content": ""}))
        if t == "response.output_text.delta":
            out.extend(self._text(ev.get("delta", "")))
        elif t == "response.refusal.delta":
            out.append(self._chunk({"refusal": ev.get("delta", "")}))
        elif t == "response.output_item.added" and (ev.get("item") or {}).get("type") == "function_call":
            self._tool(ev["item"], out)
        elif t == "response.function_call_arguments.delta":
            key = ev.get("item_id")
            if key not in self.tool_index:
                key = self._tool({"id": key, "call_id": ev.get("call_id"), "name": ev.get("name")}, out)
            self.args_sent[key] = self.args_sent.get(key, "") + ev.get("delta", "")
            out.append(self._call_delta(key, {"arguments": ev.get("delta", "")}))
        elif t == "response.function_call_arguments.done":
            key = ev.get("item_id")
            if key in self.tool_index:
                self._args(key, ev.get("arguments") or "", out)
        elif t == "response.output_item.done" and (ev.get("item") or {}).get("type") == "function_call":
            key = self._tool(ev["item"], out)
            self._args(key, ev["item"].get("arguments") or "", out)
        elif t in ("response.completed", "response.incomplete"):
            out.extend(self._text("", final=True))
            r = ev.get("response") or {}
            inc = (r.get("incomplete_details") or {}).get("reason")
            if inc == "content_filter":
                finish = "content_filter"
            elif inc == "max_output_tokens" and not self.stopped:
                finish = "length"  # also for a tool call cut off mid-arguments, as Chat Completions reports it
            elif self.tool_index:
                finish = "function_call" if self.ctx.get("legacy_functions") else "tool_calls"
            else:
                finish = "stop"
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


# ------------------------------------------------------------------------------------------------ Responses state
# Router keeps no server-side state (store:true 400, previous_response_id 400 or ignored [measured]). So a chain started on
# Router is continued in this process: the runtime remembers each Router response's input + output and sends the full
# history inline on the next turn, which is what Router's own error message asks callers to do [measured].
_CHAIN_MAX, _IDS_MAX = 256, 20000
_CHAINS = OrderedDict()  # Router response id -> (input items, output items, full response json)
_ROUTER_IDS = OrderedDict()  # every Router-issued response id seen (outlives _CHAINS), so it is never sent to the provider
_DENIED = set()  # (surface, model) Router refused for this key (403/404) in this process; they go straight to the provider


def _lru_put(d, k, v, cap):
    d[k] = v
    d.move_to_end(k)
    while len(d) > cap:
        d.popitem(last=False)


def _as_items(inp):
    if inp is None:
        return []
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    return list(inp)


def _remember(ctx, resp):
    rid = resp.get("id") if isinstance(resp, dict) else None
    if not rid or ctx.get("items") is None:
        return
    _lru_put(_ROUTER_IDS, rid, True, _IDS_MAX)
    out = [o for o in resp.get("output") or [] if isinstance(o, dict)]
    _lru_put(_CHAINS, rid, (ctx["items"], out, resp), _CHAIN_MAX)


def _local_error(status, message, code="router_stateful_unsupported"):
    return ("local", status, {"error": {"message": message, "type": "invalid_request_error", "param": "previous_response_id", "code": code}})


def _provider_only_input(items):
    """Input items that reference state only the provider holds."""
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t == "item_reference":
            return "item_reference"
        if t == "reasoning" and not it.get("encrypted_content"):
            return "reasoning item without encrypted_content"
        for part in it.get("content") if isinstance(it.get("content"), list) else []:
            if isinstance(part, dict) and part.get("file_id"):
                return "file_id"
    return None


def _provider_only_tools(tools):
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "file_search":
            return "file_search (provider-side vector stores)"
        if t.get("type") == "code_interpreter" and isinstance(t.get("container"), dict) and t["container"].get("file_ids"):
            return "code_interpreter file_ids"
    return None


def _plan_responses(body, rid, owned, feature, opts):
    if opts.get("responses") is False:
        return None  # this client uses stateful Responses features; router-migrate's planner kept it on the provider
    if body.get("conversation") or body.get("background") or body.get("store") is True or body.get("prompt"):
        return None  # explicit storage, stored prompts, conversations: provider-side state Router does not keep [measured]
    items = _as_items(body.get("input"))
    prev = body.get("previous_response_id")
    if prev:
        if prev in _CHAINS:
            p_in, p_out, _ = _CHAINS[prev]
            p_out = [o for o in p_out if not (o.get("type") == "reasoning" and not o.get("encrypted_content"))]
            items = list(p_in) + p_out + items
        elif prev in _ROUTER_IDS:
            return _local_error(400, "previous_response_id %s was served by Router and its history is no longer in this process "
                                     "(router-migrate keeps the last %d chains in memory). Send the conversation inline, or set "
                                     "ROUTER_MIGRATE_DISABLE=1." % (prev, _CHAIN_MAX))
        else:
            return None  # the provider issued this id; the chain stays where it started
    why = _provider_only_input(items) or _provider_only_tools(body.get("tools"))
    if why:
        if prev:  # the provider never saw this chain, so sending it there cannot work either
            return _local_error(400, "This turn continues a conversation served by Router but needs provider-side state (%s)." % why)
        return None
    nb = dict(body, model=rid, input=items)
    for k in ("store", "previous_response_id"):
        nb.pop(k, None)
    if owned == "openai" and (CATALOG.get(rid) or {}).get("r"):
        inc = list(nb.get("include") or [])
        if "reasoning.encrypted_content" not in inc:  # store is off on Router, so reasoning must travel with the items [measured]
            nb["include"] = inc + ["reasoning.encrypted_content"]
    meta = dict(nb.get("metadata") or {})
    if feature and "feature" not in meta and len(meta) < 16:
        meta["feature"] = feature
        nb["metadata"] = meta
    if owned == "anthropic" and "prompt_cache_key" not in nb and feature:
        nb["prompt_cache_key"] = feature
    _service_tier(nb)
    return ("responses", nb, {"items": items})


def _local_retrieve(req):
    """GET /responses/{id} for a Router-issued id: answer from memory; the provider never saw it."""
    m = re.search(r"/responses/([^/?]+)(/input_items)?$", req.url.path)
    if not m or m.group(1) not in _ROUTER_IDS:
        return None
    rid = m.group(1)
    if rid not in _CHAINS:
        return _local_error(404, "Response %s was served by Router and is no longer in this process's memory." % rid)
    items, _, full = _CHAINS[rid]
    if req.method == "DELETE":
        del _CHAINS[rid]
        return ("local", 200, {"id": rid, "object": "response", "deleted": True})
    if m.group(2):
        return ("local", 200, {"object": "list", "data": items, "first_id": None, "last_id": None, "has_more": False})
    return ("local", 200, full)


def plan_request(req, opts=None):
    """Decide where a provider-bound request goes. Returns None (send to provider unchanged) or a routing tuple."""
    try:
        return _plan_request(req, opts or {})
    except Exception:
        return None  # anything unexpected: the provider gets the request exactly as before


def _plan_request(req, opts):
    if not active():
        return None
    host, path = req.url.host, req.url.path
    if req.method in ("GET", "DELETE") and host in _OPENAI_HOSTS and "/responses/" in path:
        return _local_retrieve(req)
    if req.method != "POST":
        return None
    if host in _OPENAI_HOSTS and (path.endswith("/chat/completions") or path.endswith("/responses")):
        body = json.loads(req.content or b"{}")
        rid = resolve_model(body.get("model"))
        prev = body.get("previous_response_id") if path.endswith("/responses") else None
        if (not rid or ("openai", rid) in _DENIED) and prev in _ROUTER_IDS:
            return _local_error(400, "previous_response_id %s was served by Router, but this turn's model %r cannot be served by Router."
                                % (prev, body.get("model")))
        if not rid or ("openai", rid) in _DENIED:
            return None
        owned = (CATALOG.get(rid) or {}).get("o")
        feature = _feature()
        if path.endswith("/chat/completions"):
            try:
                nb, ctx = chat_to_responses(body, rid, owned, feature)
            except Untranslatable:
                return None
            return ("chat", nb, ctx)
        return _plan_responses(body, rid, owned, feature, opts)
    if host in _ANTHROPIC_HOSTS and (path.rstrip("/").endswith("/v1/messages") or path.endswith("/v1/messages/count_tokens")):
        if req.headers.get("anthropic-beta"):
            return None  # beta features are unmeasured on Router; keep on Anthropic
        body = json.loads(req.content or b"{}")
        rid = resolve_model(body.get("model"))
        if not rid or ("anthropic", rid) in _DENIED:
            return None
        return ("messages", dict(body, model=rid), {"count": path.endswith("count_tokens")})
    return None


def _router_headers(req, anthropic=False):
    h = {"Authorization": "Bearer " + os.environ["RAMP_ROUTER_API_KEY"], "Content-Type": "application/json",
         "User-Agent": req.headers.get("User-Agent") or ("router-migrate-runtime/" + VERSION), "Accept": req.headers.get("Accept", "application/json")}
    for k in ("x-stainless-retry-count", "idempotency-key"):
        if req.headers.get(k):
            h[k] = req.headers[k]
    if anthropic:
        h["anthropic-version"] = req.headers.get("anthropic-version", "2023-06-01")
    return h


def _router_request(req, route, h):
    kind, nb, ctx = route
    url = _base() + ("/v1/messages/count_tokens" if kind == "messages" and ctx.get("count") else "/v1/messages" if kind == "messages" else "/v1/responses")
    return h.Request("POST", url, headers=_router_headers(req, anthropic=(kind == "messages")), content=json.dumps(nb).encode(),
                     extensions=req.extensions)


def _fallback_ok(req, route, status, body):
    """True when Router refused before generating anything and the provider can serve the original request."""
    if (os.environ.get("ROUTER_MIGRATE_FALLBACK") or "").strip().lower() in ("0", "off", "false", "no"):
        return False
    if not (req.headers.get("authorization") or req.headers.get("x-api-key")):
        return False  # no provider credential: surface Router's error rather than a confusing 401
    try:
        err = json.loads(body)
        err = err.get("error") if isinstance(err, dict) else {}
        err = err if isinstance(err, dict) else {}
    except ValueError:
        err = {}
    code, msg = str(err.get("code") or ""), str(err.get("message") or "")
    if status == 501:
        return True
    if status == 403:
        return True  # model/geography/permission refusal for this key (e.g. data_residency_mismatch [measured])
    if status == 404 and (code == "model_not_found" or "does not exist" in msg):
        _DENIED.add(("anthropic" if route[0] == "messages" else "openai", route[1].get("model")))
        return True
    if status == 400 and (code in ("unknown_parameter", "unsupported_parameter", "invalid_value") or msg.startswith(("Unknown parameter", "Unsupported parameter"))
                          or "Extra inputs are not permitted" in msg or "requires a caller-owned provider credential" in msg):
        return True
    return False


_HOP = ("content-length", "content-encoding", "transfer-encoding", "connection")


def _headers(up, **override):
    d = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
    d.update(override)
    return d


def _translate_json(resp_bytes, status, ctx):
    if status >= 400:
        return resp_bytes
    try:
        return json.dumps(responses_to_chat(json.loads(resp_bytes), ctx)).encode()
    except ValueError:
        return resp_bytes


class _ResponsesTee:
    """Watches a passed-through Responses SSE stream for the final response, to remember the chain."""

    def __init__(self, ctx):
        self.ctx, self.sse = ctx, _SSE()

    def feed(self, data):
        for ev in self.sse.feed(data):
            if ev.get("type") in ("response.completed", "response.incomplete") and isinstance(ev.get("response"), dict):
                _remember(self.ctx, ev["response"])


_CLASSES = {}


def _classes(libname):
    """(RouterTransport, AsyncRouterTransport) built on `libname` (httpx or httpx2), or None if it is not installed."""
    if libname in _CLASSES:
        return _CLASSES[libname]
    h = _lib(libname)
    if h is None:
        _CLASSES[libname] = None
        return None

    class _SyncStream(h.SyncByteStream):
        def __init__(self, upstream, writer):
            self.up, self.w = upstream, writer

        def __iter__(self):
            try:
                for data in self.up.iter_raw():
                    for out in self.w.feed(data):
                        yield out
                    if getattr(self.w, "done", False):
                        break
                for out in self.w.finish():
                    yield out
            finally:
                self.up.close()

        def close(self):
            self.up.close()

    class _AsyncStream(h.AsyncByteStream):
        def __init__(self, upstream, writer):
            self.up, self.w = upstream, writer

        async def __aiter__(self):
            try:
                async for data in self.up.aiter_raw():
                    for out in self.w.feed(data):
                        yield out
                    if getattr(self.w, "done", False):
                        break
                for out in self.w.finish():
                    yield out
            finally:
                await self.up.aclose()

        async def aclose(self):
            await self.up.aclose()

    def _local(route):
        _, status, payload = route
        return h.Response(status, headers={"content-type": "application/json"}, content=json.dumps(payload).encode())

    def _done(up, route, body):
        """Non-streamed Router reply (body already read) -> what the SDK expects."""
        kind, nb, ctx = route
        if up.status_code < 400 and kind == "responses":
            try:
                _remember(ctx, json.loads(body))
            except ValueError:
                pass
        content = _translate_json(body, up.status_code, ctx) if kind == "chat" else body
        return h.Response(up.status_code, headers=_headers(up), content=content)

    def _streamed(up, route, stream_cls):
        kind, nb, ctx = route
        if kind == "chat":
            return h.Response(up.status_code, headers=_headers(up, **{"content-type": "text/event-stream"}),
                              stream=stream_cls(up, _ChunkWriter(ctx)), extensions=up.extensions)
        if kind == "responses":
            return h.Response(up.status_code, headers=_headers(up), stream=stream_cls(up, _PassThrough(_ResponsesTee(ctx))),
                              extensions=up.extensions)
        return up

    class RouterTransport(h.BaseTransport):
        """Sync transport: Router for what it can serve, the wrapped transport (provider) for the rest."""

        def __init__(self, inner=None, opts=None):
            self.inner, self.opts = inner or h.HTTPTransport(), dict(opts or {})

        def handle_request(self, request):
            route = plan_request(request, self.opts)
            if route is None:
                return self.inner.handle_request(request)
            if route[0] == "local":
                return _local(route)
            up = self.inner.handle_request(_router_request(request, route, h))
            if up.status_code >= 400:
                body = up.read()
                up.close()
                if _fallback_ok(request, route, up.status_code, body):
                    return self.inner.handle_request(request)  # the original request, unchanged
                return _done(up, route, body)
            if route[1].get("stream"):
                return _streamed(up, route, _SyncStream)
            body = up.read()
            up.close()
            return _done(up, route, body)

        def close(self):
            self.inner.close()

    class AsyncRouterTransport(h.AsyncBaseTransport):
        def __init__(self, inner=None, opts=None):
            self.inner, self.opts = inner or h.AsyncHTTPTransport(), dict(opts or {})

        async def handle_async_request(self, request):
            route = plan_request(request, self.opts)
            if route is None:
                return await self.inner.handle_async_request(request)
            if route[0] == "local":
                return _local(route)
            up = await self.inner.handle_async_request(_router_request(request, route, h))
            if up.status_code >= 400:
                body = await up.aread()
                await up.aclose()
                if _fallback_ok(request, route, up.status_code, body):
                    return await self.inner.handle_async_request(request)
                return _done(up, route, body)
            if route[1].get("stream"):
                return _streamed(up, route, _AsyncStream)
            body = await up.aread()
            await up.aclose()
            return _done(up, route, body)

        async def aclose(self):
            await self.inner.aclose()

    _CLASSES[libname] = (RouterTransport, AsyncRouterTransport)
    return _CLASSES[libname]


_default = _classes("httpx") or _classes("httpx2")
RouterTransport, AsyncRouterTransport = _default if _default else (None, None)


def _sdk_lib(client_or_module):
    """'httpx' or 'httpx2': the HTTP library a client (or SDK package name) is built on."""
    inner = getattr(client_or_module, "_client", None)
    if inner is not None:
        root = type(inner).__module__.split(".")[0]
        if root in ("httpx", "httpx2"):
            return root
    pkg = client_or_module if isinstance(client_or_module, str) else type(client_or_module).__module__.split(".")[0]
    try:
        base = importlib.import_module(pkg + "._base_client")
        if getattr(base, "httpx2", None) is not None:
            return "httpx2"
    except Exception:
        pass
    return "httpx" if _lib("httpx") else "httpx2"


# ------------------------------------------------------------------------------------------------ public helpers
def _client_factory(libname, is_async):
    """The SDK's default client class for this library (keeps its timeouts/limits), else the plain library client."""
    h = _lib(libname)
    names = {("httpx", False): "DefaultHttpxClient", ("httpx", True): "DefaultAsyncHttpxClient",
             ("httpx2", False): "DefaultHttpx2Client", ("httpx2", True): "DefaultAsyncHttpx2Client"}
    try:
        import openai
        cls = getattr(openai, names[(libname, is_async)])
        if cls.__module__.split(".")[0] == "openai":
            return cls, {}
    except Exception:
        pass
    return (h.AsyncClient if is_async else h.Client), {"timeout": h.Timeout(600.0, connect=5.0), "follow_redirects": True}


def _install(hc, opts=None):
    """Put the Router transport *under* an existing httpx client, in place: its own transport and every proxy mount
    (HTTP(S)_PROXY, proxy=) become the inner transports, so headers, auth, hooks, proxies and pools all stay. Idempotent."""
    lib = next((n for n in ("httpx", "httpx2") if _lib(n) is not None and isinstance(hc, (_lib(n).Client, _lib(n).AsyncClient))), None)
    h, classes = (_lib(lib), _classes(lib)) if lib else (None, None)
    if classes is None or not hasattr(hc, "_transport"):
        return False
    is_async = isinstance(hc, h.AsyncClient)
    T = classes[1] if is_async else classes[0]
    opts = dict(opts or {})

    def wrap(t):
        if t is None:
            return None
        if isinstance(t, T):
            if opts.get("responses") is False:
                t.opts["responses"] = False  # the stricter setting wins when clients share one httpx client
            return t
        return T(inner=t, opts=opts)

    hc._transport = wrap(hc._transport)
    mounts = getattr(hc, "_mounts", None)
    if isinstance(mounts, dict):
        for k in list(mounts):
            mounts[k] = wrap(mounts[k])
    return True


def _make_client(libname, is_async, opts=None, **kwargs):
    h = _lib(libname)
    if h is None or _classes(libname) is None:
        return None
    cls, defaults = _client_factory(libname, is_async)
    for k, v in defaults.items():
        kwargs.setdefault(k, v)
    hc = cls(**kwargs)  # no transport= here, so httpx still sets up env proxies (trust_env) as it normally would
    _install(hc, opts)
    return hc


def http_client(**kwargs):
    """Client routed through Router when active (pass as http_client= to OpenAI/LangChain/LlamaIndex...); None when inactive."""
    if not active():
        return kwargs.pop("_default", None)
    kwargs.pop("_default", None)
    return _make_client(_sdk_lib("openai"), False, **kwargs)


def async_http_client(**kwargs):
    if not active():
        return kwargs.pop("_default", None)
    kwargs.pop("_default", None)
    return _make_client(_sdk_lib("openai"), True, **kwargs)


def _with_client(client, opts=None):
    try:
        _install(client._client, opts)
    except Exception:
        pass  # unknown client shape (e.g. a test double): leave it exactly as it was
    return client


def _is(client, pkg, names):
    try:
        mod = importlib.import_module(pkg)
        return isinstance(client, tuple(getattr(mod, n) for n in names if hasattr(mod, n)))
    except Exception:
        return False


def _is_openai(client):
    return _is(client, "openai", ("OpenAI", "AsyncOpenAI")) and not _is(client, "openai", ("AzureOpenAI", "AsyncAzureOpenAI"))


def wrap_openai(client, responses=True):
    """OpenAI / AsyncOpenAI client (or subclass): its traffic goes through Router, in place; unchanged when RAMP_ROUTER_API_KEY
    is unset. responses=False keeps this client's Responses API traffic on OpenAI (stateful use: previous_response_id etc.)."""
    if not active() or not _is_openai(client):
        return client  # Azure deployments and test doubles stay on their configured endpoint
    return _with_client(client, {"responses": responses})


def wrap_anthropic(client):
    if not active() or not _is(client, "anthropic", ("Anthropic", "AsyncAnthropic")):
        return client  # Bedrock / Vertex clients and test doubles stay put
    return _with_client(client)


def wrap_langchain(llm, responses=True):
    """LangChain ChatOpenAI / ChatAnthropic (or LlamaIndex-style objects holding SDK clients): wrap the SDK clients they built.
    Every request still goes through per-request planning and provider fallback, like a wrapped SDK client."""
    if not active():
        return llm
    seen = []
    for attr in ("root_client", "root_async_client", "_client", "_async_client", "client", "async_client"):
        try:
            c = getattr(llm, attr, None)  # ChatAnthropic builds _client/_async_client lazily (cached_property)
        except Exception:
            continue
        for cand in (c, getattr(c, "_client", None)):  # ChatOpenAI.client is root_client.chat.completions
            if cand is None or any(cand is x for x in seen):
                continue
            seen.append(cand)
            if _is_openai(cand):
                wrap_openai(cand, responses=responses)
            elif _is(cand, "anthropic", ("Anthropic", "AsyncAnthropic")):
                wrap_anthropic(cand)
    return llm


def install_openai_module():
    """For code that calls the module-level client (openai.chat.completions.create)."""
    if not active():
        return
    try:
        import openai
        if getattr(openai, "http_client", None) is None:
            openai.http_client = http_client()
    except Exception:
        pass


def chat_anthropic_kwargs():
    """Kept for code patched by router-migrate <= 0.1.0, which swapped ChatAnthropic's base_url and key wholesale. That sent
    every request (dated models, beta headers) to Router without per-request planning, so it now returns nothing; new
    patches use wrap_langchain(ChatAnthropic(...))."""
    return {}


def install_litellm():
    """Route LiteLLM's OpenAI-provider calls through the transport (LiteLLM builds OpenAI SDK clients on these sessions)."""
    if not active():
        return
    try:
        import litellm
        if getattr(litellm, "client_session", None) is None:
            litellm.client_session = http_client()
        if getattr(litellm, "aclient_session", None) is None:
            litellm.aclient_session = async_http_client()
    except Exception:
        pass
