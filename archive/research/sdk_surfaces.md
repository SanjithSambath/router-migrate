# SDK surfaces for the in-process Chat Completions -> Router Responses adapter

Research date 2026-10-13. Sources read (never modified):

- `PY` = `/Users/sanjithsambath/code/router-live-check/.venv/lib/python3.9/site-packages`
  (openai 2.48.0, anthropic 0.125.0, httpx 0.28.1, langchain-openai 0.3.35, langchain-core 0.3.86,
  langchain-anthropic 0.3.22, llama-index-llms-openai 0.6.26, pydantic-ai-slim 0.8.1)
- `NM` = `/Users/sanjithsambath/code/router-live-check/node_modules`
  (openai 7.32.0, @anthropic-ai/sdk 0.133.0, ai 7.0.138, @ai-sdk/openai 4.0.92, @langchain/openai 1.6.2)
- Older versions: GitHub raw sources (tags cited inline).
- Router ground truth: `~/code/router-live-check/results.md` (v1), `results_v2_annotated.md`, `raw_v2/**`, `models.json`.
  Where this doc says "measured", it is from those files. Anything else is from SDK source or OpenAI API knowledge and
  is labelled "unverified on Router".

Design recap: the layer intercepts `POST {base}/chat/completions` (base = Router `https://api.router.com/v1`), rewrites the
JSON into `POST {base}/responses`, and rewrites the result (JSON or SSE) back into `chat.completion` /
`chat.completion.chunk`. Every other request (`/responses`, `/models`, `/messages`) passes through untouched.

---

## A. Request mapping: Chat Completions -> Responses

Param list is from `PY/openai/types/chat/completion_create_params.py:43-374`; Responses params from
`PY/openai/types/responses/response_create_params.py:43-313`. LangChain already ships a chat->responses converter we
can crib from: `PY/langchain_openai/chat_models/base.py:3549` (`_construct_responses_api_payload`), `:3650`
(`_convert_chat_completions_blocks_to_responses`), `:3751` (`_construct_responses_api_input`).

### A.1 Top-level fields

