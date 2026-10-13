// Adversarial review: TS runtime streaming translation against crafted upstream SSE (no network).
// Run: node research/review_scratch/ts_runtime/check.mts   (Node >= 23 strips types)
import { routerFetch } from "./routerRuntime.ts";
process.env.RAMP_ROUTER_API_KEY = "rm-review-sim-key";
process.env.RAMP_ROUTER_BASE_URL = "https://router.mock";
const shell = (status = "completed", inc: any = null) => ({ id: "resp_x", status, model: "gpt-4o-mini", incomplete_details: inc, usage: { input_tokens: 5, output_tokens: 7, total_tokens: 12 } });
const sse = (evs: any[], sep = "\n\n") => evs.map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}${sep}`).join("");
async function run(name: string, evs: any[], sep = "\n\n") {
  const inner = async (url: any, init: any) => new Response(sse(evs, sep), { status: 200, headers: { "content-type": "text/event-stream" } });
  const f = routerFetch(inner as any)!;
  const r = await f("https://api.openai.com/v1/chat/completions", { method: "POST", headers: { authorization: "Bearer sk-x" },
    body: JSON.stringify({ model: "gpt-4o-mini", stream: true, messages: [{ role: "user", content: "go" }] }) });
  const text = await r.text();
  const chunks = text.split("\n\n").filter((l) => l.startsWith("data: {")).map((l) => JSON.parse(l.slice(6)));
  const content = chunks.map((c) => c.choices?.[0]?.delta?.content || "").join("");
  const fin = chunks.map((c) => c.choices?.[0]?.finish_reason).filter(Boolean);
  console.log(name.padEnd(28), JSON.stringify({ content, finish: fin, n: chunks.length, done: text.includes("[DONE]") }));
}
const created = { type: "response.created", response: shell("in_progress") };
await run("LF framing", [created, { type: "response.output_text.delta", delta: "hello" }, { type: "response.completed", response: shell() }]);
await run("CRLF framing", [created, { type: "response.output_text.delta", delta: "hello" }, { type: "response.completed", response: shell() }], "\r\n\r\n");
await run("streamed refusal", [created, { type: "response.refusal.delta", delta: "I can't help." }, { type: "response.completed", response: shell() }]);
await run("content_filter incomplete", [created, { type: "response.output_text.delta", delta: "partial" }, { type: "response.incomplete", response: shell("incomplete", { reason: "content_filter" }) }]);
