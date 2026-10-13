// Exercises the rendered routerRuntime.ts through the real openai-node SDK against router-sim, plus crafted Router
// replies from a fake upstream fetch (streaming edge cases, fallback, headers). Local only: no provider or Router calls.
// usage: node runtime_check.mjs <path to rendered routerRuntime.ts>   (env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL)
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import OpenAI from "openai";

const rtPath = process.argv[2];
const rt = await import(pathToFileURL(rtPath).href);
const sim = process.env.RAMP_ROUTER_BASE_URL;
const ROUTER_KEY = process.env.RAMP_ROUTER_API_KEY;
const lastReq = async () => (await (await fetch(sim + "/__last")).json());
const simCount = async () => (await (await fetch(sim + "/__log")).json()).length;
const urlOf = (input) => (typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url);
const json = (o, status = 200, headers = {}) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json", ...headers } });

// --- fake provider (api.openai.com / api.anthropic.com): records every call
const providerCalls = [];
const providerResponse = (id = "resp_provider") => ({
  id, object: "response", created_at: 0, status: "completed", model: "provider", output: [{ type: "message", id: "msg_p", role: "assistant", status: "completed", content: [{ type: "output_text", text: "from-provider", annotations: [] }] }],
  usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
});
const provider = async (input, init) => {
  const url = urlOf(input);
  providerCalls.push({ url, method: (init?.method || "GET").toUpperCase(), body: init?.body, headers: new Headers(init?.headers) });
  const p = new URL(url).pathname;
  if (p.endsWith("/embeddings"))
    return json({ object: "list", data: [{ object: "embedding", index: 0, embedding: [0.1] }], model: "e", usage: { prompt_tokens: 1, total_tokens: 1 } });
  if (p.endsWith("/input_items")) return json({ object: "list", data: [], first_id: null, last_id: null, has_more: false });
  if (/\/responses(\/[^/]+)?$/.test(p)) return json(providerResponse());
  if (p.endsWith("/v1/messages")) return json({ id: "m", type: "message", role: "assistant", model: "x", content: [{ type: "text", text: "from-provider" }], stop_reason: "end_turn", usage: { input_tokens: 1, output_tokens: 1 } });
  return json({ id: "p", object: "chat.completion", created: 0, model: "provider", choices: [{ index: 0, message: { role: "assistant", content: "from-provider" }, finish_reason: "stop" }] });
};
const base = async (input, init) => (urlOf(input).startsWith("http://127.0.0.1") ? fetch(input, init) : provider(input, init));

/** A routerFetch whose "Router" is a handler returning crafted replies; everything else goes to the fake provider. */
function fakeRouter(handler) {
  const routerCalls = [];
  const f = async (input, init) => {
    const url = urlOf(input);
    if (url.startsWith(sim)) {
      routerCalls.push({ url, body: JSON.parse(init.body), headers: new Headers(init.headers) });
      return handler(url, init);
    }
    return provider(input, init);
  };
  return { fetch: rt.routerFetch(f), routerCalls };
}

const results = [];
async function t(name, fn) {
  try { await fn(); results.push(["ok", name]); } catch (e) { results.push(["FAIL", name, String(e && e.stack || e)]); }
}
/** Asserts that fn's request went to the provider unchanged and never reached Router. */
async function onProvider(fn, n = 1) {
  providerCalls.length = 0;
  const before = await simCount();
  const r = await fn();
  assert.equal(providerCalls.length, n, "provider calls");
  assert.equal(await simCount(), before, "router-sim must not be called");
  return r;
}
/** Asserts that fn made no provider call (it may use Router). */
async function offProvider(fn) {
  providerCalls.length = 0;
  const r = await fn();
  assert.equal(providerCalls.length, 0, "provider calls");
  return r;
}
async function withEnv(vars, fn) {
  const old = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  try { return await fn(); } finally { for (const [k, v] of Object.entries(old)) if (v === undefined) delete process.env[k]; else process.env[k] = v; }
}
const client = new OpenAI({ apiKey: "sk-provider", fetch: rt.routerFetch(base), maxRetries: 0 });
const TOOLS = [{ type: "function", function: { name: "get_weather", parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] } } }];
const HI = [{ role: "user", content: "hi" }];