| Chat field | Responses mapping | Notes / why |
|---|---|---|
| `model` | `model`, after model-id normalization (G.5) | Dated ids 404 on Router (measured). |
| `messages` | `input` (list of items, A.2) | `messages` on /responses = 400 "moved to 'input'" (measured). |
| `max_completion_tokens` / `max_tokens` | `max_output_tokens` (prefer `max_completion_tokens` if both) | `max_tokens` on /responses = 400 unknown_parameter (measured). Clamp to >= 16 (OpenAI Responses minimum; unverified on Router) and <= catalog `limits.max_output_tokens`. See G.2 for reasoning models. |
| `temperature`, `top_p` | same names | Drop when catalog `capabilities.temperature == false` (400 measured on gpt-5-nano). For Anthropic models, if both are set, drop `top_p` (newer Claude rejects both together; unverified on Router). |
| `tools` `[{type:"function", function:{name, description, parameters, strict}}]` | `[{type:"function", name, description, parameters, strict}]` (flatten) | **Set `strict` explicitly.** Responses defaults function tools to strict=true; Chat defaults to false. Copy `strict` if present, else send `strict: false`, or a non-strict schema (optional props, no `additionalProperties:false`) gets a 400. `parameters` may be omitted in Chat; send `{"type":"object","properties":{}}`. Custom tools `{type:"custom", custom:{name, description, format}}` -> `{type:"custom", name, description, format}`. |
| legacy `functions: [{name, description, parameters}]` | `tools: [{type:"function", ...function, strict:false}]` | Remember it was legacy so the response comes back as `message.function_call` with `finish_reason:"function_call"` (B.3). |
| `tool_choice` `"none"|"auto"|"required"` | same string | `required` measured 200 on gpt-4o-mini, Claude, gpt-oss. |
| `tool_choice` `{type:"function", function:{name}}` | `{type:"function", name}` | Same as LangChain `base.py:3593-3603`. |
| `tool_choice` `{type:"allowed_tools", allowed_tools:{mode, tools:[{type:"function", function:{name}}]}}` | `{type:"allowed_tools", mode, tools:[{type:"function", name}]}` | Rare. |
| `tool_choice` `{type:"custom", custom:{name}}` | `{type:"custom", name}` | Rare. |
| legacy `function_call` `"none"|"auto"|{name}` | `tool_choice` `"none"|"auto"|{type:"function", name}` | |
| `parallel_tool_calls` | same | Measured 200. |
| `response_format {type:"text"}` | omit (or `text.format {type:"text"}`) | |
| `response_format {type:"json_object"}` | `text: {format: {type:"json_object"}}` | `response_format` on /responses = 400 "moved to text.format" (measured). OpenAI still requires the word "JSON" in the input. |
| `response_format {type:"json_schema", json_schema:{name, schema, strict, description}}` | `text: {format: {type:"json_schema", name, schema, strict, description}}` (flatten `json_schema`) | `PY/openai/types/responses/response_format_text_json_schema_config_param.py:11-40`. `client.chat.completions.parse()` sends this shape with `strict:true`. Catalog `structured_outputs:false` on gpt-4-turbo, gpt-4, gpt-3.5-turbo, gpt-5.4-pro, gpt-5.2-pro and Bedrock Claude: flag. |
| `verbosity` | `text.verbosity` | Merge with `text.format`. |
| `stream` | `stream` | |
| `stream_options {include_usage}` | **do not send** | Responses' `stream_options` only has `include_obfuscation` (`response_create_params.py:393-396`); `response.completed` always carries usage. Keep `include_usage` locally to decide whether to emit the usage chunk (C.3). |
| `reasoning_effort` | `reasoning: {effort}` | Drop when catalog `reasoning.supported` is false (400 on non-reasoning models, per SPEC). Drop or clamp when the effort isn't in the model's `reasoning.efforts` list. On Claude, effort needs `max_output_tokens >= ~1100` (measured 400 "budget_tokens >= 1024" at 400 tokens; 200 at 2000). |
| `user` | `user` (or `safety_identifier`) | `user` measured 200. |
| `safety_identifier`, `prompt_cache_key`, `prompt_cache_retention` | same | For Claude via Responses, **add `prompt_cache_key`** when missing (e.g. a hash of model + system + tools). Without it, caching doesn't happen (measured: 2 calls, 0 cached; with key, 10362 cached). |
| `metadata` | `metadata` | Responses metadata doesn't need `store`. Router uses it for labels; SPEC adds `{"feature": "<file>:<function>"}`. |
| `store` | **always `store: false`** | `store:true` = 400 on Router (measured). Responses defaults store to true, so send false explicitly (omitting was 200 in the measurement, but explicit false is safer). |
| `service_tier` | same | When unset, send `service_tier:"default"` on latency-sensitive paths: plain model calls get flex by default (measured), and Chat callers expect standard latency. Never send `allow_flex_tier`. |
| `seed` | **drop** | 400 unknown_parameter (measured). Loses best-effort determinism; log a warning. |
| `n` | **drop when 1; fan out when >1** | 400 unknown_parameter (measured). For `n>1` non-stream: issue n parallel requests, merge `choices[i].index=i`, sum usage. For `n>1` + stream: raise a clear error. Interleaving n streams isn't worth it. |
| `logprobs` / `top_logprobs` | `logprobs:true, top_logprobs:k` -> `top_logprobs:k` + `include:["message.output_text.logprobs"]`; map `output_text.logprobs` back to `choices[0].logprobs.content` | `logprobs` = 400 (measured); `top_logprobs` alone = 200 (measured). `include` round-trip unverified on Router. Fallback: drop, return `logprobs: null`. |
| `stop` (str or list, <= 4) | **strip, emulate client-side** (G.3) | 501 not_implemented on OpenAI models, honored on Claude (measured). |
| `frequency_penalty` | pass through | Measured 200 on gpt-4o-mini; Router echoes `frequency_penalty` in the response. Unverified on other providers. |
| `presence_penalty` | pass through | Unverified. The response echoes `presence_penalty`, so it's probably accepted. Use the generic retry (A.4). |
| `logit_bias` | **drop** | No Responses equivalent. Warn. |
| `prediction` | **drop** | Latency-only optimization, no Responses equivalent. Output is unaffected. |
| `modalities:["text","audio"]`, `audio` | **refuse** (raise a clear error, or don't route) | Responses has no audio output. Audio input also measured 400 "Audio input is not available". |
| `web_search_options {search_context_size, user_location}` | `tools += [{type:"web_search", search_context_size, user_location:{type:"approximate", ...approximate}}]` | web_search measured 200 on gpt-4o-mini, Claude, gpt-oss. Map `url_citation` annotations back (B.2). Chat web search ran on `gpt-4o-search-preview`-style models, which aren't in the catalog: map the model too. |
| `moderation` (new chat param) | drop | Not tested. |
| `prompt_cache_options` | pass | Exists on both. |
| any other / unknown key | drop + log | Router 400s on unknown params (measured). |
| (none) | `truncation`, `include`, `max_tool_calls` | Don't add any by default, except `include:["reasoning.encrypted_content"]` for the reasoning-replay trick (B.4). |

### A.2 `messages[]` -> `input[]`

Chat message param types: `PY/openai/types/chat/chat_completion_*_message_param.py`. Responses input item types:
`PY/openai/types/responses/easy_input_message_param.py:22-42`, `response_input_param.py:167-182`
(`function_call_output`), `response_input_image_param.py:27-39`, `response_input_file_param.py:24-45`,
`response_input_audio_param.py:11-23`.

| Chat message | Responses item(s) |
|---|---|
| `{role:"system", content:str}` | `{type:"message", role:"system", content:str}`. Alternative: lift *leading* system messages into `instructions` (joined with `\n\n`). Both measured 200 on gpt-4o-mini and Claude, including a mid-conversation system message on Claude. **Recommendation:** keep them as input items. That preserves order and is identical to what LangChain does (`base.py:3864-3886`). |
| `{role:"developer", ...}` | `{type:"message", role:"developer", ...}` (measured 200 on both). |
| `{role:"user", content:str}` | `{type:"message", role:"user", content:str}` |
| user content part `{type:"text", text}` | `{type:"input_text", text}` |
| `{type:"image_url", image_url:{url, detail?}}` | `{type:"input_image", image_url:url, detail: detail or "auto"}`. `detail` is `Required` in the Responses type (`response_input_image_param.py:27`), so always set it. Images on text-only models (gpt-oss-120b) = 400 (measured). Check catalog `modalities.input`. |
| `{type:"input_audio", input_audio:{data, format}}` | `{type:"input_audio", input_audio:{data, format}}` exists in the type, but Router returned 400 "Audio input is not available" (measured). Treat as unsupported: raise. |
| `{type:"file", file:{file_data, file_id, filename}}` | `{type:"input_file", file_data, file_id, filename}`. `file_id` refers to the OpenAI Files API, which isn't on Router (files 404). Flag `file_id`; `file_data` (data URL) is fine. |
| `name` on any message | **drop** (Responses has no `name`; LangChain drops it too, `base.py:3757-3759`). |
| `{role:"assistant", content:str}` | `{type:"message", role:"assistant", content:[{type:"output_text", text, annotations:[]}]}` (or `content:str` via EasyInputMessage). **Skip empty strings.** Assistant turns with `content:""` + `tool_calls` are common, and Anthropic rejects empty text blocks (unverified via Router, but cheap to avoid). |
| assistant content parts `text` / `refusal` | `output_text` / `{type:"refusal", refusal}` |
| assistant `refusal: str` | `{type:"refusal", refusal}` in the assistant message content |
| assistant `tool_calls:[{id, type:"function", function:{name, arguments}}]` | one item per call, *after* the assistant message: `{type:"function_call", call_id:id, name, arguments}`. Same as LangChain `base.py:3847-3862`. Don't send `id` (`fc_...` item ids). Cross-provider call ids (`toolu_...`, `call_...`) were measured fine in replay. |
| assistant `tool_calls[{type:"custom", custom:{name, input}}]` | `{type:"custom_tool_call", call_id, name, input}` |
| assistant legacy `function_call:{name, arguments}` | `{type:"function_call", call_id:<synthesized>, name, arguments}`. Synthesize a deterministic id, e.g. `call_fn_<msgindex>`. |
| `{role:"tool", tool_call_id, content}` | `{type:"function_call_output", call_id:tool_call_id, output}`. `output` is a str: join text parts with `""` if content is a list. Use `custom_tool_call_output` if the referenced call was custom. |
| legacy `{role:"function", name, content}` | `{type:"function_call_output", call_id:<id synthesized for the preceding function_call with that name>, output: content or ""}` |
| assistant `audio:{id}` | drop + warn |

Ordering rule: emit items in message order. For an assistant message, emit the message item (if non-empty) and then
its function_call items, so every `function_call` precedes its `function_call_output`. Full-history replay works on
Router (measured, including cross-model replay). `previous_response_id` and `conversation` must never be sent
(400; SPEC).

### A.3 Minimal reference implementation (Python, pure functions, also portable to TS)

```python
DROP = {"seed", "logit_bias", "prediction", "moderation", "stream_options", "logprobs", "n",
        "functions", "function_call", "modalities", "audio", "web_search_options", "stop",
        "messages", "max_tokens", "max_completion_tokens", "response_format", "reasoning_effort",
        "verbosity", "tools", "tool_choice"}
PASS = {"model", "temperature", "top_p", "parallel_tool_calls", "user", "metadata", "service_tier",
        "safety_identifier", "prompt_cache_key", "prompt_cache_retention", "prompt_cache_options",
        "frequency_penalty", "presence_penalty", "stream", "top_logprobs"}

def chat_to_responses(body: dict, caps: dict) -> tuple[dict, dict]:
    """returns (responses_body, ctx). ctx carries what the response side needs."""
    ctx = {"model": body["model"], "stream": bool(body.get("stream")),
           "include_usage": bool((body.get("stream_options") or {}).get("include_usage")),
           "stop": _as_list(body.get("stop")), "legacy_functions": "functions" in body,
           "custom_tools": set()}
    out = {k: v for k, v in body.items() if k in PASS}
    out["model"] = normalize_model(body["model"])            # G.5
    out["store"] = False
    out["input"] = convert_messages(body["messages"], ctx)  # A.2
    mot = body.get("max_completion_tokens") or body.get("max_tokens")
    if mot is not None:
        out["max_output_tokens"] = max(16, min(mot, caps["max_output_tokens"]))
    tools = [_tool(t, ctx) for t in body.get("tools", [])] + \
            [{"type": "function", "strict": False, **f} for f in body.get("functions", [])]
    if body.get("web_search_options") is not None:
        tools.append({"type": "web_search", **_ws(body["web_search_options"])})
    if tools:
        out["tools"] = tools
    tc = body.get("tool_choice", body.get("function_call"))
    if tc is not None:
        out["tool_choice"] = _tool_choice(tc)
    text = {}
    rf = body.get("response_format")
    if rf and rf.get("type") == "json_object":
        text["format"] = {"type": "json_object"}
    elif rf and rf.get("type") == "json_schema":
        text["format"] = {"type": "json_schema", **rf["json_schema"]}
    if body.get("verbosity"):
        text["verbosity"] = body["verbosity"]
    if text:
        out["text"] = text
    if body.get("reasoning_effort") and caps["reasoning_supported"]:
        out["reasoning"] = {"effort": body["reasoning_effort"]}
    if not caps["temperature"]:
        out.pop("temperature", None); out.pop("top_p", None)
    if body.get("logprobs"):
        out["include"] = ["message.output_text.logprobs"]
        out.setdefault("top_logprobs", body.get("top_logprobs") or 0)
    if ctx["stop"] and caps["provider"] == "anthropic":
        out["stop"] = ctx["stop"]; ctx["stop"] = []          # Router honors stop on Claude (measured)
    out.setdefault("service_tier", "default")
    return out, ctx

def _tool(t, ctx):
    if t["type"] == "function":
        f = t["function"]
        return {"type": "function", "name": f["name"], "description": f.get("description"),
                "parameters": f.get("parameters") or {"type": "object", "properties": {}},
                "strict": bool(f.get("strict", False))}
    if t["type"] == "custom":
        ctx["custom_tools"].add(t["custom"]["name"]); return {"type": "custom", **t["custom"]}
    return t
```

### A.4 Generic safety net

Router errors are OpenAI-shaped: `{"error":{"message","type","param","code"}}` (`raw_v2/raw/007_conversation_param.json`).
On a 400 with `code in {"unknown_parameter","unsupported_parameter"}` and a `param` the adapter added or passed through
(not `model`/`input`), strip that param and retry once. Log it so the report can say which knobs were lost. Never
retry 501 `not_implemented` for stop on the same model without stripping `stop`.

---

## B. Response mapping: Responses -> ChatCompletion

### B.1 What the SDK models require

openai-python `ChatCompletion` (`PY/openai/types/chat/chat_completion.py:191-246`): required `id: str`,
`choices: List[Choice]`, `created: int`, `model: str`, `object: Literal["chat.completion"]`. Optional:
`service_tier`, `system_fingerprint`, `usage`, `moderation`. `Choice` (`:38-56`): required
`finish_reason: Literal["stop","length","tool_calls","content_filter","function_call"]`, `index: int`,
`message`; optional `logprobs`. `ChatCompletionMessage` (`chat_completion_message.py:57-89`): required
`role: Literal["assistant"]`; optional `content`, `refusal`, `annotations`, `audio`, `function_call`,
`tool_calls`. Function tool call (`chat_completion_message_function_tool_call.py:25-34`): `id`,
`function{arguments:str, name:str}`, `type:"function"`, all required. `CompletionUsage` (`completion_usage.py:47-62`):
required `completion_tokens`, `prompt_tokens`, `total_tokens`; optional `completion_tokens_details{accepted_prediction_tokens,
audio_tokens, reasoning_tokens, rejected_prediction_tokens}` and `prompt_tokens_details{audio_tokens, cache_write_tokens,
cached_tokens}`.

openai-python does **not** validate by default. `_process_response_data` uses `construct_type` unless
`_strict_response_validation=True` (`PY/openai/_base_client.py:676-696`). So a missing field doesn't raise at parse
time; it fails later as `None`/AttributeError in user code. Emit every required field anyway. Content-type: if the
header isn't `*json`, the SDK still tries `response.json()` for BaseModel targets (`PY/openai/_response.py:242-273`),
but set `content-type: application/json`.

Consumers that read raw dicts:
- LangChain `_create_chat_result` raises if `response_dict.get("error")` is truthy (`base.py:1274-1275`). So **don't
  include an `error` key** (Router's Response has `"error": null`; don't copy it). It also reads `choices`,
  `message`, `finish_reason`, `usage`, `model`, `system_fingerprint`, `id`.
- `client.chat.completions.parse()` raises `LengthFinishReasonError` on `finish_reason=="length"` and
  `ContentFilterFinishReasonError` on `"content_filter"` (`PY/openai/lib/_parsing/_completions.py:99-103`), so the
  finish_reason mapping changes exception behavior.
- @ai-sdk/openai chat response schema (`NM/@ai-sdk/openai/dist/index.js:702`) is lenient (`nullish` almost
  everywhere).

### B.2 Mapping

```python
def responses_to_chat(r: dict, ctx: dict) -> dict:
    text, refusal, calls, annotations = [], [], [], []
    for item in r.get("output", []):
        t = item.get("type")
        if t == "message":
            for part in item.get("content", []):
                if part["type"] == "output_text":
                    base = sum(map(len, text))
                    text.append(part["text"])
                    for a in part.get("annotations") or []:
                        if a.get("type") == "url_citation":
                            annotations.append({"type": "url_citation", "url_citation": {
                                "start_index": base + a["start_index"], "end_index": base + a["end_index"],
                                "title": a.get("title", ""), "url": a["url"]}})
                elif part["type"] == "refusal":
                    refusal.append(part["refusal"])
        elif t == "function_call":
            calls.append({"id": item["call_id"], "type": "function",
                          "function": {"name": item["name"], "arguments": item.get("arguments") or ""}})
        elif t == "custom_tool_call":
            calls.append({"id": item["call_id"], "type": "custom",
                          "custom": {"name": item["name"], "input": item.get("input", "")}})
        # "reasoning", "web_search_call", "code_interpreter_call", ...: not representable; see B.4
    content = "".join(text) if text else None
    content, stopped = apply_stop(content, ctx["stop"])              # G.3
    msg = {"role": "assistant", "content": content, "refusal": "".join(refusal) or None}
    if annotations: msg["annotations"] = annotations
    if calls and ctx["legacy_functions"]:
        msg["function_call"] = calls[0]["function"]; finish = "function_call"
    elif calls:
        msg["tool_calls"] = calls; finish = "tool_calls"
    else:
        finish = finish_reason(r, stopped)
    u = r.get("usage") or {}
    usage = {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
             "total_tokens": u.get("total_tokens", u.get("input_tokens", 0) + u.get("output_tokens", 0)),
             "prompt_tokens_details": {"cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
                                       "cache_write_tokens": (u.get("input_tokens_details") or {}).get("cache_write_tokens", 0),
                                       "audio_tokens": 0},
             "completion_tokens_details": {"reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                                           "audio_tokens": 0, "accepted_prediction_tokens": 0,
                                           "rejected_prediction_tokens": 0}}
    if "cost" in u: usage["cost"] = u["cost"]        # Router extra; pydantic models keep extras
    return {"id": r["id"], "object": "chat.completion", "created": int(r.get("created_at") or time.time()),
            "model": ctx["model"],                    # the alias the caller sent, NOT r["model"] (dated, 404s if reused)
            "choices": [{"index": 0, "message": msg, "finish_reason": finish, "logprobs": map_logprobs(r)}],
            "usage": usage, "service_tier": r.get("service_tier"), "system_fingerprint": None}

def finish_reason(r, stopped):
    if stopped: return "stop"
    if r.get("status") == "incomplete":
        reason = (r.get("incomplete_details") or {}).get("reason")
        return "content_filter" if reason == "content_filter" else "length"
    return "stop"
```

Notes:
- `id`: Router ids look like `resp_...` or `resp_msg_...` (`raw_v2/raw/086_*.json`). Pass through, or prefix
  `chatcmpl-`. Nothing in the SDKs checks the prefix.
- `model`: return the caller's requested alias. Router echoes dated ids (`gpt-4o-mini-2024-07-18`,
  `claude-haiku-4-5-20251001`) that 404 if code feeds `completion.model` back in (measured).
