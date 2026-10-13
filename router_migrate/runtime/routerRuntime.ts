// routerRuntime.ts (vendored by router-migrate; safe to read, edit or delete)
//
// Routes this codebase's OpenAI / Anthropic SDK traffic through Ramp's Router (https://router.com) when
// RAMP_ROUTER_API_KEY is set. When it is not set, routerFetch() returns the fetch you passed in (or undefined,
// which makes every SDK use its default), so behavior is unchanged.
//
// routerFetch() wraps fetch: requests Router can serve go to Router, everything else goes to the original provider.
//   POST .../chat/completions -> {router}/v1/responses   (request, JSON reply and SSE stream translated back)
//   POST .../responses        -> {router}/v1/responses   (store dropped; previous_response_id stays on the provider)
//   POST .../v1/messages      -> {router}/v1/messages    (Anthropic; model id mapped)
//   anything else (embeddings, images, files, unknown models, n>1, logprobs, audio) -> original provider
// Env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL (default https://api.router.com), ROUTER_MIGRATE_DISABLE=1,
//      ROUTER_MIGRATE_MODEL_MAP='{"old":"router-id"}', ROUTER_MIGRATE_SERVICE_TIER.
/* eslint-disable */
// @ts-nocheck

type AnyFetch = (input: any, init?: any) => Promise<Response>;

const CATALOG_JSON = String.raw`__ROUTER_CATALOG__`;
let CATALOG: Record<string, { o?: string; t?: boolean | null; r?: boolean }> = {};
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

function env(name: string): string | undefined {
  try {
    return typeof process !== "undefined" && process.env ? process.env[name] : undefined;
  } catch {
    return undefined;
  }
}

export function routerActive(): boolean {
  const off = env("ROUTER_MIGRATE_DISABLE");
  return !!env("RAMP_ROUTER_API_KEY") && !(off === "1" || off === "true" || off === "yes");
}

function routerBase(): string {
  let b = (env("RAMP_ROUTER_BASE_URL") || "https://api.router.com").replace(/\/+$/, "");
  return b.endsWith("/v1") ? b.slice(0, -3) : b;
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
  return DATED[name] || null;
}

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
      const part: any = { type: "input_file" };
      for (const k of ["file_data", "file_id", "filename"]) if (f[k]) part[k] = f[k];
      return part;
    }
    throw new Untranslatable(`content part ${p?.type}`);
  });
}