// ------------------------------------------------------------------ baseline (router-sim)
await t("chat non-stream", async () => {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, max_tokens: 20 });
  assert.equal(r.choices[0].message.content, "pong");
  assert.equal(r.choices[0].finish_reason, "stop");
  const l = await lastReq();
  assert.equal(l.path, "/v1/responses"); assert.ok(l.valid, JSON.stringify(l));
  assert.equal(l.request.max_output_tokens, 20);
  assert.equal(l.request.service_tier, "default");
  assert.match(l.request.metadata.feature, /runtime_check\.mjs/);
});
await t("chat stream + usage", async () => {
  const s = await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, stream: true, stream_options: { include_usage: true } });
  let text = "", finishes = [], usage = null;
  for await (const ch of s) { if (ch.choices[0]) { text += ch.choices[0].delta.content || ""; if (ch.choices[0].finish_reason) finishes.push(ch.choices[0].finish_reason); } if (ch.usage) usage = ch.usage; }
  assert.equal(text, "pong"); assert.deepEqual(finishes, ["stop"]); assert.ok(usage && usage.prompt_tokens > 0);
});
await t("tools round trip", async () => {
  const msgs = [{ role: "user", content: "weather?" }];
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: msgs, tools: TOOLS });
  const m = r.choices[0].message;
  assert.equal(r.choices[0].finish_reason, "tool_calls"); assert.equal(m.tool_calls[0].function.name, "get_weather");
  msgs.push(m, { role: "tool", tool_call_id: m.tool_calls[0].id, content: "18C" });
  const r2 = await client.chat.completions.create({ model: "gpt-4o-mini", messages: msgs, tools: TOOLS });
  assert.equal(r2.choices[0].message.content, "pong");
});
await t("tools stream (claude)", async () => {
  const s = await client.chat.completions.create({ model: "claude-haiku-4-5", messages: [{ role: "user", content: "weather?" }], tools: TOOLS, stream: true });
  let args = "", name = null, fin = [];
  for await (const ch of s) { const d = ch.choices[0]; if (!d) continue; for (const tc of d.delta.tool_calls || []) { if (tc.function?.name) name = tc.function.name; args += tc.function?.arguments || ""; } if (d.finish_reason) fin.push(d.finish_reason); }
  assert.equal(name, "get_weather"); assert.deepEqual(JSON.parse(args), { city: "x" }); assert.deepEqual(fin, ["tool_calls"]);
  assert.ok((await lastReq()).request.prompt_cache_key);
});
await t("stop emulated (openai)", async () => {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, stop: ["n"] });
  assert.equal(r.choices[0].message.content, "po");
  const s = await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, stop: "ng", stream: true });
  let text = ""; for await (const ch of s) text += ch.choices[0]?.delta?.content || ""; assert.equal(text, "po");
});
await t("json_schema", async () => {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "x" }], response_format: { type: "json_schema", json_schema: { name: "City", schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] }, strict: true } } });
  assert.deepEqual(JSON.parse(r.choices[0].message.content), { name: "x" });
});
await t("passthrough: unknown model / n>1 / embeddings", async () => {
  await onProvider(async () => {
    assert.equal((await client.chat.completions.create({ model: "gpt-4-32k", messages: [{ role: "user", content: "x" }] })).choices[0].message.content, "from-provider");
    assert.equal((await client.chat.completions.create({ model: "gpt-4o-mini", n: 2, messages: [{ role: "user", content: "x" }] })).choices[0].message.content, "from-provider");
    await client.embeddings.create({ model: "text-embedding-3-small", input: "x" });
  }, 3);
});
await t("responses routed, store:false dropped", async () => {
  const r = await client.responses.create({ model: "gpt-4o-mini", input: "hi", store: false });
  assert.equal(r.output_text, "pong"); const l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.store, undefined);
  assert.equal(l.request.service_tier, "default");
  assert.equal(l.request.include, undefined, "no include for non-reasoning models");
});
await t("router error surfaces (no fallback for other 400s)", async () => {
  providerCalls.length = 0;
  await assert.rejects(client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "tool", tool_call_id: "nope", content: "x" }] }), (e) => e.status === 400);
  assert.equal(providerCalls.length, 0);
});
await t("anthropic-style fetch", async () => {
  const f = rt.routerFetch(base);
  const r = await f("https://api.anthropic.com/v1/messages", { method: "POST", headers: { "content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": "k" }, body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 16, messages: HI }) });
  const j = await r.json(); assert.equal(j.content[0].text, "pong");
  const l = await lastReq(); assert.equal(l.request.model, "claude-haiku-4-5"); assert.ok(l.valid);
});

