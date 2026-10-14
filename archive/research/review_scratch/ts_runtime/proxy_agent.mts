// Repro: routerOptions() replaces the Anthropic SDK 0.19 default fetch (node-fetch, honours httpAgent) with
// globalThis.fetch (undici, ignores `agent`). opencommit's OCO_PROXY (httpAgent: new HttpsProxyAgent(proxy)) stops working.
// Local only: a fake "provider" and a fake proxy on 127.0.0.1. Run: node research/review_scratch/ts_runtime/proxy_agent.mts
import http from "node:http";
import net from "node:net";
import { createRequire } from "node:module";
import { routerOptions } from "./routerRuntime.ts";
const require = createRequire("/Users/sanjithsambath/code/router-migrate/eval/work/base/opencommit/package.json");
const Anthropic = require("@anthropic-ai/sdk").default;
const { HttpsProxyAgent } = await import("/Users/sanjithsambath/code/router-migrate/eval/work/base/opencommit/node_modules/https-proxy-agent/dist/index.js");

const hits: string[] = [];
const provider = http.createServer((req, res) => { hits.push("provider:direct"); res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ id: "m", type: "message", role: "assistant", model: "x", content: [{ type: "text", text: "ok" }], stop_reason: "end_turn", usage: { input_tokens: 1, output_tokens: 1 } })); });
const proxy = http.createServer();
proxy.on("connect", (req, sock: any, head) => { hits.push("proxy:CONNECT " + req.url); const [h, p] = String(req.url).split(":");
  const up = net.connect(Number(p), h, () => { sock.write("HTTP/1.1 200 OK\r\n\r\n"); up.write(head); up.pipe(sock); sock.pipe(up); }); });
await new Promise<void>((r) => provider.listen(0, "127.0.0.1", () => r()));
await new Promise<void>((r) => proxy.listen(0, "127.0.0.1", () => r()));
const baseURL = `http://127.0.0.1:${(provider.address() as any).port}`;
const agent = new HttpsProxyAgent(`http://127.0.0.1:${(proxy.address() as any).port}`);

async function call(label: string) {
  hits.length = 0;
  const opts: any = { apiKey: "sk-ant-test", baseURL, httpAgent: agent, maxRetries: 0 };
  const c = new Anthropic({ ...opts, ...routerOptions(opts.fetch) }); // exactly the opencommit patch
  await c.messages.create({ model: "claude-unmapped-model", max_tokens: 5, messages: [{ role: "user", content: "hi" }] });
  console.log(label.padEnd(34), hits.join(", "));
}
delete process.env.RAMP_ROUTER_API_KEY;
await call("Router OFF (key unset):");
process.env.RAMP_ROUTER_API_KEY = "rm-review-fake";
await call("Router ON (request not routable):");
provider.close(); proxy.close(); agent.destroy?.();