export function chatToResponses(body: any, routerModel: string, ownedBy: string | undefined, feat: string | undefined) {
  const unsupported = ["audio", "modalities", "prediction", "web_search_options", "logit_bias"].filter((k) => body[k]);
  if ((body.n ?? 1) !== 1) unsupported.push("n");
  if (body.logprobs || body.top_logprobs) unsupported.push("logprobs");
  if (unsupported.length) throw new Untranslatable(unsupported.join(","));
  const info = CATALOG[routerModel] || {};
  const items: any[] = [];
  const legacy: Record<string, string> = {};
  (body.messages || []).forEach((m: any, i: number) => {
    const role = m.role;
    if (role === "system" || role === "developer") items.push({ role, content: textOf(m.content) });
    else if (role === "user") items.push({ role: "user", content: userParts(m.content) });
    else if (role === "assistant") {
      const t = textOf(m.content);
      if (t) items.push({ role: "assistant", content: t });
      for (const tc of m.tool_calls || []) {
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
  if (mot != null) out.max_output_tokens = Math.max(16, Number(mot));
  const tempOk = info.t !== false;
  for (const k of ["temperature", "top_p"]) if (body[k] != null && tempOk) out[k] = body[k];
  for (const k of ["frequency_penalty", "presence_penalty", "parallel_tool_calls", "user", "service_tier", "prompt_cache_key", "safety_identifier"])
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
  const tc = body.tool_choice ?? body.function_call;
  if (tc && typeof tc === "object") out.tool_choice = { type: "function", name: tc.function?.name || tc.name };
  else if (["auto", "none", "required"].includes(tc)) out.tool_choice = tc;
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
  const meta: Record<string, string> = { ...(body.metadata || {}) };
  if (feat && !("feature" in meta)) meta.feature = feat;
  if (Object.keys(meta).length) out.metadata = Object.fromEntries(Object.entries(meta).map(([k, v]) => [k, String(v).slice(0, 512)]));
  if (ownedBy === "anthropic" && !out.prompt_cache_key && feat) out.prompt_cache_key = feat;
  const tier = env("ROUTER_MIGRATE_SERVICE_TIER");
  if (body.stream) {
    out.stream = true;
    if (!out.service_tier) out.service_tier = tier || "default";
  } else if (tier && !out.service_tier) out.service_tier = tier;
  const ctx = { stops: ownedBy === "openai" ? stops : [], includeUsage: !!body.stream_options?.include_usage, model: body.model, legacy: !!body.functions };
  return { body: out, ctx };
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
  const inc = resp.incomplete_details?.reason;
  let finish = calls.length ? "tool_calls" : inc === "max_output_tokens" && !stopped ? "length" : inc === "content_filter" ? "content_filter" : "stop";
  const msg: any = { role: "assistant", content: t || !calls.length ? t : null, refusal };
  if (calls.length) {
    if (ctx.legacy) {
      msg.function_call = calls[0].function;
      finish = "function_call";
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
  const toolIndex: Record<string, number> = {};
  const maxStop = Math.max(0, ...(ctx.stops || []).map((s: string) => s.length));
  const chunk = (delta: any, finish: string | null = null, usage?: any, withChoices = true) => {
    const d: any = { id: `chatcmpl-${id}`, object: "chat.completion.chunk", created, model, system_fingerprint: null, choices: withChoices ? [{ index: 0, delta, finish_reason: finish, logprobs: null }] : [] };
    if (usage !== undefined) d.usage = usage;
    return enc.encode(`data: ${JSON.stringify(d)}\n\n`);
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
  const handle = (raw: string, emit: (b: Uint8Array) => void) => {
    const data = raw.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("");
    if (!data || data === "[DONE]") return;
    let ev: any;
    try {
      ev = JSON.parse(data);
    } catch {
      return;
    }
    const t = ev.type || "";
    if (!role && ["response.created", "response.in_progress", "response.output_item.added", "response.output_text.delta"].includes(t)) {
      id = ev.response?.id || id;
      model = ev.response?.model || model;
      role = true;
      emit(chunk({ role: "assistant", content: "" }));
    }
    if (t === "response.output_text.delta") textOut(ev.delta || "", false, emit);
    else if (t === "response.output_item.added" && ev.item?.type === "function_call") {
      const idx = Object.keys(toolIndex).length;
      toolIndex[ev.item.id] = idx;
      sawCall = true;
      emit(chunk({ tool_calls: [{ index: idx, id: ev.item.call_id, type: "function", function: { name: ev.item.name, arguments: "" } }] }));
    } else if (t === "response.function_call_arguments.delta") {
      emit(chunk({ tool_calls: [{ index: toolIndex[ev.item_id] ?? 0, function: { arguments: ev.delta || "" } }] }));
    } else if (t === "response.completed" || t === "response.incomplete") {
      textOut("", true, emit);
      const inc = ev.response?.incomplete_details?.reason;
      emit(chunk({}, sawCall ? "tool_calls" : inc === "max_output_tokens" && !stopped ? "length" : "stop"));
      if (ctx.includeUsage) emit(chunk({}, null, usageOf(ev.response?.usage), false));
      emit(enc.encode("data: [DONE]\n\n"));
      done = true;
    } else if (t === "response.failed" || t === "error") {
      const err = ev.response?.error || ev.error || { message: ev.message || "Router stream error" };
      emit(enc.encode(`data: ${JSON.stringify({ error: err })}\n\n`));
      emit(enc.encode("data: [DONE]\n\n"));
      done = true;
    }
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
          if (buf.trim()) handle(buf, emit);
          if (!done) emit(enc.encode("data: [DONE]\n\n"));
          controller.close();
          return;
        }
        buf += dec.decode(value, { stream: true });
        let k: number;
        let produced = false;
        while ((k = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, k);
          buf = buf.slice(k + 2);
          const before = (controller as any).desiredSize;
          handle(raw, (b) => {
            produced = true;
            emit(b);
          });
          void before;
        }
        if (produced) return;
      }
    },
    cancel() {
      return reader.cancel();
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
  if (anthropic) {
    h.set("anthropic-version", orig.get("anthropic-version") || "2023-06-01");
    const beta = orig.get("anthropic-beta");
    if (beta) h.set("anthropic-beta", beta);
  }
  return h;
}

/** fetch for OpenAI/Anthropic/Vercel/LangChain clients: Router when it can serve the request, the provider otherwise. */
export function routerFetch(base?: AnyFetch): AnyFetch | undefined {
  if (!routerActive()) return base;
  const inner: AnyFetch = base || ((...a: any[]) => (globalThis.fetch as any)(...a));
  return async (input: any, init?: any) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input?.url;
    const method = (init?.method || (typeof input === "object" && input?.method) || "GET").toUpperCase();
    const [host, path] = hostPath(url || "");
    const isOpenAI = OPENAI_HOSTS.includes(host) && (path.endsWith("/chat/completions") || path.endsWith("/responses"));
    const isAnthropic = ANTHROPIC_HOSTS.includes(host) && (path.endsWith("/v1/messages") || path.endsWith("/v1/messages/count_tokens"));
    if (!routerActive() || method !== "POST" || !(isOpenAI || isAnthropic) || typeof init?.body !== "string") return inner(input, init);
    let body: any;
    try {
      body = JSON.parse(init.body);
    } catch {
      return inner(input, init);
    }
    const rid = resolveModel(body.model);
    if (!rid) return inner(input, init);
    const origHeaders = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined));
    const owned = CATALOG[rid]?.o;
    const feat = feature();
    const call = (p: string, b: any, anthropic: boolean) =>
      inner(routerBase() + p, { method: "POST", headers: routerHeaders(origHeaders, anthropic), body: JSON.stringify(b), signal: init?.signal });
    if (isAnthropic) {
      if (origHeaders.get("anthropic-beta")) return inner(input, init); // beta features unmeasured on Router
      return call(path.endsWith("count_tokens") ? "/v1/messages/count_tokens" : "/v1/messages", { ...body, model: rid }, true);
    }
    if (path.endsWith("/responses")) {
      if (body.previous_response_id || body.conversation || body.background) return inner(input, init);
      const nb: any = { ...body, model: rid };
      delete nb.store;
      if (feat && !(nb.metadata && "feature" in nb.metadata)) nb.metadata = { ...(nb.metadata || {}), feature: feat };
      if (owned === "anthropic" && !nb.prompt_cache_key && feat) nb.prompt_cache_key = feat;
      return call("/v1/responses", nb, false);
    }
    let tr: any;
    try {
      tr = chatToResponses(body, rid, owned, feat);
    } catch (e) {
      if (e instanceof Untranslatable) return inner(input, init);
      throw e;
    }
    const up = await call("/v1/responses", tr.body, false);
    const hdrs = new Headers();
    for (const k of ["x-request-id", "x-router-provider"]) {
      const v = up.headers.get(k);
      if (v) hdrs.set(k, v);
    }
    if (!up.ok) {
      hdrs.set("content-type", up.headers.get("content-type") || "application/json");
      return new Response(await up.text(), { status: up.status, headers: hdrs });
    }
    if (tr.body.stream && up.body) {
      hdrs.set("content-type", "text/event-stream");
      return new Response(chunkStream(up.body, tr.ctx), { status: up.status, headers: hdrs });
    }
    hdrs.set("content-type", "application/json");
    return new Response(JSON.stringify(responsesToChat(await up.json(), tr.ctx)), { status: up.status, headers: hdrs });
  };
}

/** Spread into client options: new OpenAI({ ...opts, ...routerOptions(opts.fetch) }). */
export function routerOptions(base?: AnyFetch): { fetch?: AnyFetch } {
  const f = routerFetch(base);
  return f ? { fetch: f } : {};
}
