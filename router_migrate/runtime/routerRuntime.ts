// routerRuntime.ts (vendored by router-migrate; safe to read, edit or delete)
//
// Routes this codebase's OpenAI / Anthropic SDK traffic through Ramp's Router (https://router.com) when
// RAMP_ROUTER_API_KEY is set. When it is not set, routerFetch() returns the fetch you passed in (or undefined,
// which makes every SDK use its default), so behavior is unchanged.
//
// routerFetch() wraps fetch: requests Router can serve exactly go to Router, everything else goes to the original
// provider unchanged.
//   POST .../chat/completions -> {router}/v1/responses   (request, JSON reply and SSE stream translated back)
//   POST .../responses        -> {router}/v1/responses   (store dropped; provider-side state stays on the provider)
//   POST .../v1/messages      -> {router}/v1/messages    (Anthropic; model id mapped)
//   anything else (embeddings, images, files, unknown models, n>1, logprobs, seed, audio) -> original provider
// Router does not store responses, so a Responses chain started on Router is continued in this process: the last 256
// Router responses are kept in memory, a previous_response_id naming one is replaced by the full history inline, and
// GET /responses/{id} (and /input_items) is answered locally. A Router id that has been evicted gets a clear 400/404
// instead of being sent to a provider that never saw it.
// If Router rejects a routed request before generating (400 unknown/unsupported parameter or invalid value, 403,
// 404 model_not_found), the original request is resent to the provider once.
// Env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL (default https://api.router.com),
//      ROUTER_MIGRATE_DISABLE=1|true|yes|on (kill switch), ROUTER_MIGRATE_MODEL_MAP='{"old":"router-id"}',
//      ROUTER_MIGRATE_SERVICE_TIER (default "default"; "auto" sends none),
//      ROUTER_MIGRATE_FEATURE_TAG=off (no metadata.feature / automatic prompt_cache_key from the call stack),
//      ROUTER_MIGRATE_POLICY=off (ignore the compiled router.policy.json; see "v2 compiled policy" below).
/* eslint-disable */
// @ts-nocheck

type AnyFetch = (input: any, init?: any) => Promise<Response>;

const CATALOG_JSON = String.raw`__ROUTER_CATALOG__`;
let CATALOG: Record<string, { o?: string; t?: boolean | null; r?: boolean; c?: { tools?: boolean; so?: boolean; in?: string[]; st?: string; s?: string[]; f?: boolean } }> = {};
let ALIASES: Record<string, string> = {};
let DATED: Record<string, string> = {}; // dated provider snapshot -> catalog id, only where Router reported the equivalence
try {
  const d = JSON.parse(CATALOG_JSON);
  CATALOG = d.models || {};
  ALIASES = d.aliases || {};
  DATED = d.dated || {};
} catch {}

const OPENAI_HOSTS = ["api.openai.com"];
const ANTHROPIC_HOSTS = ["api.anthropic.com"];
const MAX_ROUTER_IDS = 10000;
const MAX_CHAINS = 256;
const METADATA_MAX_KEYS = 16;
const FALLBACK_400_CODES = ["unknown_parameter", "unsupported_parameter", "invalid_value"];
const DROP_HEADERS = ["content-length", "content-encoding", "transfer-encoding"];

function env(name: string): string | undefined {
  try {
    return typeof process !== "undefined" && process.env ? process.env[name] : undefined;
  } catch {
    return undefined;
  }
}

export function routerActive(): boolean {
  const off = (env("ROUTER_MIGRATE_DISABLE") || "").trim().toLowerCase();
  return !!env("RAMP_ROUTER_API_KEY") && !["1", "true", "yes", "on"].includes(off);
}

function featureTagOn(): boolean {
  return (env("ROUTER_MIGRATE_FEATURE_TAG") || "").trim().toLowerCase() !== "off";
}

function routerBase(): string {
  let b = (env("RAMP_ROUTER_BASE_URL") || "https://api.router.com").replace(/\/+$/, "");
  return b.endsWith("/v1") ? b.slice(0, -3) : b;
}

/** service_tier for routed requests the caller did not set one on: none (Router's account default, Flex where offered) unless
 * ROUTER_MIGRATE_SERVICE_TIER forces one. Latency guardrails come from the compiled policy (provider_timeout, service_tier). */
function defaultTier(): string | undefined {
  const t = (env("ROUTER_MIGRATE_SERVICE_TIER") || "").trim();
  return !t || t.toLowerCase() === "auto" ? undefined : t;
}

export function resolveModel(name: unknown): string | null {
  if (typeof name !== "string" || !name) return null;
  let override: Record<string, string> = {};
  try {
    override = JSON.parse(env("ROUTER_MIGRATE_MODEL_MAP") || "{}");
  } catch {}
  if (name in override) return override[name] || null;
  if (CATALOG[name]) return name;
  if (ALIASES[name]) return ALIASES[name];
  const ci = Object.keys(CATALOG).find((k) => k.toLowerCase() === name.toLowerCase()); // e.g. MiniMax-M3 -> minimax-m3
  return ci || DATED[name] || null;
}

// --- Router-issued response ids and in-process chains (bounded LRUs, shared by every copy of this module in the process)
// Router keeps no server-side state, so a Responses chain started on Router is continued here: each Router response's
// input + output is remembered and sent inline on the next turn (what Router's own error message asks callers to do).

function sharedMap(name: string): Map<string, any> {
  try {
    const k = Symbol.for(name);
    const g: any = globalThis as any;
    if (!(g[k] instanceof Map)) g[k] = new Map();
    return g[k];
  } catch {
    return new Map();
  }
}
const ROUTER_IDS: Map<string, true> = sharedMap("router-migrate.routerResponseIds"); // outlives CHAINS: never sent to the provider
const CHAINS: Map<string, { items: any[]; output: any[]; response: any }> = sharedMap("router-migrate.routerChains");

function lruPut(m: Map<string, any>, k: string, v: any, cap: number) {
  m.delete(k);
  m.set(k, v);
  while (m.size > cap) m.delete(m.keys().next().value);
}

function lruGet(m: Map<string, any>, k: unknown): any {
  if (typeof k !== "string" || !m.has(k)) return undefined;
  const v = m.get(k);
  lruPut(m, k, v, Infinity);
  return v;
}

function rememberRouterId(id: unknown) {
  if (typeof id === "string" && id) lruPut(ROUTER_IDS, id, true, MAX_ROUTER_IDS);
}

function isRouterId(id: unknown): boolean {
  return lruGet(ROUTER_IDS, id) !== undefined;
}

/** Remember a Router response (and the full input it answered) so the next turn can continue the chain inline. */
function rememberChain(items: any[] | undefined, resp: any) {
  const id = resp?.id;
  if (typeof id !== "string" || !id || !Array.isArray(items)) return;
  rememberRouterId(id);
  const output = (Array.isArray(resp.output) ? resp.output : []).filter((o: any) => plainObject(o));
  lruPut(CHAINS, id, { items, output, response: resp }, MAX_CHAINS);
}

function asItems(input: any): any[] {
  if (input == null) return [];
  if (typeof input === "string") return [{ role: "user", content: input }];
  if (!Array.isArray(input)) throw new Untranslatable("input");
  return [...input];
}

