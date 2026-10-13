// Exercises the rendered routerRuntime.ts through the real openai-node SDK against router-sim.
// usage: node runtime_check.mjs <path to rendered routerRuntime.ts>   (env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL)
import assert from "node:assert/strict";
import OpenAI from "openai";

const rt = await import(process.argv[2]);
const providerCalls = [];
const base = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.startsWith("http://127.0.0.1")) return fetch(input, init);
  providerCalls.push(url);
  if (url.endsWith("/embeddings"))
    return new Response(JSON.stringify({ object: "list", data: [{ object: "embedding", index: 0, embedding: [0.1] }], model: "e", usage: { prompt_tokens: 1, total_tokens: 1 } }), { headers: { "content-type": "application/json" } });
  return new Response(JSON.stringify({ id: "p", object: "chat.completion", created: 0, model: "provider", choices: [{ index: 0, message: { role: "assistant", content: "from-provider" }, finish_reason: "stop" }] }), { headers: { "content-type": "application/json" } });
};
const sim = process.env.RAMP_ROUTER_BASE_URL;
const lastReq = async () => (await (await fetch(sim + "/__last")).json());
const results = [];
async function t(name, fn) {
  try { await fn(); results.push(["ok", name]); } catch (e) { results.push(["FAIL", name, String(e && e.stack || e)]); }
}
const client = new OpenAI({ apiKey: "sk-provider", fetch: rt.routerFetch(base) });
const TOOLS = [{ type: "function", function: { name: "get_weather", parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] } } }];

await t("chat non-stream", async () => {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], max_tokens: 5 });
  assert.equal(r.choices[0].message.content, "pong");
  assert.equal(r.choices[0].finish_reason, "stop");
  const l = await lastReq();
  assert.equal(l.path, "/v1/responses"); assert.ok(l.valid, JSON.stringify(l));
  assert.equal(l.request.max_output_tokens, 16);
  assert.match(l.request.metadata.feature, /runtime_check\.mjs/);
});
await t("chat stream + usage", async () => {
  const s = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], stream: true, stream_options: { include_usage: true } });
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
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], stop: ["n"] });
  assert.equal(r.choices[0].message.content, "po");
  const s = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], stop: "ng", stream: true });
  let text = ""; for await (const ch of s) text += ch.choices[0]?.delta?.content || ""; assert.equal(text, "po");
});
await t("json_schema", async () => {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "x" }], response_format: { type: "json_schema", json_schema: { name: "City", schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] }, strict: true } } });
  assert.deepEqual(JSON.parse(r.choices[0].message.content), { name: "x" });
});
await t("passthrough: unknown model / n>1 / embeddings", async () => {
  providerCalls.length = 0;
  assert.equal((await client.chat.completions.create({ model: "gpt-4-32k", messages: [{ role: "user", content: "x" }] })).choices[0].message.content, "from-provider");
  assert.equal((await client.chat.completions.create({ model: "gpt-4o-mini", n: 2, messages: [{ role: "user", content: "x" }] })).choices[0].message.content, "from-provider");
  await client.embeddings.create({ model: "text-embedding-3-small", input: "x" });
  assert.equal(providerCalls.length, 3);
});
await t("responses passthrough sanitized", async () => {
  const r = await client.responses.create({ model: "gpt-4o-mini", input: "hi", store: true });
  assert.equal(r.output_text, "pong"); const l = await lastReq(); assert.ok(l.valid); assert.equal(l.request.store, undefined);
});
await t("router error surfaces", async () => {
  await assert.rejects(client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "tool", tool_call_id: "nope", content: "x" }] }), (e) => e.status === 400);
});
await t("anthropic-style fetch", async () => {
  const f = rt.routerFetch(base);
  const r = await f("https://api.anthropic.com/v1/messages", { method: "POST", headers: { "content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": "k" }, body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 16, messages: [{ role: "user", content: "hi" }] }) });
  const j = await r.json(); assert.equal(j.content[0].text, "pong");
  const l = await lastReq(); assert.equal(l.request.model, "claude-haiku-4-5"); assert.ok(l.valid);
});
console.log(JSON.stringify(results));
process.exit(results.some((r) => r[0] !== "ok") ? 1 : 0);
