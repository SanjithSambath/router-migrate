"""router-sim: a local stand-in for https://api.router.com that enforces Router's measured behavior.

Every rejection names its source:
  [measured]  observed against the live Router API (~/code/router-live-check results v1/v2)
  [upstream]  the OpenAI Responses / Anthropic Messages API contract Router forwards to (not separately measured)
Outputs are deterministic so tests and evals are repeatable. Every request is appended to a JSONL log with a verdict.
"""
import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..catalog import Catalog

RESPONSES_FIELDS = {
    "model", "models", "input", "instructions", "max_output_tokens", "temperature", "top_p", "tools", "tool_choice",
    "parallel_tool_calls", "text", "reasoning", "metadata", "stream", "stream_options", "store", "user", "service_tier",
    "truncation", "top_logprobs", "prompt_cache_key", "prompt_cache_retention", "safety_identifier", "include",
    "max_tool_calls", "previous_response_id", "conversation", "background", "frequency_penalty", "presence_penalty",
    "allow_flex_tier", "provider_timeout", "fallback_timeout", "timeout_before_headers", "prompt",
}
# Chat Completions leftovers Router rejects on /v1/responses [measured v1/v2].
CHAT_LEFTOVERS = {
    "messages": "unsupported_parameter", "max_tokens": "unknown_parameter", "response_format": "unsupported_parameter",
    "n": "unknown_parameter", "seed": "unknown_parameter", "logprobs": "unknown_parameter",
}
MESSAGES_FIELDS = {
    "model", "messages", "max_tokens", "system", "temperature", "top_p", "top_k", "stop_sequences", "stream", "tools",
    "tool_choice", "metadata", "thinking", "service_tier", "container", "mcp_servers", "context_management",
}


class SimError(Exception):
    def __init__(self, status, code, message, source, kind="invalid_request_error", plain=False):
        super().__init__(message)
        self.status, self.code, self.message, self.source, self.kind, self.plain = status, code, message, source, kind, plain


def _rid(prefix):
    return prefix + uuid.uuid4().hex[:24]