// ------------------------------------------------------------------ C1: previous_response_id chains / stored responses
const isStateful = (status, re = /served by Router/) => (e) => e.status === status && e.code === "router_stateful_unsupported" && re.test(e.message);
const strip = (items) => items.map(({ id, status, ...rest }) => rest); // compare items without server-assigned ids
await t("C1 Router chain continued in-process (JSON)", async () => {
  const r1 = await client.responses.create({ model: "gpt-4o-mini", input: "hi" });
  assert.equal(r1.output_text, "pong"); assert.match(r1.id, /^resp_/);
  const r2 = await offProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "next", previous_response_id: r1.id }));
  assert.equal(r2.output_text, "pong");
  let l = await lastReq(); assert.ok(l.valid, JSON.stringify(l));
  assert.equal(l.request.previous_response_id, undefined);
  assert.deepEqual(l.request.input, [{ role: "user", content: "hi" }, ...r1.output, { role: "user", content: "next" }]);
  // a third turn chains off the second: the whole history travels inline
  await client.responses.create({ model: "gpt-4o-mini", input: [{ role: "user", content: "third" }], previous_response_id: r2.id });
  l = await lastReq();
  assert.deepEqual(l.request.input, [{ role: "user", content: "hi" }, ...r1.output, { role: "user", content: "next" }, ...r2.output, { role: "user", content: "third" }]);
  // explicit store:true on a Router chain cannot go to the provider (it never saw the id): the chain continues on Router
  await offProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", previous_response_id: r2.id, store: true }));
  assert.equal((await lastReq()).request.store, undefined);
});
await t("C1 Router chain continued in-process (stream)", async () => {
  const s = await client.responses.create({ model: "gpt-4o-mini", input: "hi", stream: true });
  let id = null, text = "", out = null;
  for await (const ev of s) { if (ev.type === "response.completed") { id = ev.response.id; out = ev.response.output; } if (ev.type === "response.output_text.delta") text += ev.delta; }
  assert.equal(text, "pong"); assert.ok(id);
  const r2 = await offProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "next", previous_response_id: id }));
  assert.equal(r2.output_text, "pong");
  assert.deepEqual((await lastReq()).request.input, [{ role: "user", content: "hi" }, ...out, { role: "user", content: "next" }]);
  // a follow-up on a model Router does not serve cannot go to the provider either
  await onProvider(() => assert.rejects(client.responses.create({ model: "gpt-4-32k", input: "next", previous_response_id: id }), isStateful(400)), 0);
});
await t("C1 retrieve / input_items / delete of a Router id answered locally", async () => {
  const r1 = await client.responses.create({ model: "gpt-4o-mini", input: [{ role: "user", content: "hi" }], instructions: "be brief" });
  await onProvider(async () => {
    const got = await client.responses.retrieve(r1.id);
    assert.equal(got.id, r1.id); assert.equal(got.object, "response"); assert.deepEqual(got.output, r1.output);
    const items = await client.responses.inputItems.list(r1.id);
    assert.deepEqual(items.data, [{ role: "user", content: "hi" }]);
    const del = await client.responses.del(r1.id);
    assert.equal(del.deleted, true);
    await assert.rejects(client.responses.retrieve(r1.id), isStateful(404, /no longer in this process/));
    await assert.rejects(client.responses.inputItems.list(r1.id), isStateful(404));
    await assert.rejects(client.responses.create({ model: "gpt-4o-mini", input: "next", previous_response_id: r1.id }), isStateful(400, /ROUTER_MIGRATE_DISABLE=1/));
  }, 0);
});
await t("C1 evicted chain (LRU 256) -> 400 / 404, never the provider", async () => {
  let n = 0;
  const fr = fakeRouter(() => json({ ...providerResponse(`resp_router_${n++}`), output: [{ type: "message", id: `msg_${n}`, role: "assistant", status: "completed", content: [{ type: "output_text", text: "r", annotations: [] }] }] }));
  const c = new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 });
  for (let i = 0; i < 257; i++) await c.responses.create({ model: "gpt-4o-mini", input: `turn ${i}` });
  providerCalls.length = 0;
  await assert.rejects(c.responses.create({ model: "gpt-4o-mini", input: "x", previous_response_id: "resp_router_0" }), isStateful(400, /no longer in this process.*ROUTER_MIGRATE_DISABLE=1/));
  await assert.rejects(c.responses.retrieve("resp_router_0"), isStateful(404, /no longer in this process/));
  const r = await c.responses.create({ model: "gpt-4o-mini", input: "x", previous_response_id: "resp_router_1" });
  assert.equal(r.id, "resp_router_257");
  assert.deepEqual(strip(fr.routerCalls.at(-1).body.input), [{ role: "user", content: "turn 1" }, { type: "message", role: "assistant", content: [{ type: "output_text", text: "r", annotations: [] }] }, { role: "user", content: "x" }]);
  assert.equal(providerCalls.length, 0);
});
await t("C1 provider-side state stays on the provider", async () => {
  await onProvider(async () => {
    const r = await client.responses.create({ model: "gpt-4o-mini", input: "next", previous_response_id: "resp_from_openai" });
    assert.equal(r.output_text, "from-provider");
    assert.equal(JSON.parse(providerCalls[0].body).previous_response_id, "resp_from_openai");
  });
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", conversation: "conv_1" }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", store: true }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", background: true }));
  await onProvider(() => client.responses.retrieve("resp_from_openai"));
  await onProvider(() => client.responses.inputItems.list("resp_from_openai"));
});