- `tool_calls[].id` must be the **`call_id`** (`call_...` / `toolu_...`), not the item `id` (`fc_...`). The next
  turn's `tool_call_id` must match the `call_id` (`raw_v2/raw/018_*.json`, `020_*.json`).
- When there are no tool calls, leave `tool_calls` absent/None, not `[]` (OpenAI behavior).
- `status:"failed"` (non-stream 200 with `error` set) -> return an HTTP 500 with body
  `{"error":{"message": r["error"]["message"], "type":"api_error", "code": r["error"]["code"]}}`. The SDK then raises
  `InternalServerError` (and retries per its policy).
- Upstream non-2xx: **pass status, headers and body through unchanged**. The SDK maps the status to
  `BadRequestError`/`NotFoundError`/etc., and Router's error JSON is already OpenAI-shaped. Plain-text 504s
  (measured) also pass through; the SDK builds a generic `APIStatusError`.
- Finish reasons: when there are tool calls and `status=="incomplete"` (truncated JSON args), prefer `"length"` so
  callers don't execute broken calls. `"content_filter"` comes from `incomplete_details.reason=="content_filter"`.
  Refusal-only output keeps `"stop"` (OpenAI behavior).

### B.3 Legacy `functions` callers

If the request used `functions`, return `message.function_call = {name, arguments}` (first call only; legacy has no
parallel calls; also send `parallel_tool_calls:false`) and `finish_reason:"function_call"`. In streaming, emit
`delta.function_call` instead of `delta.tool_calls`.

### B.4 Reasoning items (gpt-5 / o-series / Claude thinking) [risk]

Chat Completions has no slot for reasoning items, so they're dropped. Catalog says OpenAI reasoning models need
`continuation.required: true` with `request_include: ["reasoning.encrypted_content"]` (`models.json`, e.g. gpt-5,
o3-mini). Without replay, multi-turn tool loops on these models lose their reasoning context; OpenAI degrades
quality. Whether Router *rejects* such a loop is unmeasured: **test in router-sim and once live.** Optional
mitigation that keeps the adapter stateless across processes but stateful in-process: for reasoning models, add
`include:["reasoning.encrypted_content"]`. Cache each `reasoning` item in an LRU keyed by the `call_id`s of the
`function_call` items that follow it. On the next request, when the history contains those `call_id`s, re-insert the
cached reasoning item just before the first matching `function_call` item. Measured: replaying gpt-5-nano reasoning
items to the same model, to Claude and to gpt-4o-mini all returned 200. Optional: expose summary text as non-standard
`message.reasoning_content` (some OpenAI-compatible consumers read it; openai-python keeps unknown fields).

---

## C. Streaming

### C.1 How the SDKs parse SSE

**openai-python 2.48**: `PY/openai/_streaming.py`.
- Byte chunking: `_iter_chunks` splits on `\r\r`, `\n\n` or `\r\n\r\n` (`:305-315`). Lines go through a WHATWG-style
  decoder: `event:`, `data:` (multi-line data joined with `\n`), `id:`, `retry:`, and `:` comments are ignored
  (`:339-385`).
- `Stream.__stream__` (`:55-110`): `if sse.data.startswith("[DONE]"): break`. Events whose `event:` starts with
  `thread.` get special-cased. Every other event is `json.loads`ed; **if the JSON has a truthy `error` key it raises
  `APIError`** (`:86-99`), otherwise the object is yielded as `ChatCompletionChunk` via `construct_type`.
- Whether a response is a stream depends on the request's `stream=True` (the `stream_cls` passed in), **not** on
  content-type (`PY/openai/_response.py:53,76,142-177`). Still send `content-type: text/event-stream`.
- Version history (GitHub tags): in openai-python **1.0-1.65**, only events with **no** `event:` field took the
  normal path. Anything with `event:` set was yielded as `{"data":..., "event":...}`, which corrupts
  `ChatCompletionChunk`. 1.66 also accepted `response.*`; 1.99+ wraps only `thread.*`. openai-node behaves the same
  way: v4.x and v5.0-5.22 parse only `event === null` or `response.*`/`transcript.*` events, and `event: error`
  throws in every version. **So emit no `event:` lines.** Plain `data: {json}\n\n` is the canonical Chat Completions
  wire format and parses in every version of both SDKs.
- openai-node v4 does not flush a trailing event that lacks a blank line, so terminate *every* frame with `\n\n`.
  openai-node v7 checks `sse.data === '[DONE]'` exactly (`NM/openai/src/core/streaming.ts:132-135`); v4 uses
  `startsWith`. Emit exactly `data: [DONE]\n\n`.