function stateError(status: number, message: string): Response {
  const error = { message: `router-migrate: ${message}`, type: "invalid_request_error", code: "router_stateful_unsupported" };
  return new Response(JSON.stringify({ error }), { status, headers: { "content-type": "application/json" } });
}

const evictedPrev = (id: string) =>
  stateError(400, `previous_response_id ${id} was served by Router and its history is no longer in this process (router-migrate keeps the last ${MAX_CHAINS} chains in memory). Send the conversation inline, or set ROUTER_MIGRATE_DISABLE=1.`);
const evictedGet = (id: string) => stateError(404, `Response ${id} was served by Router and is no longer in this process's memory.`);

function feature(): string | undefined {
  const stack = new Error().stack || "";
  for (const line of stack.split("\n").slice(1)) {
    if (/node_modules|routerRuntime|node:internal|\(native\)|<anonymous>/.test(line)) continue;
    const m = line.match(/at (?:(.+?) \()?(?:file:\/\/)?(.+?):\d+:\d+\)?$/);
    if (!m) continue;
    let file = m[2];
    try {
      const cwd = typeof process !== "undefined" && process.cwd ? process.cwd() : "";
      if (cwd && file.startsWith(cwd)) file = file.slice(cwd.length + 1);
    } catch {}
    const fn = (m[1] || "<module>").replace(/^async /, "");
    return `${file}:${fn}`.slice(0, 200);
  }
  return undefined;
}

class Untranslatable extends Error {}