// ------------------------------------------------------------------ H2: provider-account state in /responses
await t("H2 provider-bound /responses inputs", async () => {
  const user = { role: "user", content: "hi" };
  await onProvider(() => client.responses.create({ model: "gpt-5.4-mini", input: [user, { type: "reasoning", id: "rs_1", summary: [] }, user] }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: [user, { type: "item_reference", id: "msg_stored_on_openai" }, user] }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", tools: [{ type: "file_search", vector_store_ids: ["vs_1"] }] }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: "x", tools: [{ type: "code_interpreter", container: { type: "auto", file_ids: ["file_1"] } }] }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: [{ role: "user", content: [{ type: "input_file", file_id: "file_1" }] }] }));
  await onProvider(() => client.responses.create({ model: "gpt-4o-mini", input: [{ role: "user", content: [{ type: "input_image", file_id: "file_2", detail: "auto" }] }] }));
});
await t("H2 reasoning models request encrypted reasoning; replay with it routes", async () => {
  const user = { role: "user", content: "hi" };
  const r = await client.responses.create({ model: "gpt-5.4-mini", input: [user, { type: "reasoning", id: "rs_1", summary: [], encrypted_content: "enc" }, user] });
  assert.equal(r.output_text, "pong");
  let l = await lastReq(); assert.ok(l.valid); assert.deepEqual(l.request.include, ["reasoning.encrypted_content"]);
  await client.responses.create({ model: "gpt-5.4-mini", input: "x", include: ["file_search_call.results", "reasoning.encrypted_content"] });
  l = await lastReq(); assert.deepEqual(l.request.include, ["file_search_call.results", "reasoning.encrypted_content"]);
  await client.responses.create({ model: "gpt-5.4-mini", input: "x", include: ["message.output_text.logprobs"] });
  l = await lastReq(); assert.deepEqual(l.request.include, ["message.output_text.logprobs", "reasoning.encrypted_content"]);
  await client.responses.create({ model: "claude-haiku-4-5", input: "x" }); // only OpenAI-owned reasoning models (parity with Python)
  l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.include, undefined);
});