- Anthropic SDKs are the opposite: their stream parser dispatches on `event:` names
  (`PY/anthropic/_streaming.py:94-108`). Irrelevant here because `/v1/messages` passes through untouched, but don't
  reuse the chat SSE writer for Messages.

**Wire format to emit** (all SDKs, all versions):

```
data: {"id":"resp_x","object":"chat.completion.chunk","created":1791864029,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}]}\n
\n
data: {...,"choices":[{"index":0,"delta":{"content":"Po"},"finish_reason":null}]}\n
\n
...
data: {...,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n
\n
data: {...,"choices":[],"usage":{"prompt_tokens":15,"completion_tokens":4,"total_tokens":19}}\n      <- only if include_usage
\n
data: [DONE]\n
\n
```

### C.2 Consumer constraints on chunk shape (these decide the design)

1. **`role` only on the first delta.** `openai.lib.streaming._deltas.accumulate_delta` concatenates every string
   key except `index`/`type` (`PY/openai/lib/streaming/_deltas.py:6-33`). A repeated `role` becomes
   `"assistantassistant"`.
2. **Tool-call `id` and `function.name` only on the first delta for that tool call.** Later deltas carry only
   `{index, function:{arguments}}`. Three consumers concatenate them: openai-python's accumulator (same file),
   LlamaIndex (`PY/llama_index/llms/openai/utils.py:1062-1072`: `t.id += tc_delta.id`,
   `t.function.name += tc_delta.function.name`) and langchain-core's chunk merge.
3. **At most one tool call per chunk.** LlamaIndex reads only `tool_calls_delta[0]`
   (`PY/llama_index/llms/openai/utils.py:1039`).
4. **Every tool-call delta must have `index` and a `function` object.** @ai-sdk/openai's chunk schema requires
   `index: number` and a non-nullish `function` (`NM/@ai-sdk/openai/dist/index.js:755-772`). Older @ai-sdk/openai
   (0.x/1.x) also threw `InvalidResponseDataError` if the first delta for an index lacked `id` or `function.name`.
   The OpenAI-style first delta satisfies both.
5. **Usage chunk has `choices: []`.** LangChain handles `len(choices)==0` as usage-only (`base.py:918-931`).
   openai-python's `chat.completions.stream()` handles an empty-choices chunk. Only emit it when
   `stream_options.include_usage` was requested; some older consumers index `choices[0]` unconditionally.
6. **Errors mid-stream:** emit `data: {"error":{"message":..., "type":..., "code":...}}\n\n`, then end the stream. openai-python
   raises `APIError` (`_streaming.py:86-99`); openai-node does the same (E). Don't emit `[DONE]` after an error.

### C.3 Event -> chunk state machine

Router's stream is standard Responses SSE (captured event types: `response.created, response.in_progress,
response.output_item.added, response.content_part.added, response.output_text.delta, response.output_text.done,
response.content_part.done, response.output_item.done, response.completed`; `raw_v2/raw/071_*.json`, `086_*.json`).
Router adds a `provider` key to events and `usage.cost`. Key on `data["type"]`, not on the `event:` line.

```python
class ChunkTranslator:
    def __init__(self, ctx):
        self.ctx, self.started, self.tool_idx, self.item_to_idx = ctx, False, 0, {}
        self.args_seen, self.saw_tool, self.stopbuf = {}, False, StopBuffer(ctx["stop"])
        self.id, self.created, self.done = None, None, False

    def chunk(self, delta, finish=None, usage=None, choices=True):
        c = {"id": self.id, "object": "chat.completion.chunk", "created": self.created,
             "model": self.ctx["model"], "system_fingerprint": None,
             "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}] if choices else []}
        if usage is not None: c["usage"] = usage
        return b"data: " + json.dumps(c, separators=(",", ":")).encode() + b"\n\n"

    def feed(self, ev: dict):                         # yields bytes
        t = ev.get("type")
        if t == "response.created":
            r = ev["response"]; self.id, self.created = r["id"], int(r["created_at"])
            self.started = True
            yield self.chunk({"role": "assistant", "content": "", "refusal": None})
        elif t == "response.output_text.delta":
            out = self.stopbuf.push(ev["delta"])          # G.3 holds back a possible stop-prefix
            if out: yield self.chunk({"content": out})
            if self.stopbuf.hit: yield from self.finish("stop", None)   # caller sees .done and closes upstream
        elif t == "response.refusal.delta":
            yield self.chunk({"refusal": ev["delta"]})
        elif t == "response.output_item.added" and ev["item"]["type"] == "function_call":
            it = ev["item"]; i = self.tool_idx; self.tool_idx += 1; self.saw_tool = True
            self.item_to_idx[it["id"]] = i; self.args_seen[it["id"]] = it.get("arguments") or ""
            yield self.chunk({"tool_calls": [{"index": i, "id": it.get("call_id") or it["id"], "type": "function",
                                              "function": {"name": it["name"], "arguments": self.args_seen[it["id"]]}}]})
        elif t == "response.function_call_arguments.delta":
            i = self.item_to_idx[ev["item_id"]]; self.args_seen[ev["item_id"]] += ev["delta"]
            yield self.chunk({"tool_calls": [{"index": i, "function": {"arguments": ev["delta"]}}]})
        elif t in ("response.function_call_arguments.done", "response.output_item.done"):
            # some upstream adapters send no deltas, only the final args; emit the missing suffix
            item_id = ev.get("item_id") or ev.get("item", {}).get("id")
            final = ev.get("arguments") or ev.get("item", {}).get("arguments")
            if item_id in self.item_to_idx and final:
                seen = self.args_seen[item_id]
                if final.startswith(seen) and len(final) > len(seen):
                    yield self.chunk({"tool_calls": [{"index": self.item_to_idx[item_id],
                                                       "function": {"arguments": final[len(seen):]}}]})
                    self.args_seen[item_id] = final
        elif t in ("response.completed", "response.incomplete"):
            tail = self.stopbuf.flush()
            if tail: yield self.chunk({"content": tail})
            r = ev["response"]
            fin = "tool_calls" if self.saw_tool else finish_reason(r, False)
            if self.saw_tool and r.get("status") == "incomplete": fin = "length"
            yield from self.finish(fin, r.get("usage"))
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev
            yield b"data: " + json.dumps({"error": {"message": err.get("message") or "stream failed",
                  "type": "api_error", "code": err.get("code")}}).encode() + b"\n\n"
            self.done = True
        # ignore: response.in_progress, content_part.*, output_text.done, reasoning_*.delta,
        # web_search_call.*, output_text.annotation.added (or map to delta.annotations), etc.

    def finish(self, fin, usage):
        yield self.chunk({}, finish=fin)
        if self.ctx["include_usage"] and usage:
            yield self.chunk(None, usage=map_usage(usage), choices=False)
        yield b"data: [DONE]\n\n"
        self.done = True

    def eof(self):                                    # upstream ended without completed/failed
        if not self.done:
            yield b'data: {"error":{"message":"upstream stream ended early","type":"api_error"}}\n\n'
            self.done = True
```

Edge cases:
- `response.created` should always come first. If the first event is something else, synthesize id/created
  lazily before the first chunk.
- `error` events and `response.failed` can arrive after content was emitted. Emit the error frame; consumers raise.
- If the upstream stream ends without `response.completed` (connection drop), emit
  `{"error":{"message":"upstream stream ended early"}}` rather than a fake `finish_reason`.
- Legacy `functions`: emit `delta.function_call = {name, arguments:""}` on item.added, then
  `delta.function_call = {arguments: chunk}`, then `finish_reason:"function_call"`.
- Keep `x-request-id` / `x-router-provider` response headers. Drop `content-length` and `content-encoding` (D/E).

---

## D. Python: httpx transport and how each framework accepts it

### D.1 openai-python `http_client`

- `OpenAI(http_client: httpx.Client | None)` is at `PY/openai/_client.py:145`; `AsyncOpenAI` is at `:745`. Base URL
  resolution: `base_url` arg, then `OPENAI_BASE_URL`, then `https://api.openai.com/v1` (`_client.py:248-252`,
  `:848-852`).
- **Strict isinstance check:** sync requires `httpx.Client` (or `httpx2.Client`) and async requires
  `httpx.AsyncClient`, else `TypeError` (`PY/openai/_base_client.py:915-923`, `:1541-1549`; same check in 1.16 and
  1.30). An AsyncClient passed to `OpenAI` raises.
- **Timeout:** if no `timeout=` is given and the client's timeout differs from httpx's default (5s), the SDK adopts the
  client's timeout; else 600s (`_base_client.py:900-913`). It's sent per request and reaches the transport as
  `request.extensions["timeout"]`.
- **Use `DefaultHttpxClient(transport=...)` / `DefaultAsyncHttpxClient(transport=...)`**
  (`_base_client.py:854-870`, `:1449-1453`). They set `timeout=Timeout(600, connect=5)`, `limits=(1000, 100)` and
  `follow_redirects=True`. A plain `httpx.Client` gets a 5s timeout (overridden per request, as above), 100/20 limits
  and no redirect following. These helpers were **added in openai 1.17.0**. For <1.17, use
  `httpx.Client(transport=..., timeout=httpx.Timeout(600, connect=5), follow_redirects=True)`.