function plainObject(v: any): boolean {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** Caller metadata plus metadata.feature, unless the caller already set it or is at OpenAI's 16-key limit. */
function withFeature(meta: any, feat: string | undefined): Record<string, any> {
  if (meta != null && !plainObject(meta)) throw new Untranslatable("metadata");
  const m: Record<string, any> = { ...(meta || {}) };
  if (feat && !("feature" in m) && Object.keys(m).length < METADATA_MAX_KEYS) m.feature = feat;
  return m;
}

function textOf(c: any): string {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.filter((p) => p && ["text", "input_text", "output_text"].includes(p.type)).map((p) => p.text || "").join("");
  return c == null ? "" : String(c);
}

function userParts(c: any): any {
  if (typeof c === "string" || c == null) return c || "";
  return c.map((p: any) => {
    if (p?.type === "text") return { type: "input_text", text: p.text || "" };
    if (p?.type === "image_url") {
      const iu = p.image_url;
      const part: any = { type: "input_image", image_url: typeof iu === "string" ? iu : iu?.url };
      if (iu && typeof iu === "object" && iu.detail) part.detail = iu.detail;
      return part;
    }
    if (p?.type === "file") {
      const f = p.file || {};
      if (f.file_id) throw new Untranslatable("file_id lives on the provider account");
      const part: any = { type: "input_file" };
      for (const k of ["file_data", "filename"]) if (f[k]) part[k] = f[k];
      return part;
    }
    throw new Untranslatable(`content part ${p?.type}`);
  });
}

/** Assistant history turn: plain text, or a list of output parts when it carries a refusal. */
function assistantContent(m: any): any {
  const parts: any[] = [];
  if (Array.isArray(m.content)) {
    for (const p of m.content) {
      if (!p) continue;
      if (p.type === "refusal") parts.push({ type: "refusal", refusal: p.refusal || "" });
      else if (["text", "output_text", "input_text"].includes(p.type)) {
        if (p.text) parts.push({ type: "output_text", text: p.text, annotations: [] });
      } else throw new Untranslatable(`assistant content part ${p.type}`);
    }
  } else {
    const t = textOf(m.content);
    if (t) parts.push({ type: "output_text", text: t, annotations: [] });
  }
  if (typeof m.refusal === "string" && m.refusal && !parts.some((p) => p.type === "refusal")) parts.push({ type: "refusal", refusal: m.refusal });
  if (!parts.length) return null;
  if (!parts.some((p) => p.type === "refusal")) return parts.map((p) => p.text).join("");
  return parts;
}

export function chatToResponses(body: any, routerModel: string, ownedBy: string | undefined, feat: string | undefined) {
  const unsupported = ["audio", "modalities", "prediction", "web_search_options", "logit_bias"].filter((k) => body[k]);
  if ((body.n ?? 1) !== 1) unsupported.push("n");
  if (body.logprobs || body.top_logprobs) unsupported.push("logprobs");
  if (body.seed != null) unsupported.push("seed"); // Router rejects seed (measured 400); keep reproducibility on the provider
  if (body.presence_penalty != null && body.presence_penalty !== 0) unsupported.push("presence_penalty"); // unmeasured on Router
  if (unsupported.length) throw new Untranslatable(unsupported.join(","));
  const info = CATALOG[routerModel] || {};
  const items: any[] = [];
  const legacy: Record<string, string> = {};
  (body.messages || []).forEach((m: any, i: number) => {
    const role = m.role;
    // Responses has no speaker names; on a tool result the name only repeats the tool's (the output is keyed by call id)
    if (m.name != null && role !== "function" && role !== "tool") throw new Untranslatable("message name");
    if (role === "system" || role === "developer") items.push({ role, content: textOf(m.content) });
    else if (role === "user") items.push({ role: "user", content: userParts(m.content) });
    else if (role === "assistant") {
      const c = assistantContent(m);
      if (c) items.push({ role: "assistant", content: c });
      for (const tc of m.tool_calls || []) {
        if (tc.type && tc.type !== "function") throw new Untranslatable(`tool call type ${tc.type}`);
        const fn = tc.function || {};
        items.push({ type: "function_call", call_id: tc.id, name: fn.name, arguments: typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(fn.arguments || {}) });
      }
      if (m.function_call) {
        const cid = `call_legacy_${i}`;
        legacy[m.function_call.name] = cid;
        items.push({ type: "function_call", call_id: cid, name: m.function_call.name, arguments: m.function_call.arguments || "{}" });
      }
    } else if (role === "tool") items.push({ type: "function_call_output", call_id: m.tool_call_id, output: textOf(m.content) });
    else if (role === "function") items.push({ type: "function_call_output", call_id: legacy[m.name] || "call_legacy", output: textOf(m.content) });
    else throw new Untranslatable(`role ${role}`);
  });
  const out: any = { model: routerModel, input: items };
  const mot = body.max_completion_tokens ?? body.max_tokens;
  if (mot != null) {
    const n = Number(mot);
    if (!(n >= 16)) throw new Untranslatable("max_tokens below Router's minimum of 16"); // never raise the caller's cap
    out.max_output_tokens = n;
  }
  const tempOk = info.t !== false;
  for (const k of ["temperature", "top_p"]) if (body[k] != null && tempOk) out[k] = body[k];
  for (const k of ["frequency_penalty", "parallel_tool_calls", "user", "service_tier", "prompt_cache_key", "safety_identifier"])
    if (body[k] != null) out[k] = body[k];
  const tools: any[] = [];
  for (const t of body.tools || []) {
    if (t.type !== "function") throw new Untranslatable(`tool type ${t.type}`);
    const fn = t.function || {};
    const nt: any = { type: "function", name: fn.name, parameters: fn.parameters || { type: "object", properties: {} } };
    if (fn.description) nt.description = fn.description;
    nt.strict = !!fn.strict; // Responses defaults tools to strict; Chat Completions does not
    tools.push(nt);
  }
  for (const fn of body.functions || []) tools.push({ type: "function", name: fn.name, description: fn.description || "", parameters: fn.parameters || { type: "object", properties: {} }, strict: false });
  if (tools.length) out.tools = tools;
  const modern = body.tool_choice != null;
  const tc = modern ? body.tool_choice : body.function_call;
  if (tc != null) {
    if (typeof tc === "string") {
      if (!["auto", "none", "required"].includes(tc)) throw new Untranslatable(`tool_choice ${tc}`);
      out.tool_choice = tc;
    } else if (plainObject(tc)) {
      const name = modern ? (tc.type === "function" ? tc.function?.name : undefined) : tc.name;
      if (!name) throw new Untranslatable(`tool_choice ${tc.type}`); // allowed_tools, custom, ...
      out.tool_choice = { type: "function", name };
    } else throw new Untranslatable("tool_choice");
  }
  const rf = body.response_format;
  if (rf && (rf.type === "json_object" || rf.type === "json_schema")) {
    let fmt: any;
    if (rf.type === "json_object") fmt = { type: "json_object" };
    else {
      const js = rf.json_schema || {};
      fmt = { type: "json_schema", name: js.name || "response", schema: js.schema || {} };
      if (js.strict != null) fmt.strict = js.strict;
      if (js.description) fmt.description = js.description;
    }
    out.text = { format: fmt };
  }
  if (body.verbosity) out.text = { ...(out.text || {}), verbosity: body.verbosity };
  if (body.reasoning_effort && info.r && !(ownedBy === "anthropic" && (out.max_output_tokens ?? 4096) < 1100)) out.reasoning = { effort: body.reasoning_effort };
  let stops: string[] = typeof body.stop === "string" ? [body.stop] : (body.stop || []).filter(Boolean);
  if (stops.length && ownedBy !== "openai") out.stop = stops;
  const meta = withFeature(body.metadata, feat);
  if (Object.keys(meta).length) out.metadata = Object.fromEntries(Object.entries(meta).map(([k, v]) => [k, String(v).slice(0, 512)]));
  if (ownedBy === "anthropic" && !out.prompt_cache_key && feat) out.prompt_cache_key = feat;
  if (body.stream) out.stream = true;
  if (out.service_tier == null) {
    const tier = defaultTier();
    if (tier) out.service_tier = tier;
  }
  const ctx = { stops: ownedBy === "openai" ? stops : [], includeUsage: !!body.stream_options?.include_usage, model: body.model, legacy: !!body.functions };
  return { body: out, ctx };
}

/** True when a /responses request depends on state that lives on the provider account (H2). */
function responsesNeedsProvider(body: any): boolean {
  for (const t of Array.isArray(body.tools) ? body.tools : []) {
    if (!t || typeof t !== "object") continue;
    if (t.type === "file_search") return true; // vector stores live on the provider account
    if (t.type === "code_interpreter" && plainObject(t.container) && Array.isArray(t.container.file_ids) && t.container.file_ids.length) return true;
  }
  for (const it of Array.isArray(body.input) ? body.input : []) {
    if (!it || typeof it !== "object") continue;
    if (it.type === "item_reference") return true; // points at an item stored on the provider
    if (it.type === "reasoning" && !it.encrypted_content) return true; // only resolvable from provider storage
    for (const parts of [it.content, it.output]) {
      if (!Array.isArray(parts)) continue;
      for (const p of parts) if (p && typeof p === "object" && p.file_id) return true; // input_file / input_image by file_id
    }
  }
  return false;
}

function usageOf(u: any) {
  if (!u) return undefined;
  const d: any = {
    prompt_tokens: u.input_tokens || 0,
    completion_tokens: u.output_tokens || 0,
    total_tokens: u.total_tokens ?? (u.input_tokens || 0) + (u.output_tokens || 0),
    prompt_tokens_details: { cached_tokens: u.input_tokens_details?.cached_tokens || 0 },
    completion_tokens_details: { reasoning_tokens: u.output_tokens_details?.reasoning_tokens || 0 },
  };
  if (u.cost != null) d.cost = u.cost;
  return d;
}

function cut(text: string, stops: string[]): [string, boolean] {
  let hit = -1;
  for (const s of stops) {
    const k = text.indexOf(s);
    if (k >= 0 && (hit < 0 || k < hit)) hit = k;
  }
  return hit < 0 ? [text, false] : [text.slice(0, hit), true];
}

function finishReason(inc: any, stopped: boolean, sawCall: boolean): string {
  if (inc === "content_filter") return "content_filter";
  if (inc === "max_output_tokens" && !stopped) return "length"; // also a tool call cut off mid-arguments
  return sawCall ? "tool_calls" : "stop";
}

export function responsesToChat(resp: any, ctx: any) {
  let text = "";
  let refusal: string | null = null;
  const calls: any[] = [];
  for (const it of resp.output || []) {
    if (it.type === "message") {
      for (const c of it.content || []) {
        if (c.type === "output_text") text += c.text || "";
        else if (c.type === "refusal") refusal = (refusal || "") + (c.refusal || "");
      }
    } else if (it.type === "function_call") calls.push({ id: it.call_id, type: "function", function: { name: it.name, arguments: it.arguments || "" } });
  }
  const [t, stopped] = cut(text, ctx.stops || []);
  let finish = finishReason(resp.incomplete_details?.reason, stopped, calls.length > 0);
  const msg: any = { role: "assistant", content: t || !calls.length ? t : null, refusal };
  if (calls.length) {
    if (ctx.legacy) {
      msg.function_call = calls[0].function;
      if (finish === "tool_calls") finish = "function_call";
    } else msg.tool_calls = calls;
  }
  const out: any = {
    id: `chatcmpl-${resp.id}`,
    object: "chat.completion",
    created: Math.floor(resp.created_at || Date.now() / 1000),
    model: resp.model || ctx.model,
    system_fingerprint: null,
    service_tier: resp.service_tier,
    choices: [{ index: 0, message: msg, finish_reason: finish, logprobs: null }],
  };
  const u = usageOf(resp.usage);
  if (u) out.usage = u;
  if (resp.provider) out.provider = resp.provider;
  return out;
}

const SSE_SEP = /\r?\n\r?\n/;

/** Split complete SSE events off the front of buf; returns [events, rest]. */
function sseSplit(buf: string): [string[], string] {
  const evs: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = SSE_SEP.exec(buf))) {
    evs.push(buf.slice(0, m.index));
    buf = buf.slice(m.index + m[0].length);
  }
  return [evs, buf];
}

function sseData(raw: string): any {
  const data = raw
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => (l[5] === " " ? l.slice(6) : l.slice(5)))
    .join("\n");
  if (!data || data.trim() === "[DONE]") return undefined;
  try {
    return JSON.parse(data);
  } catch {
    return undefined;
  }
}

/** A WHATWG ReadableStream for a fetch body (undici gives one; node-fetch gives a Node stream). */
function webStream(body: any): ReadableStream<Uint8Array> {
  if (body && typeof body.getReader === "function") return body;
  const it = body[Symbol.asyncIterator]();
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { value, done } = await it.next();
        if (done) controller.close();
        else controller.enqueue(typeof value === "string" ? enc.encode(value) : value);
      } catch (e) {
        controller.error(e);
      }
    },
    async cancel() {
      try {
        await it.return?.();
      } catch {}
    },
  });
}

