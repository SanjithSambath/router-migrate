// Runtime smoke using a target repo's own installed SDK versions.
// usage: node compat_check.mjs <repoDir> <rendered routerRuntime.ts>   (env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL)
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
const [repo, rtPath] = process.argv.slice(2);
const req = createRequire(repo + "/package.json");
const rt = await import(pathToFileURL(rtPath).href);
const out = {};
const imp = async (name) => { try { return await import(pathToFileURL(req.resolve(name)).href); } catch (e) { return null; } };
const ver = (name) => { try { return req(name + "/package.json").version; } catch { return null; } };
const OpenAIMod = await imp("openai");
if (OpenAIMod) {
  out.openai = ver("openai");
  const OpenAI = OpenAIMod.default || OpenAIMod.OpenAI;
  const c = new OpenAI({ apiKey: "sk-provider", ...rt.routerOptions() });
  const r = await c.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }] });
  out.chat = r.choices[0].message.content;
  const s = await c.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], stream: true });
  let t = ""; for await (const ch of s) t += ch.choices[0]?.delta?.content || ""; out.stream = t;
}
const ai = await imp("ai"), aio = await imp("@ai-sdk/openai");
if (ai && aio && aio.createOpenAI) {
  out.ai = ver("ai"); out["@ai-sdk/openai"] = ver("@ai-sdk/openai");
  const p = aio.createOpenAI({ apiKey: "sk-provider", ...rt.routerOptions() });
  const g = await ai.generateText({ model: p("gpt-4o-mini"), prompt: "hi" });
  out.generateText = g.text;
  if (p.chat) { const g2 = await ai.generateText({ model: p.chat("gpt-4o-mini"), prompt: "hi" }); out.generateText_chat = g2.text; }
  const st = ai.streamText({ model: p("gpt-4o-mini"), prompt: "hi" }); let x = ""; for await (const d of st.textStream) x += d; out.streamText = x;
}
const lc = await imp("@langchain/openai");
if (lc && lc.ChatOpenAI) {
  out["@langchain/openai"] = ver("@langchain/openai");
  const m = new lc.ChatOpenAI({ model: "gpt-4o-mini", modelName: "gpt-4o-mini", apiKey: "sk-provider", openAIApiKey: "sk-provider", configuration: { ...rt.routerOptions() } });
  const r = await m.invoke("hi"); out.langchain = typeof r.content === "string" ? r.content : JSON.stringify(r.content);
}
const an = await imp("@anthropic-ai/sdk");
if (an) {
  out["@anthropic-ai/sdk"] = ver("@anthropic-ai/sdk");
  const A = an.default || an.Anthropic;
  const a = new A({ apiKey: "sk-ant", ...rt.routerOptions() });
  const m = await a.messages.create({ model: "claude-haiku-4-5", max_tokens: 16, messages: [{ role: "user", content: "hi" }] });
  out.anthropic = m.content[0].text;
}
console.log(JSON.stringify(out));
const vals = Object.entries(out).filter(([k]) => !k.includes("/") && !["openai", "ai"].includes(k) && !k.startsWith("@")).map(([, v]) => v);
process.exit(vals.length && vals.every((v) => v === "pong") ? 0 : 1);