- **httpx 0.28 + openai <1.55.3:** the SDK's *own* default client passes `proxies=` and crashes on httpx 0.28.
  Supplying our own `http_client` sidesteps that (`http_client or SyncHttpxClientWrapper(...)`).
- The SDK builds absolute URLs (`_enforce_trailing_slash` `:412-415` + `_prepare_url` `:494-505`, resource path
  `"/chat/completions"`). The transport sees `https://api.router.com/v1/chat/completions` plus any `default_query`.
  Match on `request.url.path.endswith("/chat/completions")` and keep the query.
- `with_raw_response` / `with_streaming_response` send `X-Stainless-Raw-Response: raw|stream` on the wire. Harmless;
  the translation is the same.
- **openai 0.28.x (pre-1.0) has no httpx and no `http_client`.** It uses `requests`/`aiohttp`, `openai.api_base` and
  `openai.requestssession` (`api_requestor.py:81-85`). `openai.ChatCompletion.create` can't be intercepted with an
  httpx transport. Use a `requests.adapters.HTTPAdapter` mounted on a `requests.Session` assigned to
  `openai.requestssession` plus an aiohttp session for `acreate`, or (better) codemod to openai>=1. Mark 0.28 call
  sites "convert", not "shim".

### D.2 The transport (sync + async)

httpx facts (0.28.1):
- `BaseTransport.handle_request` / `AsyncBaseTransport.handle_async_request` (`PY/httpx/_transports/base.py:14-85`).
- The client asserts the returned `response.stream` is a `SyncByteStream` (sync) or `AsyncByteStream` (async)
  (`PY/httpx/_client.py:1016`, `:1717-1736`). `httpx.Response(content=<generator>)` gives an `IteratorByteStream` that
  is sync-only. Prefer explicit `stream=` subclasses with `close`/`aclose` that close the upstream response.
- `Request._prepare` uses `headers.setdefault` (`PY/httpx/_models.py:441-460`), so a **copied stale
  `Content-Length` wins** over the new body length. Drop `content-length` (and `transfer-encoding`) when rebuilding.
- `HTTPTransport` reads timeouts from `request.extensions` (`_transports/default.py:247`). **Pass
  `extensions=request.extensions`** or SDK timeouts are lost.
- `upstream.iter_bytes()` / `.read()` decode gzip/br. Drop `content-encoding` and `content-length` from the
  rewritten response, or the outer client double-decodes.
- `httpx.Client(transport=X)` **disables env proxies** (`allow_env_proxies = trust_env and transport is None`,
  `_client.py:685`). `verify`/`cert`/`limits`/`http2` only configure the default transport. Explicit `proxy=` mounts an
  `all://` transport that **takes priority over ours**, bypassing the adapter. Put proxy/verify on the inner
  transport: `RouterTransport(inner=httpx.HTTPTransport(proxy=os.environ.get("HTTPS_PROXY"), verify=...))`.

```python
import json, httpx

CHAT_SUFFIX = "/chat/completions"
_DROP_REQ = {"content-length", "transfer-encoding", "host"}
_DROP_RESP = {"content-length", "content-encoding", "transfer-encoding"}

def _rewrite_request(request: httpx.Request, body: bytes):
    chat = json.loads(body)
    rbody, ctx = chat_to_responses(chat, caps_for(chat["model"]))       # section A
    url = request.url.copy_with(path=request.url.path[: -len(CHAT_SUFFIX)] + "/responses")
    headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in _DROP_REQ]
    if ctx["stream"]:
        headers.append(("accept", "text/event-stream"))
    return httpx.Request("POST", url, headers=headers, content=json.dumps(rbody).encode(),
                         extensions=request.extensions), ctx

def _resp_headers(upstream: httpx.Response, ctype: str):
    h = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in _DROP_RESP | {"content-type"}]
    return h + [("content-type", ctype)]

class _SSEOut(httpx.SyncByteStream):
    def __init__(self, upstream: httpx.Response, ctx):
        self.up, self.tr = upstream, ChunkTranslator(ctx)              # section C
    def __iter__(self):
        try:
            for ev in iter_sse_json(self.up.iter_bytes()):              # parse Router SSE -> dicts
                yield from self.tr.feed(ev)
                if self.tr.done: break                                   # stop-sequence hit or [DONE] emitted
            else:
                yield from self.tr.eof()                                 # error frame if no completed event
        finally:
            self.up.close()
    def close(self): self.up.close()

class _ASSEOut(httpx.AsyncByteStream):
    def __init__(self, upstream, ctx): self.up, self.tr = upstream, ChunkTranslator(ctx)
    async def __aiter__(self):
        try:
            async for ev in aiter_sse_json(self.up.aiter_bytes()):
                for b in self.tr.feed(ev): yield b
                if self.tr.done: break
            else:
                for b in self.tr.eof(): yield b
        finally:
            await self.up.aclose()
    async def aclose(self): await self.up.aclose()

class RouterChatTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport | None = None):
        self.inner = inner or httpx.HTTPTransport()
    def handle_request(self, request):
        if request.method != "POST" or not request.url.path.endswith(CHAT_SUFFIX):
            return self.inner.handle_request(request)                     # /responses, /models, ... untouched
        new_req, ctx = _rewrite_request(request, request.read())
        up = self.inner.handle_request(new_req)
        if up.status_code >= 400:                                         # pass errors through verbatim
            up.read(); return httpx.Response(up.status_code, headers=_resp_headers(up, up.headers.get("content-type", "application/json")), content=up.content)
        if ctx["stream"]:
            return httpx.Response(up.status_code, headers=_resp_headers(up, "text/event-stream"), stream=_SSEOut(up, ctx))
        up.read()
        return httpx.Response(200, headers=_resp_headers(up, "application/json"),
                              content=json.dumps(responses_to_chat(json.loads(up.content), ctx)).encode())
    def close(self): self.inner.close()

class AsyncRouterChatTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()
    async def handle_async_request(self, request):
        if request.method != "POST" or not request.url.path.endswith(CHAT_SUFFIX):
            return await self.inner.handle_async_request(request)
        new_req, ctx = _rewrite_request(request, await request.aread())
        up = await self.inner.handle_async_request(new_req)
        if up.status_code >= 400:
            await up.aread(); return httpx.Response(up.status_code, headers=_resp_headers(up, up.headers.get("content-type", "application/json")), content=up.content)
        if ctx["stream"]:
            return httpx.Response(up.status_code, headers=_resp_headers(up, "text/event-stream"), stream=_ASSEOut(up, ctx))
        await up.aread()
        return httpx.Response(200, headers=_resp_headers(up, "application/json"),
                              content=json.dumps(responses_to_chat(json.loads(up.content), ctx)).encode())
    async def aclose(self): await self.inner.aclose()

# usage (the codemod emits this)
from openai import OpenAI, AsyncOpenAI, DefaultHttpxClient, DefaultAsyncHttpxClient
client = OpenAI(base_url="https://api.router.com/v1", api_key=os.environ["RAMP_ROUTER_API_KEY"],
                http_client=DefaultHttpxClient(transport=RouterChatTransport()))
aclient = AsyncOpenAI(base_url="https://api.router.com/v1", api_key=os.environ["RAMP_ROUTER_API_KEY"],
                      http_client=DefaultAsyncHttpxClient(transport=AsyncRouterChatTransport()))
```