function chunkStream(upstream: ReadableStream<Uint8Array>, ctx: any): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  let buf = "";
  let id = "router";
  let model = ctx.model;
  const created = Math.floor(Date.now() / 1000);
  let role = false;
  let stopped = false;
  let sawCall = false;
  let tail = "";
  let done = false;
  let nCalls = 0;
  const toolIndex: Record<string, number> = {};
  const streamedArgs: Record<string, string> = {};
  const maxStop = Math.max(0, ...(ctx.stops || []).map((s: string) => s.length));
  const chunk = (delta: any, finish: string | null = null, usage?: any, withChoices = true) => {
    const d: any = { id: `chatcmpl-${id}`, object: "chat.completion.chunk", created, model, system_fingerprint: null, choices: withChoices ? [{ index: 0, delta, finish_reason: finish, logprobs: null }] : [] };
    if (usage !== undefined) d.usage = usage;
    return enc.encode(`data: ${JSON.stringify(d)}\n\n`);
  };
  const ensureRole = (ev: any, emit: (b: Uint8Array) => void) => {
    if (role) return;
    id = ev?.response?.id || id;
    model = ev?.response?.model || model;
    role = true;
    emit(chunk({ role: "assistant", content: "" }));
  };
  const textOut = (s: string, final: boolean, emit: (b: Uint8Array) => void) => {
    if (stopped) return;
    if (!maxStop) {
      if (s) emit(chunk({ content: s }));
      return;
    }
    tail += s;
    const [c, hit] = cut(tail, ctx.stops);
    if (hit) {
      stopped = true;
      if (c) emit(chunk({ content: c }));
      return;
    }
    const keep = final ? 0 : maxStop - 1;
    const e = keep ? tail.slice(0, Math.max(0, tail.length - keep)) : tail;
    tail = keep ? tail.slice(Math.max(0, tail.length - keep)) : "";
    if (e) emit(chunk({ content: e }));
  };
  // legacy `functions` requests get delta.function_call ({name, arguments} first, then {arguments}); others delta.tool_calls
  const callDelta = (key: string, fn: any, callId?: string) => {
    if (ctx.legacy) return chunk({ function_call: fn });
    const tc: any = { index: toolIndex[key] ?? 0, function: fn };
    if (callId !== undefined) Object.assign(tc, { id: callId, type: "function" });
    return chunk({ tool_calls: [tc] });
  };
  const announceCall = (item: any, emit: (b: Uint8Array) => void) => {
    const key = item.id || item.call_id;
    if (key in toolIndex) return key;
    toolIndex[key] = nCalls++;
    streamedArgs[key] = "";
    sawCall = true;
    emit(callDelta(key, { name: item.name, arguments: "" }, item.call_id));
    return key;
  };
  const argsOut = (key: string, s: string, emit: (b: Uint8Array) => void) => {
    if (!s) return;
    if (key in streamedArgs) streamedArgs[key] += s;
    emit(callDelta(key, { arguments: s }));
  };
  const argsFinal = (key: string, full: unknown, emit: (b: Uint8Array) => void) => {
    if (typeof full !== "string" || !(key in streamedArgs)) return;
    const have = streamedArgs[key];
    if (full.length > have.length && full.startsWith(have)) argsOut(key, full.slice(have.length), emit);
  };
  const finish = (resp: any, emit: (b: Uint8Array) => void) => {
    ensureRole(null, emit);
    textOut("", true, emit);
    let fin = finishReason(resp?.incomplete_details?.reason, stopped, sawCall);
    if (fin === "tool_calls" && ctx.legacy) fin = "function_call";
    emit(chunk({}, fin));
    if (ctx.includeUsage) emit(chunk({}, null, usageOf(resp?.usage), false));
    emit(enc.encode("data: [DONE]\n\n"));
    done = true;
  };
  const handle = (raw: string, emit: (b: Uint8Array) => void) => {
    if (done) return;
    const ev = sseData(raw);
    if (!ev || typeof ev !== "object") return;
    const t = ev.type || "";
    if (t === "response.completed" || t === "response.incomplete") return finish(ev.response, emit);
    if (t === "response.failed" || t === "error") {
      const err = ev.response?.error || ev.error || { message: ev.message || "Router stream error" };
      emit(enc.encode(`data: ${JSON.stringify({ error: err })}\n\n`));
      emit(enc.encode("data: [DONE]\n\n"));
      done = true;
      return;
    }
    ensureRole(ev, emit);
    if (t === "response.output_text.delta") textOut(ev.delta || "", false, emit);
    else if (t === "response.refusal.delta") {
      if (ev.delta) emit(chunk({ refusal: ev.delta }));
    } else if (t === "response.output_item.added" && ev.item?.type === "function_call") announceCall(ev.item, emit);
    else if (t === "response.function_call_arguments.delta") {
      const key = ev.item_id in toolIndex ? ev.item_id : announceCall({ id: ev.item_id, call_id: ev.call_id, name: ev.name }, emit);
      argsOut(key, ev.delta || "", emit);
    }
    else if (t === "response.function_call_arguments.done") argsFinal(ev.item_id, ev.arguments, emit);
    else if (t === "response.output_item.done" && ev.item?.type === "function_call") argsFinal(announceCall(ev.item, emit), ev.item.arguments, emit);
  };
  const reader = upstream.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const emit = (b: Uint8Array) => controller.enqueue(b);
      while (true) {
        if (done) {
          try {
            await reader.cancel();
          } catch {}
          controller.close();
          return;
        }
        const { value, done: end } = await reader.read();
        if (end) {
          buf += dec.decode();
          const [evs, rest] = sseSplit(buf);
          for (const raw of evs) handle(raw, emit);
          if (rest.trim()) handle(rest, emit); // last event without a trailing blank line
          buf = "";
          if (!done) finish(null, emit);
          controller.close();
          return;
        }
        buf += dec.decode(value, { stream: true });
        const [evs, rest] = sseSplit(buf);
        buf = rest;
        let produced = false;
        for (const raw of evs)
          handle(raw, (b) => {
            produced = true;
            emit(b);
          });
        if (produced) return;
      }
    },
    cancel() {
      return reader.cancel();
    },
  });
}