class Sim:
    def __init__(self, log_path=None, catalog=None):
        self.catalog = catalog or Catalog.load()
        self.log_path = log_path
        self.lock = threading.Lock()
        self.records = []

    # ------------------------------------------------------------------ helpers
    def model_or_404(self, name, surface):
        if not isinstance(name, str) or not name:
            raise SimError(400, "invalid_request", "`model` is required", "[measured]")
        rid = name if name in self.catalog.by_id else self.catalog.alias.get(name)
        if not rid:
            raise SimError(404, "model_not_found", "The model `%s` does not exist or you do not have access to it." % name,
                           "[measured] dated/prefixed/unknown ids 404")
        m = self.catalog.by_id[rid]
        if surface not in (m.get("surfaces") or []):
            raise SimError(404, "model_not_found", "Model `%s` is not available on /v1/%s" % (name, surface), "[measured] surfaces")
        return m

    def log(self, rec):
        with self.lock:
            self.records.append(rec)
            if self.log_path:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------------ /v1/responses
    def validate_responses(self, b):
        for k, code in CHAT_LEFTOVERS.items():
            if k in b:
                raise SimError(400, code, "Unsupported parameter: '%s'." % k, "[measured] chat-style field on Responses")
        unknown = [k for k in b if k not in RESPONSES_FIELDS and k not in ("stop",)]
        if unknown:
            raise SimError(400, "unknown_parameter", "Unknown parameter: '%s'." % unknown[0], "[measured] unknown field -> 400")
        if "model" in b and "models" in b:
            raise SimError(400, "invalid_request", "`models` cannot be provided with `model`", "[measured]")
        if "models" in b:
            cands = b["models"]
            if not isinstance(cands, list) or not cands:
                raise SimError(400, "invalid_request", "`models` must be a non-empty list", "[measured]")
            m = None
            for c in cands:  # first candidate that resolves wins (fallback list semantics)
                name = c.split(":", 1)[1] if ":" in c else c
                name = name.rsplit(":", 1)[0] if name.endswith((":flex", ":default")) else name
                try:
                    m = self.model_or_404(name, "responses")
                    break
                except SimError:
                    continue
            if m is None:
                raise SimError(404, "model_not_found", "No candidate in `models` is available", "[measured]")
        else:
            m = self.model_or_404(b.get("model"), "responses")
        openai_owned = m.get("owned_by") == "openai"
        if b.get("store") is True:
            raise SimError(400, "invalid_request", "OpenAI `store` must be `false` when using a shared gateway credential.", "[measured]")
        if b.get("previous_response_id") and openai_owned:
            raise SimError(400, "invalid_request", "OpenAI `previous_response_id` requires a caller-owned provider credential; "
                           "send the complete conversation history inline instead.", "[measured]")
        if b.get("conversation"):
            raise SimError(400, "invalid_request", "OpenAI `conversation` requires a caller-owned provider credential.", "[measured]")
        if b.get("background"):
            raise SimError(501, "not_implemented", "Background v1 requires explicit model sail/moonshotai/Kimi-K3", "[measured]",
                           kind="not_implemented_error")
        if "stop" in b:
            if openai_owned:
                raise SimError(501, "not_implemented", "Stop sequences cannot be translated to OpenAI: the Responses API has no stop parameter.",
                               "[measured] stop on OpenAI -> 501", kind="not_implemented_error")
        if "temperature" in b and m.get("temperature") is False:
            raise SimError(400, "invalid_request_error", "Unsupported parameter: 'temperature' is not supported with this model.",
                           "[measured] capabilities.temperature=false")
        r = b.get("reasoning")
        if isinstance(r, dict) and r.get("effort") is not None:
            if not m.get("reasoning"):
                raise SimError(400, "unsupported_parameter", "Unsupported parameter: 'reasoning.effort' is not supported with this model.", "[measured]")
            if m.get("owned_by") == "anthropic" and isinstance(b.get("max_output_tokens"), int) and b["max_output_tokens"] < 1100:
                raise SimError(400, "invalid_request_error", "thinking.enabled.budget_tokens: Input should be greater than or equal to 1024",
                               "[measured] effort on Claude needs room")
        if "allow_flex_tier" in b and not isinstance(b["allow_flex_tier"], bool):
            raise SimError(400, "invalid_request", "`allow_flex_tier` must be a boolean.", "[measured]")
        if "fallback_timeout" in b and not (b.get("stream") and len(b.get("models") or []) >= 2):
            raise SimError(400, "invalid_request", "`fallback_timeout` requires `stream: true` and at least two `models` candidates.", "[measured]")
        for k in ("provider_timeout", "timeout_before_headers"):
            if isinstance(b.get(k), (int, float)) and b[k] > 660:
                raise SimError(400, "invalid_request", "%s exceeds this gateway's maximum allowed provider timeout of 660 seconds." % k, "[measured]")
        self.validate_tools(b)
        self.validate_input(b.get("input"))
        tf = (b.get("text") or {}).get("format") if isinstance(b.get("text"), dict) else None
        if tf is not None:
            if not isinstance(tf, dict) or tf.get("type") not in ("text", "json_object", "json_schema"):
                raise SimError(400, "invalid_value", "Invalid value for 'text.format.type'.", "[upstream]")
            if tf.get("type") == "json_schema" and (not tf.get("name") or not isinstance(tf.get("schema"), dict)):
                raise SimError(400, "missing_required_parameter", "Missing required parameter: 'text.format.name' / 'schema'.", "[upstream]")
        return m

    def validate_tools(self, b):
        tools = b.get("tools")
        if tools is None:
            pass
        elif not isinstance(tools, list):
            raise SimError(400, "invalid_type", "Invalid type for 'tools'.", "[upstream]")
        else:
            for i, t in enumerate(tools):
                if not isinstance(t, dict) or "type" not in t:
                    raise SimError(400, "missing_required_parameter", "Missing required parameter: 'tools[%d].type'." % i, "[upstream]")
                if t["type"] == "file_search" and t.get("vector_store_ids"):
                    raise SimError(404, None, "Vector store with id %s not found." % t["vector_store_ids"],
                                   "[measured] Router's credential cannot see vector stores created with the caller's provider key")
                if t["type"] == "function":
                    if "function" in t or not t.get("name"):
                        raise SimError(400, "missing_required_parameter", "Missing required parameter: 'tools[%d].name'." % i,
                                       "[upstream] Responses tools are flat {type,name,parameters}")
                    if t.get("strict", True) and not _strict_ok(t.get("parameters") or {}):
                        raise SimError(400, "invalid_function_parameters",
                                       "Invalid schema for function '%s': In context=(), 'additionalProperties' is required to be supplied and to be false, "
                                       "and all properties must be listed in 'required'." % t["name"],
                                       "[upstream] Responses function tools default to strict=true")
        tc = b.get("tool_choice")
        if isinstance(tc, dict):
            if tc.get("type") == "function" and not tc.get("name"):
                raise SimError(400, "missing_required_parameter", "Missing required parameter: 'tool_choice.name'.", "[upstream]")
        elif tc is not None and tc not in ("auto", "none", "required"):
            raise SimError(400, "invalid_value", "Invalid value for 'tool_choice'.", "[upstream]")

    def validate_input(self, inp):
        if inp is None:
            raise SimError(400, "missing_required_parameter", "Missing required parameter: 'input'.", "[upstream]")
        if isinstance(inp, str):
            return
        if not isinstance(inp, list):
            raise SimError(400, "invalid_type", "Invalid type for 'input'.", "[upstream]")
        calls = set()
        for i, it in enumerate(inp):
            if not isinstance(it, dict):
                raise SimError(400, "invalid_type", "Invalid type for 'input[%d]'." % i, "[upstream]")
            t = it.get("type", "message" if "role" in it else None)
            if t == "message":
                role = it.get("role")
                if role not in ("user", "assistant", "system", "developer"):
                    raise SimError(400, "invalid_value", "Invalid value: '%s'. Supported values are: 'assistant', 'system', 'developer', and 'user'." % role,
                                   "[upstream] no 'tool' role in Responses")
                c = it.get("content")
                if isinstance(c, list):
                    allowed = ("output_text", "refusal") if role == "assistant" else ("input_text", "input_image", "input_file", "input_audio")
                    for j, p in enumerate(c):
                        pt = p.get("type") if isinstance(p, dict) else None
                        if pt not in allowed:
                            raise SimError(400, "invalid_value", "Invalid value: '%s' in input[%d].content[%d]. Supported values are: %s." % (pt, i, j, ", ".join(allowed)),
                                           "[upstream]")
                        if pt == "input_image" and not (p.get("image_url") or p.get("file_id")):
                            raise SimError(400, "missing_required_parameter", "input_image needs image_url or file_id", "[upstream]")
                elif not isinstance(c, str):
                    raise SimError(400, "invalid_type", "Invalid type for 'input[%d].content'." % i, "[upstream]")
            elif t == "function_call":
                if not it.get("call_id") or not it.get("name") or not isinstance(it.get("arguments"), str):
                    raise SimError(400, "missing_required_parameter", "function_call needs call_id, name and string arguments", "[upstream]")
                calls.add(it["call_id"])
            elif t == "function_call_output":
                if it.get("call_id") not in calls:
                    raise SimError(400, "invalid_request_error", "No tool call found for function call output with call_id %s." % it.get("call_id"),
                                   "[upstream]")
                if not isinstance(it.get("output"), (str, list)):
                    raise SimError(400, "invalid_type", "function_call_output.output must be a string", "[upstream]")
            elif t in ("reasoning", "item_reference") and not it.get("encrypted_content"):
                raise SimError(400, "invalid_request_error", "Item with id '%s' not found. Items are not persisted when `store` is set to false. "
                               "Try again with `store` set to true, or remove this item from your input." % it.get("id"),
                               "[upstream] Router forces store:false, so only self-contained (encrypted) reasoning items can be replayed")
            elif t in ("reasoning", "item_reference", "web_search_call", "file_search_call", "computer_call", "computer_call_output",
                       "code_interpreter_call", "mcp_call", "mcp_list_tools", "image_generation_call", "local_shell_call"):
                continue
            else:
                raise SimError(400, "invalid_value", "Invalid input item type: '%s'." % t, "[upstream]")

    def responses_result(self, b, m):
        """Deterministic output: a function call when tools are offered and the last input is a user turn, else text."""
        inp = b.get("input")
        last = inp[-1] if isinstance(inp, list) and inp else {"role": "user"}
        tools = [t for t in (b.get("tools") or []) if t.get("type") == "function"]
        tc = b.get("tool_choice")
        out = []
        want_call = tools and tc != "none" and (last.get("role") == "user" or last.get("type") == "message" and last.get("role") == "user")
        if isinstance(tc, dict) and tc.get("name"):
            tools = [t for t in tools if t.get("name") == tc["name"]] or tools
        if want_call:
            t = tools[0]
            out.append({"type": "function_call", "id": _rid("fc_"), "call_id": _rid("call_"), "name": t["name"],
                        "arguments": json.dumps(_fill(t.get("parameters") or {})), "status": "completed"})
        else:
            tf = (b.get("text") or {}).get("format") if isinstance(b.get("text"), dict) else None
            if tf and tf.get("type") == "json_schema":
                text = json.dumps(_fill(tf.get("schema") or {}))
            elif tf and tf.get("type") == "json_object":
                text = json.dumps({"result": "pong"})
            else:
                text = "pong"
            stops = b.get("stop") if isinstance(b.get("stop"), list) else ([b["stop"]] if isinstance(b.get("stop"), str) else [])
            for s in stops:
                if s and s in text:
                    text = text.split(s, 1)[0]
            out.append({"type": "message", "id": _rid("msg_"), "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]})
        mot = b.get("max_output_tokens")
        n_out = 4 if not out or out[-1]["type"] == "message" else 12
        status, incomplete = "completed", None
        if isinstance(mot, int) and mot < n_out:
            status, incomplete = "incomplete", {"reason": "max_output_tokens"}
        n_in = max(1, len(json.dumps(inp)) // 4)
        price = m.get("pricing") or {}
        cost = (n_in * float(price.get("input") or 0) + n_out * float(price.get("output") or 0)) / 1e6
        tier = b.get("service_tier") if b.get("service_tier") in ("default",) else ("flex" if m.get("owned_by") == "openai" and b.get("allow_flex_tier", True) and not b.get("models") else "default")
        return {
            "id": _rid("resp_"), "object": "response", "created_at": int(time.time()), "status": status,
            "incomplete_details": incomplete, "error": None, "model": m.get("provider_model") or m["id"], "provider": (m.get("providers") or ["router"])[0],
            "output": out, "parallel_tool_calls": b.get("parallel_tool_calls", True), "tool_choice": b.get("tool_choice", "auto"),
            "tools": b.get("tools") or [], "store": False, "service_tier": tier, "metadata": b.get("metadata"),
            "usage": {"input_tokens": n_in, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": n_out,
                      "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": n_in + n_out, "cost": round(cost, 10)},
        }

    def responses_events(self, resp):
        seq = [0]

        def ev(t, **kw):
            seq[0] += 1
            d = {"type": t, "sequence_number": seq[0]}
            d.update(kw)
            return t, d
        shell = dict(resp, status="in_progress", output=[], usage=None)
        yield ev("response.created", response=shell)
        yield ev("response.in_progress", response=shell)
        for oi, item in enumerate(resp["output"]):
            if item["type"] == "message":
                yield ev("response.output_item.added", output_index=oi, item=dict(item, content=[], status="in_progress"))
                yield ev("response.content_part.added", output_index=oi, item_id=item["id"], content_index=0,
                         part={"type": "output_text", "text": "", "annotations": []})
                text = item["content"][0]["text"]
                for k in range(0, len(text), 3):
                    yield ev("response.output_text.delta", output_index=oi, item_id=item["id"], content_index=0, delta=text[k:k + 3])
                yield ev("response.output_text.done", output_index=oi, item_id=item["id"], content_index=0, text=text)
                yield ev("response.content_part.done", output_index=oi, item_id=item["id"], content_index=0, part=item["content"][0])
            else:
                yield ev("response.output_item.added", output_index=oi, item=dict(item, arguments="", status="in_progress"))
                a = item["arguments"]
                for k in range(0, len(a), 5):
                    yield ev("response.function_call_arguments.delta", output_index=oi, item_id=item["id"], delta=a[k:k + 5])
                yield ev("response.function_call_arguments.done", output_index=oi, item_id=item["id"], arguments=a)
            yield ev("response.output_item.done", output_index=oi, item=item)
        yield ev("response.incomplete" if resp["status"] == "incomplete" else "response.completed", response=resp)

    # ------------------------------------------------------------------ /v1/messages
    def validate_messages(self, b, headers):
        if not headers.get("anthropic-version"):
            raise SimError(400, "invalid_request_error", "anthropic-version header is required.", "[measured]")
        m = self.model_or_404(b.get("model"), "messages")
        unknown = [k for k in b if k not in MESSAGES_FIELDS]
        if unknown and m.get("owned_by") in ("anthropic", "bedrock"):
            raise SimError(400, "invalid_request_error", "%s: Extra inputs are not permitted" % unknown[0], "[measured] strict on Claude")
        if not isinstance(b.get("max_tokens"), int):
            raise SimError(400, "invalid_request_error", "max_tokens: Field required", "[upstream]")
        msgs = b.get("messages")
        if not isinstance(msgs, list) or not msgs:
            raise SimError(400, "invalid_request_error", "messages: Field required", "[upstream]")
        for i, mm in enumerate(msgs):
            if mm.get("role") not in ("user", "assistant"):
                raise SimError(400, "invalid_request_error", "messages.%d.role: Input should be 'user' or 'assistant'" % i, "[upstream]")
        if "temperature" in b and m.get("temperature") is False:
            raise SimError(400, "invalid_request_error", "Unsupported parameter: 'temperature' is not supported with this model.", "[measured]")
        tc = b.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "tool" and tc.get("name") not in [t.get("name") for t in b.get("tools") or []]:
            raise SimError(400, "invalid_request_error", "Tool '%s' not found in provided tools" % tc.get("name"),
                           "[measured] live replay of anthropic-quickstarts")
        if b.get("stop_sequences") and m.get("owned_by") == "openai":
            raise SimError(501, "not_implemented", "Stop sequences cannot be translated to OpenAI: the Responses API has no stop parameter.",
                           "[measured]", kind="not_implemented_error")
        return m

    def messages_result(self, b, m):
        tools = b.get("tools") or []
        last = b["messages"][-1]
        last_is_result = isinstance(last.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in last["content"])
        if tools and not last_is_result and (b.get("tool_choice") or {}).get("type") != "none":
            content = [{"type": "tool_use", "id": _rid("toolu_"), "name": tools[0]["name"], "input": _fill(tools[0].get("input_schema") or {})}]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": "pong"}]
            stop = "end_turn"
        n_in = max(1, len(json.dumps(b["messages"])) // 4)
        return {"id": _rid("msg_"), "type": "message", "role": "assistant", "model": m.get("provider_model") or m["id"], "provider": (m.get("providers") or ["router"])[0],
                "content": content, "stop_reason": stop, "stop_sequence": None,
                "usage": {"input_tokens": n_in, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}

    def messages_events(self, msg):
        shell = dict(msg, content=[], stop_reason=None, usage={"input_tokens": msg["usage"]["input_tokens"], "output_tokens": 0})
        yield "message_start", {"type": "message_start", "message": shell}
        for i, blk in enumerate(msg["content"]):
            if blk["type"] == "text":
                yield "content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}
                yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": blk["text"]}}
            else:
                yield "content_block_start", {"type": "content_block_start", "index": i, "content_block": dict(blk, input={})}
                yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": json.dumps(blk["input"])}}
            yield "content_block_stop", {"type": "content_block_stop", "index": i}
        yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
                                "usage": {"output_tokens": msg["usage"]["output_tokens"]}}
        yield "message_stop", {"type": "message_stop"}


def _strict_ok(schema):
    """Strict-mode schema rules: every object has additionalProperties:false and lists all properties as required."""
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if schema.get("additionalProperties", True) is not False or set(schema.get("required") or []) != set(props):
            return False
        return all(_strict_ok(v) for v in props.values())
    if schema.get("type") == "array":
        return _strict_ok(schema.get("items") or {})
    return True


def _fill(schema):
    """Produce a value satisfying a JSON schema's required fields (deterministic)."""
    t = schema.get("type") if isinstance(schema, dict) else None
    if "enum" in (schema or {}):
        return schema["enum"][0]
    if t == "object" or (t is None and "properties" in (schema or {})):
        props = schema.get("properties") or {}
        req = schema.get("required") or list(props)
        return {k: _fill(props.get(k, {})) for k in req}
    if t == "array":
        return [_fill(schema.get("items") or {"type": "string"})]
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if isinstance(t, list):
        return _fill(dict(schema, type=[x for x in t if x != "null"][0]))
    return "x"


def make_handler(sim):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, status, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("x-request-id", uuid.uuid4().hex)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _sse(self, events, provider):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("x-router-provider", provider)
            self.send_header("Connection", "close")
            self.end_headers()
            for name, data in events:
                self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode())
                self.wfile.flush()
            self.close_connection = True

        def _err(self, e, surface, rec):
            rec.update(status=e.status, valid=False, error=e.message, error_code=e.code, rule_source=e.source)
            sim.log(rec)
            if e.plain:
                return self._send(e.status, e.message, "text/plain; charset=UTF-8")
            if surface == "messages":
                return self._send(e.status, {"type": "error", "error": {"type": e.kind, "message": e.message}})
            return self._send(e.status, {"error": {"message": e.message, "type": e.kind, "param": None, "code": e.code}})

        def _proxied(self):
            """The request path, also when this sim is used as the HTTP proxy (probes: HTTP(S)_PROXY points here so nothing
            leaves the machine). Absolute-form requests for this sim are served; any other destination is blocked and logged."""
            if not self.path.startswith(("http://", "https://")):
                return self.path.split("?")[0]
            from urllib.parse import urlsplit
            u = urlsplit(self.path)
            own = {"%s:%d" % (h, self.server.server_address[1]) for h in ("127.0.0.1", "localhost")}
            if u.netloc in own:
                return u.path
            self._block(u.netloc)
            return None

        def _block(self, host):
            sim.log({"ts": time.time(), "method": self.command, "path": host, "valid": False, "blocked": True,
                     "error": "outbound traffic blocked (router-sim is the proxy)"})
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            self._send(403, {"error": {"message": "router-sim: outbound traffic to %s is blocked here; only Router requests are "
                                                  "served (stub the provider call in your probe)" % host, "type": "blocked", "code": "blocked"}})

        def do_CONNECT(self):  # HTTPS to anywhere (a provider) through the proxy: refuse, so the run fails fast and offline
            self._block(self.path)

        def _auth(self):
            a = self.headers.get("Authorization", "")
            return (a.startswith("Bearer ") and len(a) > 8) or bool(self.headers.get("x-api-key"))

        def do_GET(self):
            path = self._proxied()
            if path is None:
                return
            if path == "/__last":  # test helper: the most recent logged request
                return self._send(200, sim.records[-1] if sim.records else {})
            if path == "/__log":
                return self._send(200, sim.records)
            rec = {"ts": time.time(), "method": "GET", "path": path, "ua": self.headers.get("User-Agent")}
            if not self._auth():
                rec.update(status=401, valid=False, error="missing key")
                sim.log(rec)
                return self._send(401, {"error": {"message": "Invalid API key.", "type": "authentication_error", "param": None, "code": "invalid_api_key"}})
            if path in ("/v1/models", "/models"):
                rec.update(status=200, valid=True)
                sim.log(rec)
                data = [{"id": m["id"], "object": "model", "created": 0, "owned_by": m["owned_by"]} for m in sim.catalog.models]
                return self._send(200, {"object": "list", "data": data})
            rec.update(status=404, valid=False, error="404 page not found", rule_source="[measured] only /v1/models GET")
            sim.log(rec)
            return self._send(404, "404 page not found\n", "text/plain; charset=utf-8")

        def do_DELETE(self):
            self.do_POST()

        def do_POST(self):
            path = self._proxied()
            if path is None:
                return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            rec = {"ts": time.time(), "method": self.command, "path": path, "ua": self.headers.get("User-Agent"),
                   "feature": None, "body_keys": None}
            ua = self.headers.get("User-Agent") or ""
            if ua.startswith("Python-urllib"):
                rec.update(status=403, valid=False, error="error code: 1010", rule_source="[measured] Cloudflare blocks urllib default UA")
                sim.log(rec)
                return self._send(403, "error code: 1010", "text/plain; charset=UTF-8")
            if not self._auth():
                rec.update(status=401, valid=False, error="missing key")
                sim.log(rec)
                return self._send(401, {"error": {"message": "Invalid API key.", "type": "authentication_error", "param": None, "code": "invalid_api_key"}})
            surface = "responses" if path in ("/v1/responses",) else "messages" if path.startswith("/v1/messages") else None
            if surface is None or self.command != "POST":
                rec.update(status=404 if self.command == "POST" else 405, valid=False, error="unsupported route", rule_source="[measured]")
                sim.log(rec)
                if self.command != "POST":
                    return self._send(405, "Method Not Allowed\n", "text/plain; charset=utf-8")
                return self._send(404, "404 page not found\n", "text/plain; charset=utf-8")
            try:
                b = json.loads(raw or b"{}")
            except ValueError:
                return self._err(SimError(400, "invalid_json", "Request body is not valid JSON", "[upstream]"), surface, rec)
            rec["body_keys"] = sorted(b.keys()) if isinstance(b, dict) else None
            rec["model"] = b.get("model") if isinstance(b, dict) else None
            rec["feature"] = (((b.get("metadata") or {}) if isinstance(b, dict) else {}).get("feature") if isinstance((b or {}).get("metadata"), dict) else None) \
                or self.headers.get("x-router-migrate-feature")
            rec["client"] = self.headers.get("x-router-migrate-client")
            rec["stream"] = bool(b.get("stream")) if isinstance(b, dict) else False
            rec["request"] = b
            try:
                if surface == "responses":
                    m = sim.validate_responses(b)
                    resp = sim.responses_result(b, m)
                    rec.update(status=200, valid=True, provider=resp["provider"])
                    sim.log(rec)
                    if b.get("stream"):
                        return self._sse(sim.responses_events(resp), resp["provider"])
                    return self._send(200, resp, extra={"x-router-provider": resp["provider"]})
                if path == "/v1/messages/count_tokens":
                    if not self.headers.get("anthropic-version"):
                        raise SimError(400, "invalid_request_error", "anthropic-version header is required.", "[measured]")
                    m = sim.model_or_404(b.get("model"), "messages")
                    rec.update(status=200, valid=True)
                    sim.log(rec)
                    return self._send(200, {"input_tokens": max(1, len(json.dumps(b.get("messages"))) // 4)},
                                      extra={"x-router-token-count-source": "provider" if m.get("owned_by") == "anthropic" else "estimated"})
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                m = sim.validate_messages(b, hdrs)
                msg = sim.messages_result(b, m)
                rec.update(status=200, valid=True, provider=msg["provider"])
                sim.log(rec)
                if b.get("stream"):
                    return self._sse(sim.messages_events(msg), msg["provider"])
                return self._send(200, msg, extra={"x-router-provider": msg["provider"]})
            except SimError as e:
                return self._err(e, surface, rec)
    return H


def serve(port=0, log_path=None):
    """Start router-sim on 127.0.0.1:<port> in a daemon thread. Returns (server, sim, base_url)."""
    sim = Sim(log_path=log_path)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sim))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sim, "http://127.0.0.1:%d" % srv.server_address[1]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="router-sim", description="Local Router stand-in enforcing measured behavior.")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--log", default=os.environ.get("ROUTER_SIM_LOG", "router-sim.jsonl"))
    a = ap.parse_args(argv)
    srv, sim, base = serve(a.port, a.log)
    print("router-sim listening on %s (log: %s)" % (base, a.log), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