Note: when the inner transport returns `up`, `up.read()` works without a client (the stream is the transport's raw
stream and httpx's `Response` decodes it). The `n>1` fan-out (A.1) is just N calls to `self.inner.handle_request`
with rebuilt requests, run in a thread pool or with `asyncio.gather`.

### D.3 Framework injection points

| Framework | How to inject | Source |
|---|---|---|
| **LangChain `ChatOpenAI`** (langchain-openai 0.1-0.3) | `ChatOpenAI(base_url=ROUTER, api_key=..., http_client=httpx.Client(transport=RouterChatTransport()), http_async_client=httpx.AsyncClient(transport=AsyncRouterChatTransport()))`. **Pass both:** with only `http_client`, async calls use a default client and bypass the adapter. `openai_proxy` + `http_client` raises `ValueError`. Better for >=0.3.9: `use_responses_api=True` and no adapter needed (Router measured OK for invoke, stream, bind_tools, with_structured_output). | `PY/langchain_openai/chat_models/base.py:460` (`base_url` alias of `openai_api_base`; env `OPENAI_API_BASE` at `:762`), `:564`/`:569` (`http_client`/`http_async_client`), `:798-806`, `:821-844`; 0.1.25 `:530,:545`; 0.2.14 `:577,:590`. `use_responses_api` added in **0.3.9**; auto-switch logic `:1222-1233` + `:3511-3522` (built-in tools or `include/previous_response_id/reasoning/text/truncation`). |
| **AzureChatOpenAI** | Same `http_client`/`http_async_client` fields, passed straight to the SDK | `PY/langchain_openai/chat_models/azure.py:686,690` |
| **LlamaIndex `OpenAI`** | `OpenAI(model=..., api_base=ROUTER, api_key=..., http_client=httpx.Client(transport=...), async_http_client=httpx.AsyncClient(transport=...))`. `api_base` order: param, `OPENAI_API_BASE`, `openai.base_url`, default (so `OPENAI_BASE_URL` is effectively ignored). Alternative: `llama_index.llms.openai.OpenAIResponses` (same client params), but it **crashes on Python 3.9** (PEP 604 types, measured). | `PY/llama_index/llms/openai/base.py:270-275,341-358,446-454`; `utils.py:982-989`; `responses.py:147,302-303` |
| **Pydantic AI** | `OpenAIProvider(base_url=ROUTER, api_key=..., http_client=httpx.AsyncClient(transport=AsyncRouterChatTransport()))` or `OpenAIProvider(openai_client=AsyncOpenAI(...))` (mutually exclusive). Async only. Better: codemod `OpenAIModel`/`OpenAIChatModel` -> `OpenAIResponsesModel` (measured OK on Router, including Claude), or the `openai-responses:` model prefix. | `PY/pydantic_ai/providers/openai.py:39-72`; default client `models/__init__.py:766-798`; `models/openai.py:195,422` (chat), `:736,930` (responses), prefixes `:725-734` |
| **LiteLLM** | (a) Native bridge, no adapter: model `openai/responses/<model>` with `api_base=ROUTER`, or global `litellm.route_all_chat_openai_to_responses = True` (`litellm/responses/bridge_check.py:39-75`; the prefix exists by ~v1.74, the global flag in ~v1.82-1.85). (b) Our transport: `litellm.client_session = httpx.Client(transport=...)` / `litellm.aclient_session = httpx.AsyncClient(transport=...)` (`litellm/__init__.py:425-426`), used for `openai/` chat via the OpenAI SDK (`llms/openai/common_utils.py:346-383`). Set them before the first call (clients are cached). Per call: `completion(..., client=OpenAI(http_client=...))`. The bridge path uses LiteLLM's own `HTTPHandler`, so `client_session` doesn't apply to it. | GitHub BerriAI/litellm main (v1.105.0) |
| **langchain-anthropic `ChatAnthropic`** | No `http_client` param, but none is needed: `base_url=` / `anthropic_api_url=` (env `ANTHROPIC_API_URL`, then `ANTHROPIC_BASE_URL`) set to `https://api.router.com` works (measured). | `PY/langchain_anthropic/chat_models.py:1393-1398`, clients `:1539-1567` |

---

## E. TypeScript: custom `fetch`

### E.1 openai-node `fetch` option across versions

| Version | Option / type | What the SDK passes to fetch | Default fetch |
|---|---|---|---|
| **v3** | **none.** axios-based; only an `AxiosInstance` can be injected (`base.ts:53`) | n/a | axios |
| **v4.x** (checked v4.104.0) | `fetch?: Core.Fetch`, `Fetch = (url: RequestInfo, init?: RequestInit) => Promise<Response>` (`src/core.ts:35`, `src/index.ts:206`); also `httpAgent` | `url`: always a **string**. `init = {method, body, headers, agent, signal}`: `body` is a **pretty-printed JSON string** (`JSON.stringify(body, null, 2)`, `core.ts:344`); `headers` is a **plain object** that includes `content-length`, `accept`, `content-type` and `x-stainless-*` (`core.ts:372-398`); call `this.fetch.call(undefined, url, init)` (`core.ts:576-603`) | **node-fetch@2** shim in Node (`_shims/node-runtime.ts:68,77`). Its `body` is a Node stream, not a web ReadableStream. |
| **v5/v6/v7** (installed 7.32.0) | `fetch?: Fetch`, `Fetch = (input: string \| URL \| Request, init?: RequestInit) => Promise<Response>` (`NM/openai/src/internal/builtin-types.ts:1`); `fetchOptions?: MergedRequestInit` (`client.ts:396,403`) | `url` string; `init = {method, headers: Headers, signal, body: JSON string, ...fetchOptions}`; **no content-length**; `duplex:'half'` only for stream bodies (`client.ts:2029-2072, 2256-2316`) | `globalThis.fetch` (`client.ts:636`). v7 caveat: x509 workload-identity auth replaces `this.fetch` (`client.ts:654-660`), so the custom fetch is bypassed in that mode. |

- URL: `baseURL + '/chat/completions'` with double slashes collapsed. Default base is `OPENAI_BASE_URL ??
  'https://api.openai.com/v1'` (`client.ts:535,579`). `defaultQuery` is appended (Azure `api-version`). Match
  `new URL(u).pathname.endsWith('/chat/completions')` and keep `search`.
- Non-stream parse (`NM/openai/src/internal/parse.ts:18-81`; v4 `core.ts:60-95`): streaming is decided by the
  **request** `stream:true`. JSON is parsed **only if** content-type includes `application/json` or ends with `+json`,
  else the raw text is returned, so **set `content-type: application/json`**. Errors: `!ok` -> `text()` -> JSON ->
  `APIError.generate(status, json)` reading `json.error` (`client.ts:1829-1928`). Router's error body passes through
  as-is.
- Stream parse: `Stream.fromSSEResponse` -> `_iterSSEMessages` -> `LineDecoder` + `SSEDecoder`
  (`NM/openai/src/core/streaming.ts:132-154, 543-597, 685-731`). Requires `response.body`. Blank-line framing
  (`\n\n`/`\r\n\r\n`/`\r\r`). `data.error` truthy -> throws `APIError`. Event-name dispatch differs by version (C.1).

### E.2 Fetch wrapper (v4 and v5+ compatible)

Rules:
- Accept `string | URL | Request`.
- Body is always a string from openai v4/v5+, LangChain JS and the AI SDK (`@ai-sdk/provider-utils post-to-api.ts`
  sends `JSON.stringify` + plain-object headers).
- Normalize headers with `new Headers(init.headers)`.
- **Delete `content-length`**: v4 sets it for the original body, and undici throws a content-length mismatch.
- Drop `agent` (node-fetch only) and `duplex`.
- **Keep `signal` exactly**: it carries SDK timeouts, abort, and consumer `break`.
- Call **`globalThis.fetch`**, not the SDK default. Under v4 that's node-fetch, whose body can't `pipeThrough`.
  Requires Node >= 18.
- Strip `content-length`/`content-encoding` on the rewritten response; undici already decompressed it.

```ts
type AnyFetch = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

export function routerChatFetch(baseFetch: AnyFetch = globalThis.fetch): AnyFetch {
  return async (input, init) => {
    const req = input instanceof Request ? input : undefined;
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
    const method = (init?.method ?? req?.method ?? 'GET').toUpperCase();
    if (method !== 'POST' || !url.pathname.endsWith('/chat/completions')) return baseFetch(input as any, init);

    const raw = typeof init?.body === 'string' ? init.body
      : req ? await req.clone().text()
      : init?.body instanceof Uint8Array ? new TextDecoder().decode(init.body) : String(init?.body ?? '');
    const chat = JSON.parse(raw);
    const { body: rbody, ctx } = chatToResponses(chat);           // section A, ported

    url.pathname = url.pathname.replace(/\/chat\/completions$/, '/responses');
    const headers = new Headers(init?.headers ?? req?.headers);
    headers.delete('content-length'); headers.delete('transfer-encoding');
    headers.set('content-type', 'application/json');
    if (ctx.stream) headers.set('accept', 'text/event-stream');
    const { agent, duplex, body: _b, headers: _h, ...rest } = (init ?? {}) as any;

    const up = await baseFetch(url.toString(), { ...rest, method: 'POST', headers,
      signal: init?.signal ?? req?.signal, body: JSON.stringify(rbody) });

    const out = new Headers(up.headers);
    out.delete('content-length'); out.delete('content-encoding');
    if (!up.ok) return new Response(await up.text(), { status: up.status, headers: out });
    if (!ctx.stream) {
      out.set('content-type', 'application/json');
      return new Response(JSON.stringify(responsesToChat(await up.json(), ctx)), { status: 200, headers: out });
    }
    out.set('content-type', 'text/event-stream; charset=utf-8');
    const body = up.body!
      .pipeThrough(new TextDecoderStream())
      .pipeThrough(sseToChatChunks(ctx))       // TransformStream<string,string>: parse Router SSE, emit `data: {...}\n\n`
      .pipeThrough(new TextEncoderStream());
    return new Response(body, { status: 200, headers: out });
  };
}
```

`sseToChatChunks` is the C.3 state machine as a `TransformStream`: line-buffer, accumulate `data:` lines until a blank
line, `JSON.parse`, dispatch on `.type`, enqueue `data: …\n\n` frames, and make `flush()` emit an error frame if
`response.completed` never arrived. Cancelling the consumer (SDK `break`/abort) propagates back through `pipeThrough`
to the upstream body. For stop-sequence emulation, call `controller.terminate()` after `[DONE]`, which cancels the
upstream body.

Usage:

```ts
new OpenAI({ baseURL: 'https://api.router.com/v1', apiKey: process.env.RAMP_ROUTER_API_KEY, fetch: routerChatFetch() });
createOpenAI({ baseURL: 'https://api.router.com/v1', apiKey: process.env.RAMP_ROUTER_API_KEY, fetch: routerChatFetch() });
new ChatOpenAI({ model, configuration: { baseURL: 'https://api.router.com/v1', apiKey: ..., fetch: routerChatFetch() } });
```

### E.3 Vercel AI SDK: which API does `openai(model)` call?

| `ai` | `@ai-sdk/openai` | default `openai(id)` | `.chat()` | `.responses()` | `compatibility` | env `OPENAI_BASE_URL` |
|---|---|---|---|---|---|---|
| 3.x | 0.0.x | **Chat** (`openai-provider.ts:197-214` @0.0.72) | yes | **no** | yes: `createOpenAI` defaults `'compatible'`, default instance `'strict'` | no |
| 4.x | 1.0-1.3.x | **Chat** (1.3.24 `:269-286`) | yes | **added 1.2.2** | yes (strict only adds `stream_options.include_usage`) | no |
| 5.x | 2.0.x | **Responses** (2.0.0 `:202-209`, "switch default to openai responses api") | yes | yes | removed | added 2.0.41 |
| 6.x | 3.0.x | Responses | yes | yes | no | yes |
| 7.x | 4.0.92 (installed) | Responses (`src/openai-provider.ts:322-331,360-362`) | yes (`:247-253,366`) | yes (`:333-344,368`) | no | yes (`:220-228`) |

- `createOpenAI({apiKey, baseURL, fetch, headers, organization, project})` has `fetch` in every version (0.0.72
  `:133`, 1.3.24 `:179`, 2.0.0 `:129`, 4.0.92 `:205`). URLs are `${withoutTrailingSlash(baseURL)}${path}` with
  `/chat/completions` (`NM/@ai-sdk/openai/dist/index.js:1348,1412`) or `/responses` (`:6477,6921`).
- Measured: ai 7 `router(model)` works (Responses); `router.chat(model)` 404s.
- Codemod: ai 3 / @ai-sdk/openai 0.x has no `.responses()`, so the fetch adapter is the only option (or a
  dependency bump). ai 4 with @ai-sdk/openai >=1.2.2: rewrite `openai(m)` -> `openai.responses(m)`. ai 5+: default
  already works; rewrite `.chat(m)` -> `.responses(m)` (or keep `.chat` + fetch adapter).
- The AI SDK chat model always sends `stream_options:{include_usage:true}` when streaming
  (`chat/openai-chat-language-model.ts:500-502`), so the usage chunk is required there. Its chunk schema
  (`dist/index.js:755-800`) wants `choices[].index`, tool-call `index` and a non-null `function` object. ai 7's
  `ToolCallTracker` (`NM/@ai-sdk/provider-utils/dist/index.js:4207`) tolerates odd orderings; older versions required
  `id` + `function.name` on the first tool-call delta.
- @ai-sdk/openai 4.x detects chat chunks arriving at a Responses model and raises an error telling you to use
  `.chat()` (`dist/index.js:6985,7887-7897`). The adapter must only rewrite `/chat/completions`, never `/responses`.

### E.4 LangChain JS `@langchain/openai`

- `configuration` is spread into the OpenAI client options, so **`configuration.fetch` reaches `new OpenAI({...})`**
  (1.6.2 `NM/@langchain/openai/dist/chat_models/base.js:233-236,268-274,316-328`; same in 0.3.17
  `dist/chat_models.js:1118-1128`). LangChain sets `maxRetries:0` on the inner client and retries itself.
  0.3/0.4 depend on openai ^4 (string body + plain-object headers including content-length), 0.5 on ^5, 1.6 on ^7.
- `useResponsesApi`: **absent <= 0.4.5; added 0.4.6.** Auto-switch on built-in tools or
  `previous_response_id/text/truncation/include` (0.4.6-0.5.x). 0.6.x adds `reasoning.summary` and custom tools.
  1.2.0+ adds model-based routing (`_modelPrefersResponsesAPI`, `dist/utils/misc.js:89-96`: `gpt-5.2-pro`,
  `gpt-5.4-pro`, `gpt-5.5-pro`, `gpt-5.6`, `codex`). Decision in 1.6.2: `dist/chat_models/index.js:617-674`.
  Otherwise the default is still Chat Completions (measured 404 on Router without the flag).
- Codemod preference: add `useResponsesApi: true` for >=0.4.6. Fetch adapter for <0.4.6.

---

## F. Anthropic SDKs: plain base_url swap

- **Python** (`anthropic` 0.125): base URL is the `base_url` kwarg, then `ANTHROPIC_BASE_URL` (`PY/anthropic/_client.py:233-234`,
  async `:665-666`), then `https://api.anthropic.com` (`:241`), **no `/v1`**. Paths carry `/v1`:
  `"/v1/messages"` (`resources/messages/messages.py:1050`, also streams), `"/v1/messages/count_tokens"` (`:1567`),
  `"/v1/models"` (`resources/models.py:135`). Beta: `"/v1/messages?beta=true"`
  (`resources/beta/messages/messages.py:1242`) and `"/v1/messages/count_tokens?beta=true"` (`:2090`), with
  `anthropic-beta: ",".join(betas)` (`:1234, 2080-2088`). Auth is `X-Api-Key` (`_client.py:366`) or
  `Authorization: Bearer` (`:379`), plus `anthropic-version: 2023-06-01` (`:387`).
- **TS** (`@anthropic-ai/sdk` 0.133): `baseURL = readEnv('ANTHROPIC_BASE_URL') ?? 'https://api.anthropic.com'`
  (`NM/@anthropic-ai/sdk/src/client.ts:791-823`, no `/v1`). Paths: `/v1/messages` (`resources/messages/messages.ts:99`),
  `/v1/messages/count_tokens` (`:195`), beta `/v1/messages?beta=true` (`resources/beta/messages/messages.ts:123`,
  `anthropic-beta` comma-joined `:129`), beta count_tokens adds `token-counting-2024-11-01` (`:209-214`),
  `/v1/models` (`resources/models.ts:58`). Also has `fetch` (`:673`) and `fetchOptions` (`:666`).
- **Router base for Anthropic SDKs is `https://api.router.com`**. `ANTHROPIC_BASE_URL=https://api.router.com/v1`
  produces `/v1/v1/messages`, so the codemod must strip a trailing `/v1`.
- **Does a base_url swap suffice?**
  - `messages.create` / `messages.stream` / tool loops / `count_tokens` / `models.list`: **yes** (all measured OK
    with anthropic-python and anthropic-node, on Claude and gpt-4o-mini).
  - Dated model ids: **no**. `claude-haiku-4-5-20251001` = 404 (measured). Model normalization (G.5) is needed in the
    codemod too.
  - `beta.messages.*` (`?beta=true` + `anthropic-beta`): **unmeasured.** Plausible since Router routes by path, but
    beta features (computer use, files, MCP connector, context management) may be rejected. Test in router-sim and
    live once; flag `betas=[...]` call sites.
  - `stop_sequences` on non-Anthropic models via Messages: 501 (measured). Not an issue when the model stays Claude.
  - Router-only fields (`provider_timeout`) are rejected on Messages (400, measured). Never add them on this surface.
  - Messages has no `usage.cost`, and Fireworks models report `input_tokens: 1` (measured). Only matters if code reads
    usage for billing.
  - Batches (`/v1/messages/batches`), Files API, `models.retrieve`: unsupported, so flag them.
- `langchain-anthropic` has no `http_client` but takes `base_url`/`anthropic_api_url` and the
  `ANTHROPIC_API_URL`/`ANTHROPIC_BASE_URL` envs (`PY/langchain_anthropic/chat_models.py:1393-1398`). It switches to
  `beta.messages.create` when `betas` is set (`:1620-1628`). Measured OK with plain base_url.

---

## G. Model capability gotchas the adapter must handle

Source: `~/code/router-live-check/models.json` (97 entries, `router.capabilities`, `router.limits`,
`router.providers[]`) plus the measured results.

### G.1 temperature / top_p

Drop when `capabilities.temperature == false`. In the snapshot that covers: every OpenAI reasoning model (o1, o3,
o3-mini, o3-pro, o4-mini, gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-pro, gpt-5.5*, gpt-5.6-*, gpt-6-*, gpt-5.2-pro,
gpt-5.4-pro) and **also newer Claude** (claude-opus-4-7, opus-4-8, opus-5, sonnet-5, opus-5-5, sonnet-5-5,
haiku-5-5, fable-5*). Not dropped: gpt-4o*, gpt-4.1*, gpt-5.1, gpt-5.2, gpt-5.3-codex, gpt-5.4, gpt-5.4-mini/nano,
claude-haiku-4-5, sonnet-4-5, sonnet-4-6, opus-4-6, Gemini, Grok, Fireworks/Baseten models. Bedrock rows show
`temperature: None` (unknown), so treat None as "drop" to be safe. Measured: gpt-5-nano + temperature = 400 (and
Router's silent flex fallback also 400s). LangChain hard-codes the same rule for `gpt-5*` (`base.py:3559-3562`).
Use the catalog flag, not name patterns.

### G.2 max_tokens vs max_completion_tokens; reasoning budgets

- Chat o-series rejected `max_tokens` and required `max_completion_tokens`. The Responses `max_output_tokens`
  covers both, so map either.
- `max_output_tokens` includes reasoning tokens. Chat-era code passes small caps (`max_tokens=50`) meant for
  visible text. On a reasoning model this gives `status:"incomplete"` with **no text**. Measured: gpt-5/gpt-5-mini/
  gpt-5-nano/o3/o4-mini at 64 tokens all came back incomplete with only a reasoning item. Mitigation when the target
  model has `reasoning.supported` and `default_effort != "none"`: (a) if `reasoning_effort` wasn't given, send the
  lowest supported effort (`minimal`/`none`/`low`, from `reasoning.efforts`); and/or (b) raise the floor to ~1024.
  Choose one and record it as a behavior change. Map `incomplete` -> `finish_reason:"length"` either way.
- Claude + `reasoning.effort` needs `max_output_tokens >= ~1100` (measured 400 at 400).
- OpenAI Responses rejects `max_output_tokens < 16` (unverified on Router). Classifier code with `max_tokens=1..5`
  needs a clamp to 16, which changes length semantics slightly.
- Clamp to catalog `limits.max_output_tokens` (gpt-4o/gpt-4o-mini 16384, gpt-4-turbo 4096, gpt-3.5-turbo 4096).

### G.3 stop -> 501 on OpenAI models: client-side emulation

Measured: `stop` on gpt-4o-mini via Responses = **501** `not_implemented` "Responses API has no stop parameter". Same 501
via Messages `stop_sequences`. Honored on claude-haiku-4-5. Strategy: strip `stop` for every non-Anthropic model
(Gemini/Fireworks/xAI are unverified; stripping is safe) and emulate:

```python
def apply_stop(text, stops):
    if not text or not stops: return text, False
    cut = min((i for i in (text.find(s) for s in stops) if i >= 0), default=-1)
    return (text[:cut], True) if cut >= 0 else (text, False)

class StopBuffer:          # streaming: hold back len(max stop)-1 chars so a stop split across deltas is caught
    def __init__(self, stops): self.stops, self.buf, self.hit = [s for s in stops if s], "", False
    def push(self, d):
        if not self.stops: return d
        self.buf += d
        t, hit = apply_stop(self.buf, self.stops)
        if hit: self.hit, self.buf = True, ""; return t
        keep = max(map(len, self.stops)) - 1
        out, self.buf = (self.buf[:-keep], self.buf[-keep:]) if keep else (self.buf, "")
        return out
    def flush(self): out, self.buf = self.buf, ""; return out
```

On a hit: emit the prefix, `finish_reason:"stop"`, `[DONE]`, then **close the upstream response** to stop billing
(tokens generated before the close are still billed). Usage reflects tokens generated past the stop point. Stop
strings apply only to text content, not tool-call arguments (same as OpenAI). Known divergence: OpenAI stops
*before* generating the stop sequence, so ours costs extra output tokens.

### G.4 Parameters Router rejects (measured)

`seed` 400, `n` 400, `logprobs` 400, `max_tokens` 400, `messages` 400, `response_format` 400, unknown fields 400,
`store:true` 400, `previous_response_id` 400 on OpenAI (200 but forgets elsewhere), `conversation` 400,
`stop` 501 on OpenAI, `provider_timeout` 400 on Messages only. Accepted: `user`, `truncation`, `parallel_tool_calls`,
`top_logprobs`, `safety_identifier`, `top_p`, `frequency_penalty`, `metadata`, `temperature` (where the catalog
allows it). `n>1` -> fan out (non-stream) or error (stream).

### G.5 Model id normalization

Catalog ids are undated. Dated or prefixed ids 404 on both surfaces (measured: `claude-haiku-4-5-20251001`). Rule:
look up the exact id in the catalog; else strip a date suffix and retry: `-\d{4}-\d{2}-\d{2}$`
(`gpt-4o-2024-08-06`->`gpt-4o`, `gpt-4o-mini-2024-07-18`->`gpt-4o-mini`, `o3-mini-2025-01-31`->`o3-mini`,
`gpt-4.1-2025-04-14`->`gpt-4.1`), `-\d{8}$` (`claude-sonnet-4-5-20250929`->`claude-sonnet-4-5`), `-\d{4}$`
(`gpt-4-0613`->`gpt-4`, `gpt-3.5-turbo-0125`->`gpt-3.5-turbo`), `-latest$`. Explicit aliases: `gpt-4-turbo-preview`,
`gpt-4-1106-preview`, `gpt-4-0125-preview` -> `gpt-4-turbo`; `gpt-3.5-turbo-16k` -> `gpt-3.5-turbo`;
`chatgpt-4o-latest` -> `gpt-4o`. Unmappable ids get flagged, not guessed: `o1-mini`, `o1-preview`, `gpt-4.5-preview`,
`gpt-4o-*-preview` (audio/realtime/search), `claude-3-*`, `claude-3-5-*`, `claude-3-7-*` (no Claude 3.x in the
catalog). Also flag `gpt-5.3-codex` (in /models but 403 data_residency_mismatch for this account, measured).
Strip provider prefixes `openai/`, `openai:`, `anthropic/`. Bedrock/Vertex-style ids (`us.anthropic.*`,
`global.anthropic.*`) are `requires_byok`: flag them.

### G.6 Other capability flags worth enforcing

- `tools.supported == false` for **gpt-4 and gpt-3.5-turbo** in the catalog. Chat code that sends `tools` on those
  models will likely fail. Flag it, or (as an explicit behavior change) map to gpt-4o-mini/gpt-4.1-mini.
- `modalities.input` lacks `image` for gpt-oss-120b, glm-5p3, gpt-4, gpt-3.5-turbo, o3-mini: image parts 400
  (measured on gpt-oss).
- `structured_outputs == false`: json_schema strict will fail; json_object may still work (unverified).
- Flex: plain model calls are served at flex by default (gpt-5-nano measured `service_tier:"flex"`). Send
  `service_tier:"default"` from the adapter unless the caller set it.
- `response.model` is dated and must never be sent back (B.2).
- urllib's default User-Agent gets 403 (Cloudflare 1010, measured). Irrelevant for httpx/fetch, but a raw-HTTP
  rewrite must set a UA.

---

## H. Risk summary

Riskiest translation pieces, roughly in order:
1. **Streaming tool calls.** Consumers concatenate `id`, `name` and `role` on repeat (openai-python accumulator,
   LlamaIndex, langchain-core), and LlamaIndex reads only the first tool-call delta per chunk. Emit exactly one
   tool-call delta per chunk, with id, type and name only on the first. Fall back to `function_call_arguments.done` /
   `output_item.done` when a provider sends no argument deltas.
2. **Reasoning models.** Small chat-era `max_tokens` gives `status:incomplete` with empty text. Encrypted reasoning
   can't round-trip through Chat history (catalog says `continuation.required:true` for OpenAI reasoning models), and
   the Claude effort-vs-budget 400 is another trap. Needs live/sim verification of a gpt-5-nano tool loop without
   reasoning replay.
3. **`strict` default flip.** Responses function tools default to strict=true. Without an explicit `strict:false`,
   many existing loose schemas 400.
4. **`stop` emulation.** 501 on OpenAI models. Client-side truncation costs tokens and needs a hold-back buffer
   across deltas.
5. **Parameters with no equivalent:** `n>1` (fan-out), `seed`, `logit_bias`, `logprobs` (needs `include`;
   unverified), audio in/out (unsupported), legacy `functions`, OpenAI `file_id`s.
6. **Model ids.** Dated ids 404; never echo `response.model`. gpt-4/gpt-3.5-turbo have no tool support in the
   catalog, and Claude 4.7+/5.x rejects `temperature`.
7. **Transport plumbing.** Stale `content-length` (httpx setdefault; undici mismatch with openai-node v4), forwarded
   `content-encoding`, lost `request.extensions` timeouts, env proxies disabled by a custom transport, and an explicit
   `proxy=` silently bypassing it.

SDK versions where transport/fetch injection is **impossible or ineffective**:
- **openai-python 0.x (<=0.28):** no httpx, no `http_client` (requests/aiohttp globals). Needs a codemod to >=1 or a
  `requests` adapter hack.
- **openai-node v3:** axios-based, no `fetch` option. Needs a codemod/upgrade.
- **openai-python <1.17:** injection works, but `DefaultHttpxClient` doesn't exist; use `httpx.Client` with explicit
  timeout and redirects.
- **openai-node v7 with x509 workload-identity auth:** the SDK swaps in its own fetch (`client.ts:654-660`), bypassing
  ours.
- **Vercel @ai-sdk/openai 0.x (ai 3):** `fetch` works, but there's no `.responses()`, so the adapter is mandatory
  there.
- **LiteLLM Responses bridge / `litellm.responses()`:** uses its own `HTTPHandler`; `litellm.client_session` doesn't
  apply (inject via `client=HTTPHandler(...)`).
- **langchain-anthropic:** no `http_client` param (not needed; base_url swap works).
- **LangChain Python with only `http_client`:** async calls bypass the transport. Always also set
  `http_async_client`.