/** Pass a /responses SSE stream through byte for byte, remembering the response Router issued (id and chain). */
function recordIdsStream(upstream: ReadableStream<Uint8Array>, items: any[] | undefined): ReadableStream<Uint8Array> {
  const dec = new TextDecoder();
  let buf = "";
  const scan = (raws: string[]) => {
    for (const raw of raws) {
      const ev = sseData(raw);
      if (!ev || typeof ev.type !== "string" || !ev.type.startsWith("response.") || !ev.response?.id) continue;
      rememberRouterId(ev.response.id);
      if (ev.type === "response.completed" || ev.type === "response.incomplete") rememberChain(items, ev.response);
    }
  };
  const reader = upstream.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (done) {
          const [evs, rest] = sseSplit(buf + dec.decode());
          scan(rest.trim() ? [...evs, rest] : evs);
          controller.close();
          return;
        }
        controller.enqueue(value);
        const [evs, rest] = sseSplit(buf + dec.decode(value, { stream: true }));
        buf = rest;
        scan(evs);
      } catch (e) {
        controller.error(e);
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

function hostPath(url: string): [string, string] {
  try {
    const u = new URL(url);
    return [u.host, u.pathname];
  } catch {
    return ["", ""];
  }
}

function routerHeaders(orig: Headers, anthropic: boolean): Headers {
  const h = new Headers();
  h.set("authorization", `Bearer ${env("RAMP_ROUTER_API_KEY")}`);
  h.set("content-type", "application/json");
  h.set("accept", orig.get("accept") || "application/json");
  h.set("user-agent", orig.get("user-agent") || "router-migrate-runtime/0.1");
  if (anthropic) h.set("anthropic-version", orig.get("anthropic-version") || "2023-06-01"); // anthropic-beta never: see dropBetas
  return h;
}

/** Upstream headers for a rebuilt Response (the body is re-encoded, so length/encoding headers no longer apply). */
function copyHeaders(up: any): Headers {
  const h = new Headers();
  try {
    up.headers.forEach((v: string, k: string) => {
      if (DROP_HEADERS.includes(k.toLowerCase())) return;
      try {
        h.append(k, v);
      } catch {}
    });
  } catch {}
  return h;
}

/** Router errors that happen before any generation and that the provider can serve. */
function providerCanServe(status: number, text: string): boolean {
  if (status === 403) return true;
  if (status !== 400 && status !== 404) return false;
  let e: any;
  try {
    e = JSON.parse(text)?.error;
  } catch {
    return false;
  }
  if (!e || typeof e !== "object") return false;
  if (status === 400) return FALLBACK_400_CODES.includes(e.code);
  return e.code === "model_not_found" || (!e.code && /model/i.test(String(e.message || ""))); // Anthropic-shaped errors carry no code
}

let NODE_FETCH: Promise<AnyFetch | null> | null = null;
/** The host app's node-fetch (what openai-node v4 / @anthropic-ai/sdk 0.x use by default, and what honours `agent`). */
function nodeFetch(): Promise<AnyFetch | null> {
  if (!NODE_FETCH)
    NODE_FETCH = (async () => {
      try {
        const spec = "node-fetch";
        const m: any = await import(/* webpackIgnore: true */ /* @vite-ignore */ spec);
        if (typeof m?.default === "function") return m.default;
        if (typeof m?.default?.default === "function") return m.default.default;
        return typeof m === "function" ? m : null;
      } catch {
        return null; // edge runtimes, bundles without node-fetch
      }
    })();
  return NODE_FETCH;
}

/** The SDK's http(s) agent for a request to another URL: dropped only when it is a plain Agent for the other protocol. */
function agentFor(agent: any) {
  return (u: any) => {
    let a: any;
    try {
      a = typeof agent === "function" ? agent(u) : agent;
    } catch {
      return undefined;
    }
    const d = a && typeof a === "object" ? Object.getOwnPropertyDescriptor(a, "protocol") : undefined;
    if (d && typeof d.value === "string" && u && typeof u.protocol === "string" && d.value !== u.protocol) return undefined;
    return a;
  };
}

// --- v2 compiled policy (router.policy.json -> routerPolicy.ts, which sets globalThis.__ROUTER_MIGRATE_POLICY__ =
// {policy, hash} when imported). Applied only to Router-bound POST /v1/responses bodies (translated chat and the Responses
// passthrough), never to /v1/messages. No policy, an invalid one, or ROUTER_MIGRATE_POLICY=off => v1 bodies, byte for byte.
// Policy never changes which model answers: no model key, no generation params; fallbacks only add candidates after the
// original. Logs go to console.warn and carry no request content: the policy hash once, each guard skip once per
// (feature, key).

type PolicyEntry = { allow_flex_tier?: boolean; prompt_cache_key?: string; metadata?: Record<string, string>; fallbacks?: string[]; provider_timeout?: number };
type LoadedPolicy = { hash: string; defaults?: PolicyEntry; features: Record<string, PolicyEntry>; globs: [string, RegExp, PolicyEntry][] };

const POLICY_KEYS = ["allow_flex_tier", "prompt_cache_key", "metadata", "fallbacks", "provider_timeout", "service_tier", "model_map", "drop_params"];
const POLICY_TOP_KEYS = ["version", "defaults", "globs", "features"];
const MAX_FALLBACKS = 14; // Router takes 1-15 candidates; the original model is always first
const POLICY_LOGS: Map<string, true> = sharedMap("router-migrate.policyLogs");
const POLICY_CACHE: WeakMap<object, LoadedPolicy | null> = new WeakMap();
const own = (o: any, k: string) => Object.prototype.hasOwnProperty.call(o, k);

function policyLog(key: string, msg: string) {
  if (POLICY_LOGS.has(key)) return;
  lruPut(POLICY_LOGS, key, true, MAX_ROUTER_IDS);
  try {
    console.warn(msg);
  } catch {}
}

function validEntry(e: any): boolean {
  if (!plainObject(e)) return false;
  for (const k of Object.keys(e)) {
    const v = e[k];
    if (!POLICY_KEYS.includes(k)) return false;
    if (k === "allow_flex_tier" && typeof v !== "boolean") return false;
    if (k === "prompt_cache_key" && typeof v !== "string") return false;
    if (k === "metadata" && !(plainObject(v) && Object.values(v).every((x) => typeof x === "string"))) return false;
    if (k === "fallbacks" && !(Array.isArray(v) && v.length >= 1 && v.length <= MAX_FALLBACKS && v.every((x) => typeof x === "string" && !!x))) return false;
    if (k === "provider_timeout" && !(typeof v === "number" && Number.isFinite(v) && v >= 1 && v <= 660)) return false;
    if (k === "service_tier" && !(v === "default" || v === "flex")) return false;
    if (k === "model_map" && !(plainObject(v) && Object.keys(v).length && Object.entries(v).every(([a, b]) => !!a && typeof b === "string" && !!b))) return false;
    if (k === "drop_params" && !(Array.isArray(v) && v.length && v.every((x) => typeof x === "string" && !!x))) return false;
  }
  return true;
}

function validPolicy(p: any): boolean {
  if (!plainObject(p) || p.version !== 1) return false;
  if (!Object.keys(p).every((k) => POLICY_TOP_KEYS.includes(k))) return false;
  if (own(p, "defaults") && !validEntry(p.defaults)) return false;
  for (const k of ["globs", "features"]) if (own(p, k) && !(plainObject(p[k]) && Object.values(p[k]).every(validEntry))) return false;
  return true;
}

/** fnmatch on a feature id: `*` and `?` stay within a path segment, `**` matches anything, `[...]` is a character class. */
export function globToRegExp(pat: string): RegExp {
  let re = "";
  for (let i = 0; i < pat.length; i++) {
    const c = pat[i];
    if (c === "*") {
      if (pat[i + 1] === "*") {
        while (pat[i + 1] === "*") i++;
        re += ".*";
      } else re += "[^/]*";
    } else if (c === "?") re += "[^/]";
    else if (c === "[") {
      let j = i + 1;
      if (pat[j] === "!") j++;
      if (pat[j] === "]") j++;
      while (j < pat.length && pat[j] !== "]") j++;
      if (j >= pat.length) re += "\\[";
      else {
        let cls = pat.slice(i + 1, j).replace(/\\/g, "\\\\");
        if (cls[0] === "!") cls = "^" + cls.slice(1);
        else if (cls[0] === "^") cls = "\\" + cls;
        re += `[${cls}]`;
        i = j;
      }
    } else re += c.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
  }
  return new RegExp(`^${re}$`, "s");
}

/** The compiled policy, validated (cached per policy object); null when absent, invalid or ROUTER_MIGRATE_POLICY=off. */
function activePolicy(): LoadedPolicy | null {
  if ((env("ROUTER_MIGRATE_POLICY") || "").trim().toLowerCase() === "off") return null;
  let g: any;
  try {
    g = (globalThis as any).__ROUTER_MIGRATE_POLICY__;
  } catch {
    return null;
  }
  if (g == null) return null;
  if (typeof g !== "object") {
    policyLog("invalid", "router-migrate policy invalid: ignored (v1 behaviour)");
    return null;
  }
  if (POLICY_CACHE.has(g)) return POLICY_CACHE.get(g) || null;
  let loaded: LoadedPolicy | null = null;
  let hash = "";
  try {
    hash = typeof g.hash === "string" ? g.hash : "";
    const p = g.policy;
    if (validPolicy(p)) {
      const globs: [string, RegExp, PolicyEntry][] = Object.keys(p.globs || {})
        .sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0)) // least specific first; the longest pattern wins
        .map((k) => [k, globToRegExp(k), p.globs[k]]);
      loaded = { hash, defaults: p.defaults, features: p.features || {}, globs };
    }
  } catch {
    loaded = null;
  }
  if (!loaded) policyLog("invalid", `router-migrate policy ${hash.slice(0, 8) || "(no hash)"} invalid: ignored (v1 behaviour)`);
  POLICY_CACHE.set(g, loaded);
  return loaded;
}