// ------------------------------------------------------------------ M3: streaming translation of crafted Router SSE
const shell = (status = "completed", inc = null) => ({ id: "resp_x", object: "response", status, model: "gpt-4o-mini", incomplete_details: inc, output: [], usage: { input_tokens: 5, output_tokens: 7, total_tokens: 12 } });
const sse = (evs, sep = "\n\n", trailing = true) => evs.map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}`).join(sep) + (trailing ? sep : "");
const sseReply = (text) => () => new Response(text, { status: 200, headers: { "content-type": "text/event-stream" } });
const created = { type: "response.created", response: shell("in_progress") };
const completed = { type: "response.completed", response: shell() };
const fcItem = (args) => ({ type: "function_call", id: "fc_1", call_id: "call_1", name: "get_weather", arguments: args, status: args ? "completed" : "in_progress" });
async function streamChat(body, extra = {}) {
  const fr = fakeRouter(sseReply(body));
  const c = new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 });
  const s = await c.chat.completions.create({ model: "gpt-4o-mini", stream: true, messages: [{ role: "user", content: "go" }], ...extra });
  const out = { content: "", refusal: "", fin: [], calls: [] };
  for await (const ch of s) {
    const d = ch.choices[0]; if (!d) continue;
    out.content += d.delta.content || ""; out.refusal += d.delta.refusal || "";
    for (const tc of d.delta.tool_calls || []) {
      const x = (out.calls[tc.index] ||= { id: null, name: null, args: "" });
      if (tc.id) x.id = tc.id; if (tc.function?.name) x.name = tc.function.name; x.args += tc.function?.arguments || "";
    }
    if (d.finish_reason) out.fin.push(d.finish_reason);
  }
  const raw = await (await fakeRouter(sseReply(body)).fetch("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-x" }, body: JSON.stringify({ model: "gpt-4o-mini", stream: true, messages: [{ role: "user", content: "go" }] }) })).text();
  out.done = raw.trimEnd().endsWith("data: [DONE]");
  return out;
}
await t("M3 CRLF-framed SSE", async () => {
  const o = await streamChat(sse([created, { type: "response.output_text.delta", delta: "hel" }, { type: "response.output_text.delta", delta: "lo" }, completed], "\r\n\r\n"));
  assert.equal(o.content, "hello"); assert.deepEqual(o.fin, ["stop"]); assert.ok(o.done);
});
await t("M3 streamed refusal -> delta.refusal", async () => {
  const o = await streamChat(sse([created, { type: "response.refusal.delta", delta: "I can't " }, { type: "response.refusal.delta", delta: "help." }, completed]));
  assert.equal(o.refusal, "I can't help."); assert.equal(o.content, ""); assert.deepEqual(o.fin, ["stop"]);
});
await t("M3 tool args only in function_call_arguments.done / output_item.done", async () => {
  const a = '{"x":1}';
  let o = await streamChat(sse([created, { type: "response.output_item.added", item: fcItem("") }, { type: "response.function_call_arguments.done", item_id: "fc_1", arguments: a }, { type: "response.output_item.done", item: fcItem(a) }, completed]), { tools: TOOLS });
  assert.deepEqual(o.calls, [{ id: "call_1", name: "get_weather", args: a }]); assert.deepEqual(o.fin, ["tool_calls"]);
  o = await streamChat(sse([created, { type: "response.output_item.done", item: fcItem(a) }, completed]), { tools: TOOLS });
  assert.deepEqual(o.calls, [{ id: "call_1", name: "get_weather", args: a }]); assert.deepEqual(o.fin, ["tool_calls"]);
  o = await streamChat(sse([created, { type: "response.output_item.added", item: fcItem("") }, { type: "response.function_call_arguments.delta", item_id: "fc_1", delta: '{"x"' }, { type: "response.function_call_arguments.done", item_id: "fc_1", arguments: a }, { type: "response.output_item.done", item: fcItem(a) }, completed]), { tools: TOOLS });
  assert.deepEqual(o.calls, [{ id: "call_1", name: "get_weather", args: a }]);
});
await t("M3 response.incomplete content_filter -> content_filter", async () => {
  const o = await streamChat(sse([created, { type: "response.output_text.delta", delta: "partial" }, { type: "response.incomplete", response: shell("incomplete", { reason: "content_filter" }) }]));
  assert.equal(o.content, "partial"); assert.deepEqual(o.fin, ["content_filter"]); assert.ok(o.done);
});
await t("M3 tool call cut off by max_output_tokens -> length", async () => {
  const o = await streamChat(sse([created, { type: "response.output_item.added", item: fcItem("") }, { type: "response.function_call_arguments.delta", item_id: "fc_1", delta: '{"x": "trunc' }, { type: "response.incomplete", response: shell("incomplete", { reason: "max_output_tokens" }) }]), { tools: TOOLS });
  assert.equal(o.calls[0].args, '{"x": "trunc'); assert.deepEqual(o.fin, ["length"]);
});
await t("M3 last event without trailing blank line / stream ends early", async () => {
  let o = await streamChat(sse([created, { type: "response.output_text.delta", delta: "hello" }, completed], "\n\n", false));
  assert.equal(o.content, "hello"); assert.deepEqual(o.fin, ["stop"]); assert.ok(o.done);
  o = await streamChat(sse([created, { type: "response.output_text.delta", delta: "hello" }]));
  assert.equal(o.content, "hello"); assert.deepEqual(o.fin, ["stop"]); assert.ok(o.done);
});
const FUNCTIONS = [{ name: "get_weather", parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] } }];
async function legacyStream(c) {
  const s = await c.chat.completions.create({ model: "gpt-4o-mini", stream: true, messages: [{ role: "user", content: "weather?" }], functions: FUNCTIONS });
  const out = { deltas: [], name: null, args: "", fin: [], toolCalls: 0 };
  for await (const ch of s) {
    const d = ch.choices[0]; if (!d) continue;
    if (d.delta.function_call) { out.deltas.push(d.delta.function_call); if (d.delta.function_call.name) out.name = d.delta.function_call.name; out.args += d.delta.function_call.arguments || ""; }
    if (d.delta.tool_calls) out.toolCalls++;
    if (d.finish_reason) out.fin.push(d.finish_reason);
  }
  return out;
}
await t("legacy functions streaming -> delta.function_call", async () => {
  let o = await legacyStream(client); // router-sim
  assert.equal(o.toolCalls, 0); assert.equal(o.name, "get_weather"); assert.deepEqual(JSON.parse(o.args), { city: "x" });
  assert.deepEqual(o.deltas[0], { name: "get_weather", arguments: "" }); assert.ok(o.deltas.slice(1).every((d) => d.name === undefined && typeof d.arguments === "string"));
  assert.deepEqual(o.fin, ["function_call"]);
  const l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.tools[0].name, "get_weather");
  // crafted: args only in .done, and a cut-off call -> length
  const a = '{"city":"x"}';
  let fr = fakeRouter(sseReply(sse([created, { type: "response.output_item.added", item: fcItem("") }, { type: "response.function_call_arguments.done", item_id: "fc_1", arguments: a }, completed])));
  o = await legacyStream(new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }));
  assert.equal(o.args, a); assert.deepEqual(o.fin, ["function_call"]); assert.equal(o.toolCalls, 0);
  fr = fakeRouter(sseReply(sse([created, { type: "response.output_item.added", item: fcItem("") }, { type: "response.function_call_arguments.delta", item_id: "fc_1", delta: '{"ci' }, { type: "response.incomplete", response: shell("incomplete", { reason: "max_output_tokens" }) }])));
  o = await legacyStream(new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }));
  assert.deepEqual(o.fin, ["length"]);
});
await t("M3 non-stream content_filter / truncated tool call", async () => {
  const resp = (inc, output) => ({ ...shell("incomplete", { reason: inc }), output });
  let fr = fakeRouter(() => json(resp("max_output_tokens", [fcItem('{"x": "tr')])));
  let r = await new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }).chat.completions.create({ model: "gpt-4o-mini", messages: HI, tools: TOOLS });
  assert.equal(r.choices[0].finish_reason, "length");
  fr = fakeRouter(() => json(resp("content_filter", [{ type: "message", role: "assistant", content: [{ type: "output_text", text: "par" }] }])));
  r = await new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }).chat.completions.create({ model: "gpt-4o-mini", messages: HI });
  assert.equal(r.choices[0].finish_reason, "content_filter");
});

// ------------------------------------------------------------------ M4: untranslatable params, provider fallback
await t("M4 untranslatable tool_choice / presence_penalty -> provider", async () => {
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, tools: TOOLS, tool_choice: { type: "allowed_tools", allowed_tools: { mode: "auto", tools: [{ type: "function", function: { name: "get_weather" } }] } } }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, tools: TOOLS, tool_choice: { type: "custom", custom: { name: "x" } } }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, tools: [{ type: "custom", custom: { name: "x" } }] }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, presence_penalty: 0.5 }));
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, presence_penalty: 0, tools: TOOLS, tool_choice: { type: "function", function: { name: "get_weather" } } });
  const l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.presence_penalty, undefined); assert.deepEqual(l.request.tool_choice, { type: "function", name: "get_weather" });
});
const routerError = (status, error, headers = {}) => () => json({ error }, status, headers);
await t("M4 Router 404 model_not_found -> original request resent to provider", async () => {
  const fr = fakeRouter(routerError(404, { message: "The model `gpt-4o-mini` does not exist", type: "invalid_request_error", code: "model_not_found" }));
  const c = new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 });
  providerCalls.length = 0;
  const body = { model: "gpt-4o-mini", messages: HI, temperature: 0.3 };
  const r = await c.chat.completions.create(body);
  assert.equal(r.choices[0].message.content, "from-provider");
  assert.equal(fr.routerCalls.length, 1); assert.equal(providerCalls.length, 1);
  assert.equal(providerCalls[0].url, "https://api.openai.com/v1/chat/completions");
  assert.deepEqual(JSON.parse(providerCalls[0].body), body);
  assert.equal(providerCalls[0].headers.get("authorization"), "Bearer sk-provider");
});
await t("M4 fallback codes: 400 unknown/unsupported/invalid_value, 403; not other 400s", async () => {
  for (const [status, err] of [[400, { code: "unknown_parameter", type: "invalid_request_error", message: "Unknown parameter" }], [400, { code: "unsupported_parameter", message: "x" }], [400, { code: "invalid_value", message: "x" }], [403, { code: "data_residency_mismatch", message: "x" }]]) {
    const fr = fakeRouter(routerError(status, err));
    providerCalls.length = 0;
    const r = await new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }).chat.completions.create({ model: "gpt-4o-mini", messages: HI });
    assert.equal(r.choices[0].message.content, "from-provider", `${status} ${err.code}`); assert.equal(providerCalls.length, 1);
  }
  const fr = fakeRouter(() => new Response("error code: 1010", { status: 403, headers: { "content-type": "text/plain" } }));
  providerCalls.length = 0;
  assert.equal((await new OpenAI({ apiKey: "sk-provider", fetch: fr.fetch, maxRetries: 0 }).chat.completions.create({ model: "gpt-4o-mini", messages: HI })).choices[0].message.content, "from-provider");
  const bad = fakeRouter(routerError(400, { code: "invalid_request_error", message: "No tool call found" }));
  providerCalls.length = 0;
  await assert.rejects(new OpenAI({ apiKey: "sk-provider", fetch: bad.fetch, maxRetries: 0 }).chat.completions.create({ model: "gpt-4o-mini", messages: HI }), (e) => e.status === 400);
  assert.equal(providerCalls.length, 0);
});
await t("M4 fallback: streaming, /responses, anthropic; never without provider auth", async () => {
  const nf = routerError(404, { code: "model_not_found", message: "nope" });
  let fr = fakeRouter(nf);
  providerCalls.length = 0;
  let r = await fr.fetch("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-p" }, body: JSON.stringify({ model: "gpt-4o-mini", stream: true, messages: HI }) });
  assert.equal(r.status, 200); assert.equal(providerCalls.length, 1); assert.equal(JSON.parse(providerCalls[0].body).stream, true);
  providerCalls.length = 0;
  r = await fr.fetch("https://api.openai.com/v1/responses", { method: "POST", headers: { authorization: "Bearer sk-p" }, body: JSON.stringify({ model: "gpt-4o-mini", input: "x" }) });
  assert.equal((await r.json()).id, "resp_provider"); assert.equal(providerCalls.length, 1);
  fr = fakeRouter(() => json({ type: "error", error: { type: "not_found_error", message: "model: claude-haiku-4-5" } }, 404));
  providerCalls.length = 0;
  r = await fr.fetch("https://api.anthropic.com/v1/messages", { method: "POST", headers: { "x-api-key": "k", "anthropic-version": "2023-06-01" }, body: JSON.stringify({ model: "claude-haiku-4-5", max_tokens: 16, messages: HI }) });
  assert.equal((await r.json()).content[0].text, "from-provider"); assert.equal(providerCalls.length, 1);
  fr = fakeRouter(nf);
  providerCalls.length = 0;
  r = await fr.fetch("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model: "gpt-4o-mini", messages: HI }) });
  assert.equal(r.status, 404); assert.equal((await r.json()).error.code, "model_not_found"); assert.equal(providerCalls.length, 0);
});

// ------------------------------------------------------------------ M5: silent semantic changes now stay on the provider / are preserved
await t("M5 seed / small max_tokens / message name -> provider", async () => {
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, seed: 7 }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, max_tokens: 1 }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, max_completion_tokens: 15 }));
  await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", name: "alice", content: "hi" }] }));
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, max_completion_tokens: 16 });
  assert.equal((await lastReq()).request.max_output_tokens, 16);
});
await t("M5 assistant refusal history is kept", async () => {
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: [HI[0], { role: "assistant", content: [{ type: "refusal", refusal: "no" }] }, HI[0], { role: "assistant", content: null, refusal: "still no" }, HI[0], { role: "assistant", content: "ok", refusal: null }, HI[0]] });
  const l = await lastReq(); assert.ok(l.valid, JSON.stringify(l));
  assert.deepEqual(l.request.input[1], { role: "assistant", content: [{ type: "refusal", refusal: "no" }] });
  assert.deepEqual(l.request.input[3], { role: "assistant", content: [{ type: "refusal", refusal: "still no" }] });
  assert.deepEqual(l.request.input[5], { role: "assistant", content: "ok" });
});
await t("M5 metadata at 16 keys gets no feature", async () => {
  const m16 = Object.fromEntries(Array.from({ length: 16 }, (_, i) => [`k${i}`, "v"]));
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, metadata: m16 });
  let l = await lastReq(); assert.equal(Object.keys(l.request.metadata).length, 16); assert.equal(l.request.metadata.feature, undefined);
  const m15 = Object.fromEntries(Array.from({ length: 15 }, (_, i) => [`k${i}`, "v"]));
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, metadata: m15 });
  l = await lastReq(); assert.equal(Object.keys(l.request.metadata).length, 16); assert.ok(l.request.metadata.feature);
  await client.responses.create({ model: "gpt-4o-mini", input: "x", metadata: m16 });
  l = await lastReq(); assert.equal(l.request.metadata.feature, undefined);
});
await t("M5 service_tier default unless set; env override, auto = omit", async () => {
  await client.chat.completions.create({ model: "gpt-4o-mini", messages: HI, service_tier: "priority" });
  assert.equal((await lastReq()).request.service_tier, "priority");
  await withEnv({ ROUTER_MIGRATE_SERVICE_TIER: "flex" }, () => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI }));
  assert.equal((await lastReq()).request.service_tier, "flex");
  await withEnv({ ROUTER_MIGRATE_SERVICE_TIER: "Auto" }, () => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI }));
  assert.equal((await lastReq()).request.service_tier, undefined);
  await withEnv({ ROUTER_MIGRATE_SERVICE_TIER: "auto" }, () => client.responses.create({ model: "gpt-4o-mini", input: "x" }));
  assert.equal((await lastReq()).request.service_tier, undefined);
});

// ------------------------------------------------------------------ M7 / L1 / L6 / feature tag
await t("M7 kill switch is case-insensitive", async () => {
  for (const v of ["1", "true", "TRUE", "True", "yes", "YES", "on", "On", " on "]) {
    await withEnv({ ROUTER_MIGRATE_DISABLE: v }, async () => {
      assert.equal(rt.routerActive(), false, v);
      await onProvider(() => client.chat.completions.create({ model: "gpt-4o-mini", messages: HI }));
    });
  }
  for (const v of ["0", "false", ""]) await withEnv({ ROUTER_MIGRATE_DISABLE: v }, async () => assert.equal(rt.routerActive(), true, v));
});
await t("L1 upstream headers kept on translated responses", async () => {
  const h = { "retry-after": "7", "x-ratelimit-remaining-requests": "0", "x-request-id": "req_1", "x-router-provider": "openai", "x-should-retry": "true" };
  let fr = fakeRouter(routerError(429, { code: "rate_limit_exceeded", message: "slow down" }, h));
  let r = await fr.fetch("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-p" }, body: JSON.stringify({ model: "gpt-4o-mini", messages: HI }) });
  assert.equal(r.status, 429);
  for (const [k, v] of Object.entries(h)) assert.equal(r.headers.get(k), v, k);
  fr = fakeRouter(() => new Response(JSON.stringify({ ...shell(), output: [{ type: "message", role: "assistant", content: [{ type: "output_text", text: "hi" }] }] }), { headers: { "content-type": "application/json", "content-length": "999999", ...h } }));
  r = await fr.fetch("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-p" }, body: JSON.stringify({ model: "gpt-4o-mini", messages: HI }) });
  assert.equal((await r.json()).choices[0].message.content, "hi");
  assert.equal(r.headers.get("content-length"), null); assert.equal(r.headers.get("x-ratelimit-remaining-requests"), "0"); assert.equal(r.headers.get("retry-after"), "7");
});
await t("L6 unexpected request shapes go to the provider unchanged", async () => {
  const f = rt.routerFetch(base);
  for (const extra of [{ stop: 5 }, { metadata: ["a"] }, { messages: "hi" }]) {
    await onProvider(async () => {
      const body = JSON.stringify({ model: "gpt-4o-mini", messages: HI, ...extra });
      const r = await f("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-p" }, body });
      assert.equal((await r.json()).choices[0].message.content, "from-provider");
      assert.equal(providerCalls[0].body, body);
    });
  }
});
await t("feature tag off: no metadata.feature, no automatic prompt_cache_key", async () => {
  await withEnv({ ROUTER_MIGRATE_FEATURE_TAG: "off" }, async () => {
    await client.chat.completions.create({ model: "claude-haiku-4-5", messages: HI });
    let l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.metadata, undefined); assert.equal(l.request.prompt_cache_key, undefined);
    await client.responses.create({ model: "claude-haiku-4-5", input: "x" });
    l = await lastReq(); assert.equal(l.request.metadata, undefined); assert.equal(l.request.prompt_cache_key, undefined);
    await client.chat.completions.create({ model: "claude-haiku-4-5", messages: HI, prompt_cache_key: "mine" });
    assert.equal((await lastReq()).request.prompt_cache_key, "mine");
  });
});

// ------------------------------------------------------------------ H3: the SDK's http agent (proxy / keep-alive) is honoured
// The runtime copy lives under the project so its lazy import("node-fetch") resolves like it would in a host app.
await t("H3 init.agent is honoured (node-fetch) for provider and Router traffic", async () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const copy = path.join(here, `.rt-agent-${process.pid}.ts`);
  fs.copyFileSync(rtPath, copy);
  const prov = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => { res.setHeader("content-type", "application/json"); res.end(JSON.stringify({ id: "p", object: "chat.completion", created: 0, model: "provider", choices: [{ index: 0, message: { role: "assistant", content: "from-local-provider" }, finish_reason: "stop" }] })); });
  });
  await new Promise((r) => prov.listen(0, "127.0.0.1", r));
  class TagAgent extends http.Agent {
    hits = [];
    createConnection(opts, cb) { this.hits.push(`${opts.host}:${opts.port}`); return super.createConnection(opts, cb); }
  }
  try {
    const rt2 = await import(pathToFileURL(copy).href);
    const provURL = `http://127.0.0.1:${prov.address().port}/v1`;
    const simPort = new URL(sim).port;
    // key unset: exact no-op, the SDK's own node-fetch uses the agent
    await withEnv({ RAMP_ROUTER_API_KEY: undefined }, async () => {
      const agent = new TagAgent();
      const c = new OpenAI({ apiKey: "sk-provider", baseURL: provURL, httpAgent: agent, maxRetries: 0, ...rt2.routerOptions() });
      assert.equal((await c.chat.completions.create({ model: "gpt-4o-mini", messages: HI })).choices[0].message.content, "from-local-provider");
      assert.deepEqual(agent.hits, [`127.0.0.1:${prov.address().port}`]);
    });
    await withEnv({ RAMP_ROUTER_API_KEY: ROUTER_KEY }, async () => {
      // key set, request stays on the provider: still through the agent
      let agent = new TagAgent();
      let c = new OpenAI({ apiKey: "sk-provider", baseURL: provURL, httpAgent: agent, maxRetries: 0, ...rt2.routerOptions() });
      assert.equal((await c.chat.completions.create({ model: "gpt-4o-mini", messages: HI })).choices[0].message.content, "from-local-provider");
      assert.deepEqual(agent.hits, [`127.0.0.1:${prov.address().port}`]);
      // key set, routed: Router traffic keeps the agent (JSON and a node-fetch stream body)
      agent = new TagAgent();
      c = new OpenAI({ apiKey: "sk-provider", httpAgent: agent, maxRetries: 0, ...rt2.routerOptions() });
      assert.equal((await c.chat.completions.create({ model: "gpt-4o-mini", messages: HI })).choices[0].message.content, "pong");
      const s = await c.chat.completions.create({ model: "gpt-4o-mini", messages: HI, stream: true });
      let text = "", fin = []; for await (const ch of s) { text += ch.choices[0]?.delta?.content || ""; if (ch.choices[0]?.finish_reason) fin.push(ch.choices[0].finish_reason); }
      assert.equal(text, "pong"); assert.deepEqual(fin, ["stop"]);
      const rs = await c.responses.create({ model: "gpt-4o-mini", input: "hi", stream: true });
      let rid = null; for await (const ev of rs) if (ev.type === "response.completed") rid = ev.response.id;
      assert.equal((await c.responses.create({ model: "gpt-4o-mini", input: "x", previous_response_id: rid })).output_text, "pong");
      assert.equal((await lastReq()).request.input.length, 3);
      assert.ok(agent.hits.length >= 3 && agent.hits.every((h) => h === `127.0.0.1:${simPort}`), JSON.stringify(agent.hits));
      // an https keep-alive agent (openai-node's default) is not forced onto an http Router URL
      c = new OpenAI({ apiKey: "sk-provider", httpAgent: new https.Agent({ keepAlive: true }), maxRetries: 0, ...rt2.routerOptions() });
      assert.equal((await c.chat.completions.create({ model: "gpt-4o-mini", messages: HI })).choices[0].message.content, "pong");
    });
  } finally {
    prov.close();
    fs.rmSync(copy, { force: true });
  }
});

console.log(JSON.stringify(results));
process.exit(results.some((r) => r[0] !== "ok") ? 1 : 0);