/** Matching entries for a feature id, least specific first: defaults, matching globs by pattern length, exact feature. */
function policyLayers(L: LoadedPolicy, featId: string | undefined): PolicyEntry[] {
  const layers: PolicyEntry[] = [];
  if (L.defaults) layers.push(L.defaults);
  if (typeof featId === "string") {
    for (const [, re, e] of L.globs) if (re.test(featId)) layers.push(e);
    if (own(L.features, featId)) layers.push(L.features[featId]);
  }
  return layers;
}

function layersPick(layers: PolicyEntry[], key: string): any {
  let v: any;
  for (const l of layers) if (own(l, key)) v = (l as any)[key];
  return v;
}

/** Effective policy settings for a feature id (metadata merged), or null when no valid policy is active. */
export function routerPolicyFor(featId?: string): { hash: string; settings: PolicyEntry } | null {
  const L = activePolicy();
  if (!L) return null;
  const layers = policyLayers(L, featId);
  const settings: any = {};
  for (const k of POLICY_KEYS) {
    if (k === "metadata") {
      const m = Object.fromEntries(layers.flatMap((l) => Object.entries(l.metadata || {})));
      if (Object.keys(m).length) settings.metadata = m;
    } else {
      const v = layersPick(layers, k);
      if (v !== undefined) settings[k] = v;
    }
  }
  return { hash: L.hash, settings };
}

/** Input modalities a Responses body needs beyond text (image / file parts). */
function inputModalities(input: any): string[] {
  const need = new Set<string>();
  for (const it of Array.isArray(input) ? input : []) {
    if (!it || typeof it !== "object") continue;
    for (const parts of [it.content, it.output]) {
      if (!Array.isArray(parts)) continue;
      for (const p of parts) {
        if (p?.type === "input_image") need.add("image");
        else if (p?.type === "input_file") need.add("file");
      }
    }
  }
  return [...need];
}

/** Why a fallback candidate cannot serve this request (capability guard), or "" when it can. */
function candidateProblem(c: string, nb: any, need: string[]): string {
  if (!own(CATALOG, c)) return "not in catalog";
  if (c === nb.model) return "original model";
  const m = CATALOG[c];
  const cap = m.c || {};
  if ((cap.st || "active") !== "active") return `status ${cap.st}`;
  if (!(cap.s || []).includes("responses")) return "no responses surface";
  if (nb.tools != null && !cap.tools) return "no tools";
  const ft = plainObject(nb.text) && plainObject(nb.text.format) ? nb.text.format.type : undefined;
  if ((ft === "json_schema" || ft === "json_object") && !cap.so) return "no structured outputs";
  const miss = need.filter((x) => !(cap.in || ["text"]).includes(x));
  if (miss.length) return `no ${miss.join("/")} input`;
  if (nb.stop != null && m.o === "openai") return "OpenAI does not take stop";
  if (nb.temperature != null && m.t === false) return "no temperature";
  return "";
}

function policyBody(L: LoadedPolicy, nb: any, featId: string | undefined, callerTier: boolean): any {
  policyLog("hash", `router-migrate policy ${L.hash.slice(0, 8) || "(no hash)"}`);
  const layers = policyLayers(L, featId);
  if (!layers.length) return nb;
  const fid = featId || "(no feature)";
  const skip = (key: string, why: string) => policyLog(`skip\u0000${fid}\u0000${key}`, `router-migrate policy: ${key} skipped for ${fid} (${why})`);
  const out: any = { ...nb };

  // metadata: defaults < globs < feature, the request's own keys win; at the 16-key cap the least specific policy keys go first
  const req = plainObject(out.metadata) ? out.metadata : {};
  const room = METADATA_MAX_KEYS - Object.keys(req).length;
  const pol = new Map<string, string>();
  let capped = false;
  for (const l of [...layers].reverse())
    for (const [k, v] of Object.entries(l.metadata || {})) {
      if (own(req, k) || pol.has(k)) continue;
      if (pol.size >= room) capped = true;
      else pol.set(k, v);
    }
  if (capped) skip("metadata", `${METADATA_MAX_KEYS}-key cap`);
  if (pol.size) out.metadata = { ...req, ...Object.fromEntries(pol) };

  const pck = layersPick(layers, "prompt_cache_key");
  if (pck !== undefined && !out.prompt_cache_key) {
    if (pck.includes("$feature") && !featId) skip("prompt_cache_key", "no feature id for $feature");
    else {
      const v = pck.split("$feature").join(featId || "");
      if (v) out.prompt_cache_key = v;
    }
  }

  const pt = layersPick(layers, "provider_timeout");
  if (pt !== undefined && out.provider_timeout == null) out.provider_timeout = pt;
  const st = layersPick(layers, "service_tier");
  if (st !== undefined && !callerTier && layersPick(layers, "allow_flex_tier") === undefined) out.service_tier = st;

  let multi = false;
  const fb: string[] | undefined = layersPick(layers, "fallbacks");
  if (fb !== undefined && typeof out.model === "string") {
    const need = inputModalities(out.input);
    const kept: string[] = [];
    const dropped: string[] = [];
    for (const c of fb) {
      const why = candidateProblem(c, out, need);
      if (why) dropped.push(`${c}: ${why}`);
      else if (!kept.includes(c)) kept.push(c);
    }
    if (dropped.length) skip("fallbacks", dropped.join("; "));
    if (kept.length) {
      out.models = [out.model, ...kept];
      delete out.model; // Router rejects model and models together
      multi = true;
    }
  }

  const flex = layersPick(layers, "allow_flex_tier");
  if (flex !== undefined) {
    if (multi) skip("allow_flex_tier", "fallbacks send a models list; flex applies to a single model only");
    else if (callerTier) skip("allow_flex_tier", "the caller set service_tier");
    else if (flex === true && CATALOG[out.model]?.c?.f !== true) skip("allow_flex_tier", `${out.model} has no flex tier`);
    else {
      out.allow_flex_tier = flex;
      delete out.service_tier; // v1's automatic service_tier would switch Flex off
    }
  }
  return out;
}

/** The v1 Router body with the compiled policy applied; the v1 body itself (same object) when no policy applies. */
function applyPolicy(nb: any, featId: string | undefined, callerTier: boolean): any {
  const L = activePolicy();
  if (!L) return nb;
  try {
    return policyBody(L, nb, featId, callerTier);
  } catch {
    return nb; // a policy bug never changes where or how the request is served
  }
}

/** Feature id for policy lookup: the caller's own metadata.feature, else the one derived from the call stack. */
function policyFeature(meta: any, feat: string | undefined): string | undefined {
  if (plainObject(meta) && own(meta, "feature") && meta.feature != null) return String(meta.feature);
  return feat;
}

type Plan =
  | { local: Response }
  | { kind: "chat" | "responses" | "anthropic"; path: string; body: any; ctx?: any; items?: any[]; anthropic: boolean; headers: Headers; providerAuth: boolean; feature?: string };

const localJson = (o: any, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });

/** GET/DELETE /responses/{id}[/input_items] for a Router-issued id: answered from this process; the provider never saw it. */
function localResponse(method: string, path: string): Response | null {
  const m = path.match(/\/responses\/([^/]+)(\/input_items)?\/?$/);
  if (!m) return null;
  let id = m[1];
  try {
    id = decodeURIComponent(id);
  } catch {}
  if (!isRouterId(id)) return null;
  const chain = lruGet(CHAINS, id);
  if (!chain) return evictedGet(id);
  if (method === "DELETE") {
    if (m[2]) return evictedGet(id);
    CHAINS.delete(id); // the id stays known, so it is still never sent to the provider
    return localJson({ id, object: "response", deleted: true });
  }
  if (m[2]) return localJson({ object: "list", data: chain.items, first_id: null, last_id: null, has_more: false });
  return localJson(chain.response);
}

/** operatorKeyOnly (an approved per-user-keys card): route only requests carrying the operator's own provider key. */
function operatorKeyOk(headers: Headers, anthropic: boolean): boolean {
  const want = env(anthropic ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY") || "";
  if (!want) return false;
  const auth = headers.get("authorization") || "";
  const got = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : (headers.get("x-api-key") || "").trim();
  return !!got && got === want;
}

/** Router id for a request's model: an approved model swap for this feature (policy model_map) first, else the catalog. */
function resolveFor(body: any, feat: string | undefined, origin?: string): string | null {
  if (typeof body.model === "string") {
    const mm = policyKey("model_map", body, feat, origin);
    const to = plainObject(mm) && own(mm, body.model) ? mm[body.model] : undefined;
    if (to && CATALOG[to]) return to;
    if (to) policyLog("mm:" + to, `router-migrate policy: model_map target ${to} is not a Router catalog id: ignored`);
  }
  return resolveModel(body.model);
}

const MAX_TOKEN_KEYS = ["max_tokens", "max_completion_tokens", "max_output_tokens"];

/** The Router-bound copy of a request with this feature's accepted trade-offs (policy drop_params): listed parameters are
 * dropped, a listed output cap is raised to Router's minimum of 16, a listed `name` is folded into the message text. The
 * original request is untouched, so a fallback to the provider still sends exactly what the code built. */
function policyKey(key: string, body: any, feat: string | undefined, origin?: string): any {
  const L = activePolicy();
  if (!L) return undefined;
  const v = layersPick(policyLayers(L, policyFeature(body.metadata, feat)), key);
  return v || !origin ? v : layersPick(policyLayers(L, origin), key); // framework calls: the client's origin names the site
}

function adapt(body: any, feat: string | undefined, origin?: string): any {
  const drop = policyKey("drop_params", body, feat, origin);
  if (!Array.isArray(drop) || !drop.length) return body;
  const b: any = { ...body };
  for (const k of drop) {
    if (MAX_TOKEN_KEYS.includes(k)) {
      if (typeof b[k] === "number" && b[k] < 16) b[k] = 16;
    } else if (k === "name" && Array.isArray(b.messages)) {
      b.messages = b.messages.map((m: any) => {
        if (!plainObject(m) || m.name == null || m.role === "function" || m.role === "tool") return m;
        const { name, ...rest } = m;
        return typeof rest.content === "string" ? { ...rest, content: `${name}: ${rest.content}` } : rest;
      });
    } else delete b[k];
  }
  return b;
}

/** unlockHosts (v2 unlock.vendor_endpoint, customer-approved): another vendor's OpenAI-compatible hosts (e.g. "api.x.ai")
 * whose chat/responses requests go to Router when their model is a Router catalog id; everything else stays on the vendor.
 * dropBetas (v2 unlock.stated_loss, customer-approved): anthropic-beta flags that may be dropped (not forwarded) so the
 * request can go to Router; a request carrying any other beta flag stays on Anthropic. */
export type RouterOpts = { operatorKeyOnly?: boolean; origin?: string; unlockHosts?: string[]; dropBetas?: string[] };

/** Every flag in an anthropic-beta header is in the approved dropBetas list. */
function betasDroppable(beta: string, drop?: string[]): boolean {
  const flags = beta.split(",").map((b) => b.trim()).filter(Boolean);
  return !!drop?.length && flags.every((b) => drop.includes(b));
}

/** Decide where one request goes. null = the original provider, unchanged. Throws only on bugs (caller sends to provider). */
function planRequest(input: any, init: any, opts?: RouterOpts): Plan | null {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input?.url;
  const method = (init?.method || (typeof input === "object" && input?.method) || "GET").toUpperCase();
  const [host, path] = hostPath(url || "");
  const unlocked = !OPENAI_HOSTS.includes(host) && !!opts?.unlockHosts?.includes(host);
  const openaiLike = OPENAI_HOSTS.includes(host) || unlocked;
  if (openaiLike && (method === "GET" || method === "DELETE")) {
    const local = localResponse(method, path);
    return local ? { local } : null;
  }
  const isChat = openaiLike && path.endsWith("/chat/completions");
  const isResponses = openaiLike && path.endsWith("/responses");
  const isAnthropic = ANTHROPIC_HOSTS.includes(host) && (path.endsWith("/v1/messages") || path.endsWith("/v1/messages/count_tokens"));
  if (method !== "POST" || !(isChat || isResponses || isAnthropic) || typeof init?.body !== "string") return null;
  let body: any;
  try {
    body = JSON.parse(init.body);
  } catch {
    return null;
  }
  if (!plainObject(body)) return null;
  if (opts?.operatorKeyOnly && !unlocked && !operatorKeyOk(new Headers(init.headers || (typeof input === "object" ? input.headers : undefined)), isAnthropic)) return null;
  const feat = featureTagOn() ? feature() : undefined;
  body = adapt(body, feat, opts?.origin);
  let items: any[] | undefined;
  let chained = false;
  if (isResponses) {
    const prev = body.previous_response_id;
    if (prev != null && isRouterId(prev)) {
      // a chain that started on Router: continue it here with the history inline, or fail clearly (never the provider)
      const chain = lruGet(CHAINS, prev);
      if (!chain) return { local: evictedPrev(String(prev)) };
      const prior = chain.output.filter((o: any) => !(o.type === "reasoning" && !o.encrypted_content)); // not replayable without storage
      items = [...chain.items, ...prior, ...asItems(body.input)];
      chained = true;
    } else {
      if (prev != null || body.conversation != null || body.store === true || body.background) return null;
      items = asItems(body.input);
    }
  }
  const rid = resolveFor(body, feat, opts?.origin);
  if (!rid) return chained ? { local: stateError(400, `previous_response_id ${body.previous_response_id} was served by Router, but model ${body.model} is not; send the conversation inline, or set ROUTER_MIGRATE_DISABLE=1.`) } : null;
  const headers = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined));
  const providerAuth = headers.has("authorization") || headers.has("x-api-key");
  const owned = CATALOG[rid]?.o;
  if (isAnthropic) {
    const beta = headers.get("anthropic-beta");
    if (beta && !betasDroppable(beta, opts?.dropBetas)) return null; // beta features unmeasured on Router (unless approved to drop)
    if (activePolicy()) policyLog("messages", "router-migrate policy: not applied on /v1/messages (Router-only fields are not supported there)");
    const p = path.endsWith("count_tokens") ? "/v1/messages/count_tokens" : "/v1/messages";
    return { kind: "anthropic", path: p, body: { ...body, model: rid }, anthropic: true, headers, providerAuth, feature: feat };
  }
  if (isResponses) {
    if (responsesNeedsProvider({ ...body, input: items })) {
      if (!chained) return null;
      return { local: stateError(400, `previous_response_id ${body.previous_response_id} was served by Router, but this turn needs provider-side state (file_id, vector stores, item_reference or stored reasoning); send the conversation inline, or set ROUTER_MIGRATE_DISABLE=1.`) };
    }
    const nb: any = { ...body, model: rid, input: items };
    delete nb.store;
    delete nb.previous_response_id;
    const meta = withFeature(body.metadata, feat);
    if (Object.keys(meta).length) nb.metadata = meta;
    if (owned === "anthropic" && !nb.prompt_cache_key && feat) nb.prompt_cache_key = feat;
    if (owned === "openai" && CATALOG[rid]?.r) {
      if (body.include != null && !Array.isArray(body.include)) throw new Untranslatable("include");
      nb.include = [...new Set([...(body.include || []), "reasoning.encrypted_content"])]; // routed turns stay replayable
    }
    if (nb.service_tier == null) {
      const tier = defaultTier();
      if (tier) nb.service_tier = tier;
    }
    const pb = applyPolicy(nb, policyFeature(body.metadata, feat), body.service_tier != null);
    return { kind: "responses", path: "/v1/responses", body: pb, items, anthropic: false, headers, providerAuth, feature: feat };
  }
  const tr = chatToResponses(body, rid, owned, feat);
  const pb = applyPolicy(tr.body, policyFeature(body.metadata, feat), body.service_tier != null);
  return { kind: "chat", path: "/v1/responses", body: pb, ctx: tr.ctx, anthropic: false, headers, providerAuth, feature: feat };
}

/** fetch for OpenAI/Anthropic/Vercel/LangChain clients: Router when it can serve the request, the provider otherwise. */
export function routerFetch(base?: AnyFetch, opts?: RouterOpts): AnyFetch | undefined {
  if (!routerActive()) return base;
  const origin = featureTagOn() ? feature() : undefined; // where this client was built: names framework sites, whose calls start in library code
  const inner: AnyFetch =
    base ||
    (async (input: any, init?: any) => {
      // openai-node v4 / @anthropic-ai/sdk 0.x pass an http(s) agent (proxy, keep-alive) meant for node-fetch
      if (init?.agent && (typeof input === "string" || input instanceof URL)) {
        const nf = await nodeFetch();
        if (nf) return nf(input, init);
      }
      return (globalThis.fetch as any)(input, init);
    });
  return async (input: any, init?: any) => {
    if (!routerActive()) return inner(input, init);
    let plan: Plan | null = null;
    try {
      plan = planRequest(input, init, { ...opts, origin });
    } catch {
      plan = null; // Untranslatable or an unexpected shape: the provider serves it exactly as before
    }
    if (!plan) return inner(input, init);
    if ("local" in plan) return plan.local;
    const rh = routerHeaders(plan.headers, plan.anthropic);
    if (plan.feature) rh.set("x-router-migrate-feature", plan.feature.slice(0, 200)); // the calling file:function (Messages bodies can't carry it)
    if (origin) rh.set("x-router-migrate-client", origin.slice(0, 200));
    const routerInit: any = { method: "POST", headers: rh, body: JSON.stringify(plan.body), signal: init?.signal };
    if (init?.agent) routerInit.agent = agentFor(init.agent);
    const up: any = await inner(routerBase() + plan.path, routerInit);
    if (up.status >= 400) {
      let text = "";
      try {
        text = await up.text();
      } catch {}
      if (plan.providerAuth && providerCanServe(up.status, text)) return inner(input, init); // the original request, once
      const h = copyHeaders(up);
      if (!h.get("content-type")) h.set("content-type", "application/json");
      return new Response(text, { status: up.status, statusText: up.statusText, headers: h });
    }
    if (plan.kind === "anthropic") return up;
    const hdrs = copyHeaders(up);
    if (plan.kind === "responses") {
      if ((up.headers.get("content-type") || "").includes("event-stream") && up.body)
        return new Response(recordIdsStream(webStream(up.body), plan.items), { status: up.status, statusText: up.statusText, headers: hdrs });
      const text = await up.text();
      try {
        rememberChain(plan.items, JSON.parse(text));
      } catch {}
      return new Response(text, { status: up.status, statusText: up.statusText, headers: hdrs });
    }
    if (plan.body.stream && up.body) {
      hdrs.set("content-type", "text/event-stream");
      return new Response(chunkStream(webStream(up.body), plan.ctx), { status: up.status, headers: hdrs });
    }
    hdrs.set("content-type", "application/json");
    return new Response(JSON.stringify(responsesToChat(await up.json(), plan.ctx)), { status: up.status, headers: hdrs });
  };
}

/** Spread into client options: new OpenAI({ ...opts, ...routerOptions(opts.fetch) }); routerOptions(undefined,
 * { operatorKeyOnly: true }) routes only requests made with the operator's own provider key; { unlockHosts: ["api.x.ai"] }
 * / { dropBetas: [...] } carry approved unlocks (see RouterOpts). */
export function routerOptions(base?: AnyFetch, opts?: RouterOpts): { fetch?: AnyFetch } {
  const f = routerFetch(base, opts);
  return f ? { fetch: f } : {};
}
