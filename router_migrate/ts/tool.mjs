#!/usr/bin/env node
// router-migrate TypeScript/JavaScript half: AST scanner + client-construction codemods.
//
//   node router_migrate/ts/tool.mjs scan  <root>
//   node router_migrate/ts/tool.mjs apply <root> --runtime <rendered routerRuntime.ts> --sites <json array of site ids>
//   (an entry may be { id, opts } instead: approved runtime levers, e.g. { unlockHosts: ["api.x.ai"] }, passed as the
//   second argument of routerOptions()/routerFetch() at that site)
//
// scan prints the site JSON described in router_migrate/SCHEMA.md (lang "ts").
// apply patches only client construction sites so they take the vendored runtime's fetch
// (routerOptions()/routerFetch()); call sites are never touched. See SCHEMA.md and archive/research/sdk_surfaces.md (E, F).
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");

// ---------------------------------------------------------------------------------------------------------------
// tables

const SOURCE_EXTS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"]);
const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "build", "out", "coverage", ".git", "vendor"]);
const MAX_BYTES = 1024 * 1024;

// Modules whose import marks a file as "LLM code" (gates the member-chain call matcher).
function isLLMModule(m) {
  return (
    m === "openai" || m.startsWith("openai/") ||
    m === "@anthropic-ai/sdk" || m.startsWith("@anthropic-ai/") ||
    m === "ai" || m.startsWith("ai/") || m.startsWith("@ai-sdk/") ||
    m === "@langchain/openai" || m === "@langchain/anthropic" || /^langchain\/(chat_models|llms|embeddings)\/(openai|anthropic)$/.test(m) ||
    m in PROVIDER_SDK
  );
}

// Other provider SDK packages: sdk name + the classes that construct a client.
const PROVIDER_SDK = {
  "@google/generative-ai": ["google-genai", ["GoogleGenerativeAI"]],
  "@google/genai": ["google-genai", ["GoogleGenAI"]],
  "@mistralai/mistralai": ["mistral", ["Mistral", "MistralClient", "default"]],
  "groq-sdk": ["groq", ["Groq", "default"]],
  "cohere-ai": ["cohere", ["CohereClient", "CohereClientV2"]],
  ollama: ["ollama", ["Ollama"]],
  "ollama/browser": ["ollama", ["Ollama"]],
  "@azure/openai": ["azure-openai", ["OpenAIClient", "AzureOpenAI"]],
};

const LC_OPENAI = new Set(["@langchain/openai", "langchain/chat_models/openai", "langchain/llms/openai", "langchain/embeddings/openai"]);
const LC_ANTHROPIC = new Set(["@langchain/anthropic", "langchain/chat_models/anthropic"]);

const NO_ROUTE = (sdk) => `${sdk} clients are not served by Router; left on the provider`;

// (module, exported name) used with `new` -> client description
function newClass(module, name) {
  if (module === "openai") {
    if (name === "default" || name === "OpenAI") return { sdk: "openai", surface: "OpenAI", patch: "options" };
    if (name === "AzureOpenAI") return { sdk: "azure-openai", surface: "AzureOpenAI", patch: null, reason: "AzureOpenAI targets Azure OpenAI deployments, which Router does not serve; left on the provider" };
    return null;
  }
  if (module === "@anthropic-ai/sdk") {
    if (name === "default" || name === "Anthropic") return { sdk: "anthropic", surface: "Anthropic", patch: "options" };
    return null;
  }
  if (LC_OPENAI.has(module)) {
    if (name === "ChatOpenAI") return { sdk: "langchain-openai", surface: "ChatOpenAI", patch: "langchain", lc: true };
    if (name === "AzureChatOpenAI") return { sdk: "langchain-openai", surface: "AzureChatOpenAI", patch: "langchain", lc: true };
    if (name === "OpenAIEmbeddings" || name === "AzureOpenAIEmbeddings")
      return { sdk: "langchain-openai", surface: name, patch: null, reason: "embeddings are not served by Router; left on the provider" };
    if (name === "OpenAI" || name === "AzureOpenAI")
      return { sdk: "langchain-openai", surface: name, patch: null, reason: "legacy completions LLM; Router does not serve /v1/completions" };
    return null;
  }
  if (LC_ANTHROPIC.has(module)) {
    if (name === "ChatAnthropic") return { sdk: "langchain-anthropic", surface: "ChatAnthropic", patch: "langchain-anthropic", lc: true };
    return null;
  }
  if (module === "ai" && ["ToolLoopAgent", "Agent", "Experimental_Agent"].includes(name)) return { agent: true };
  const p = PROVIDER_SDK[module];
  if (p && p[1].includes(name)) return { sdk: p[0], surface: name === "default" ? p[1][0] : name, patch: null, reason: NO_ROUTE(p[0]) };
  return null;
}

const AI_SDK_PROVIDER_SDK = {
  "@ai-sdk/openai": "vercel-ai-openai",
  "@ai-sdk/anthropic": "vercel-ai-anthropic",
  "@ai-sdk/gateway": "vercel-ai-gateway",
  "@ai-sdk/azure": "azure-openai",
  "@ai-sdk/google": "google-genai",
  "@ai-sdk/google-vertex": "google-genai",
  "@ai-sdk/mistral": "mistral",
  "@ai-sdk/groq": "groq",
  "@ai-sdk/cohere": "cohere",
  "@ai-sdk/amazon-bedrock": "bedrock",
};
const AI_SDK_NON_PROVIDER = new Set(["@ai-sdk/react", "@ai-sdk/rsc", "@ai-sdk/svelte", "@ai-sdk/vue", "@ai-sdk/solid", "@ai-sdk/angular",
  "@ai-sdk/provider", "@ai-sdk/provider-utils", "@ai-sdk/ui-utils", "@ai-sdk/otel", "@ai-sdk/devtools", "@ai-sdk/mcp"]);

// (module, exported name) called as a function -> client factory
function factory(module, name) {
  if (module === "@ai-sdk/openai" && name === "createOpenAI") return { sdk: "vercel-ai-openai", surface: "createOpenAI", patch: "options" };
  if (module === "@ai-sdk/anthropic" && name === "createAnthropic") return { sdk: "vercel-ai-anthropic", surface: "createAnthropic", patch: "options" };
  if ((module === "ai" || module === "@ai-sdk/gateway") && name === "createGateway")
    return { sdk: "vercel-ai-gateway", surface: "createGateway", patch: null, reason: "Vercel AI Gateway owns its transport; not routed" };
  if (module.startsWith("@ai-sdk/") && !AI_SDK_NON_PROVIDER.has(module) && /^create[A-Z]/.test(name)) {
    const sdk = AI_SDK_PROVIDER_SDK[module] || "other";
    return { sdk, surface: name, patch: null, reason: NO_ROUTE(sdk === "other" ? module : sdk) };
  }
  return null;
}

// (module, exported name) imported as a ready-made provider instance
function defaultInstance(module, name) {
  if (module === "@ai-sdk/openai" && name === "openai")
    return { sdk: "vercel-ai-openai", surface: "openai-default-provider", patch: "default-provider", factory: "createOpenAI" };
  if (module === "@ai-sdk/anthropic" && name === "anthropic")
    return { sdk: "vercel-ai-anthropic", surface: "anthropic-default-provider", patch: "default-provider", factory: "createAnthropic" };
  if ((module === "ai" || module === "@ai-sdk/gateway") && name === "gateway")
    return { sdk: "vercel-ai-gateway", surface: "gateway", patch: null, reason: "Vercel AI Gateway owns its transport; not routed" };
  if (module.startsWith("@ai-sdk/") && !AI_SDK_NON_PROVIDER.has(module) && module !== "@ai-sdk/openai-compatible" && /^[a-z][A-Za-z0-9]*$/.test(name) && !name.startsWith("create")) {
    const sdk = AI_SDK_PROVIDER_SDK[module] || "other";
    return { sdk, surface: `${name}-default-provider`, patch: null, reason: NO_ROUTE(sdk === "other" ? module : sdk) };
  }
  return null;
}

const AI_FUNCS = {
  generateText: ["framework.chat", false], streamText: ["framework.chat", true],
  generateObject: ["framework.chat", false], streamObject: ["framework.chat", true],
  embed: ["framework.embeddings", false], embedMany: ["framework.embeddings", false],
  generateImage: ["images", false], experimental_generateImage: ["images", false],
};

const STREAMABLE = new Set(["chat.completions", "responses", "messages", "messages.beta", "completions.legacy"]);
const LC_CALLS ={ invoke: false, stream: true, batch: false, streamEvents: true };
const LC_DERIVE = new Set(["withStructuredOutput", "bindTools", "withConfig", "withRetry", "bind", "withFallbacks"]);

// Member-chain call matcher. `n` is the list of property names ending at the called method.
// Returns {surface, len (number of trailing names that belong to the API path), stream (method-implied) }.
function matchChain(n) {
  const L = n.length;
  const at = (i) => n[L + i]; // at(-1) = method
  const is = (i, v) => (Array.isArray(v) ? v.includes(at(i)) : at(i) === v);
  const m = at(-1);
  if (L >= 4 && is(-4, "beta") && is(-3, "chat") && is(-2, "completions")) {
    if (m === "parse") return { surface: "chat.completions.parse", len: 4 };
    if (m === "stream") return { surface: "chat.completions.stream", len: 4, stream: true };
    if (m === "create" || m === "runTools") return { surface: "chat.completions", len: 4 };
  }
  if (L >= 3 && is(-3, "chat") && is(-2, "completions")) {
    if (m === "create" || m === "runTools") return { surface: "chat.completions", len: 3 };
    if (m === "parse") return { surface: "chat.completions.parse", len: 3 };
    if (m === "stream") return { surface: "chat.completions.stream", len: 3, stream: true };
  }
  if (L >= 2 && is(-2, "responses") && ["create", "stream", "parse"].includes(m))
    return { surface: "responses", len: L >= 3 && is(-3, "beta") ? 3 : 2, stream: m === "stream" ? true : undefined };
  // beta.messages.* (incl. beta.messages.batches.*)
  for (let k = 3; k <= 4; k++) if (L >= k && is(-k, "beta") && is(-k + 1, "messages")) return { surface: "messages.beta", len: k, stream: m === "stream" ? true : undefined };
  if (L >= 3 && is(-3, "messages") && is(-2, "batches")) return { surface: "other", len: 3 };
  if (L >= 2 && is(-2, "messages")) {
    if (m === "create") return { surface: "messages", len: 2 };
    if (m === "stream") return { surface: "messages.stream", len: 2, stream: true };
    if (m === "countTokens") return { surface: "messages.count_tokens", len: 2 };
  }
  if (L >= 2 && is(-2, "embeddings") && m === "create") return { surface: "embeddings", len: 2 };
  if (L >= 2 && is(-2, "images") && ["generate", "edit", "createVariation"].includes(m)) return { surface: "images", len: 2 };
  for (let k = 2; k <= 3; k++) if (L >= k && is(-k, "audio")) return { surface: "audio", len: k };
  for (let k = 2; k <= 3; k++)
    if (L >= k && is(-k, "vectorStores")) return { surface: "vector_stores", len: L > k && is(-k - 1, "beta") ? k + 1 : k };
  if (L >= 3 && is(-3, "beta") && is(-2, "assistants")) return { surface: "assistants", len: 3 };
  for (let k = 3; k <= 5; k++) if (L >= k && is(-k, "beta") && is(-k + 1, "threads")) return { surface: "assistants", len: k };
  if (L >= 2 && is(-2, "files") && ["create", "list", "retrieve", "del", "delete", "content", "retrieveContent", "waitForProcessing", "upload"].includes(m))
    return { surface: "files", len: L >= 3 && is(-3, "beta") ? 3 : 2 };
  if (L >= 2 && is(-2, "models") && (m === "list" || m === "retrieve")) return { surface: "models", len: L >= 3 && is(-3, "beta") ? 3 : 2 };
  if (L >= 2 && is(-2, "completions") && m === "create" && !(L >= 3 && is(-3, "chat"))) return { surface: "completions.legacy", len: 2 };
  if (L >= 2 && is(-2, "moderations") && m === "create") return { surface: "other", len: 2 };
  return null;
}

const NONPROD_DIRS = new Set(["test", "tests", "__tests__", "__test__", "__mocks__", "__fixtures__", "fixtures", "mocks", "spec", "specs", "e2e",
  "example", "examples", "docs", "doc", "notebook", "notebooks", "eval", "evals", "evaluation", "evaluations", "benchmark", "benchmarks", "bench",
  "cookbook", "cookbooks", "scripts", "playwright", "cypress", "stories", ".storybook"]);

function isProd(rel) {
  const parts = rel.split("/");
  const file = parts.pop().toLowerCase();
  if (parts.some((p) => NONPROD_DIRS.has(p.toLowerCase()))) return false;
  if (/\.(test|spec|stories|story|mock|mocks|bench|e2e)\.[cm]?[jt]sx?$/.test(file)) return false;
  if (/^test[_-]/.test(file) || /[_-]test\.[cm]?[jt]sx?$/.test(file)) return false;
  return true;
}

// ---------------------------------------------------------------------------------------------------------------
// AST helpers

function skipOuter(e) {
  while (e && (ts.isParenthesizedExpression(e) || ts.isAwaitExpression(e) || ts.isAsExpression(e) || ts.isNonNullExpression(e) ||
    ts.isTypeAssertionExpression(e) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(e)))) e = e.expression;
  return e;
}

function isRequireCall(e) {
  return ts.isCallExpression(e) && ts.isIdentifier(e.expression) && e.expression.text === "require" && e.arguments.length === 1 && ts.isStringLiteralLike(e.arguments[0]);
}
function isImportCall(e) {
  return ts.isCallExpression(e) && e.expression.kind === ts.SyntaxKind.ImportKeyword && e.arguments.length >= 1 && ts.isStringLiteralLike(e.arguments[0]);
}

function propName(p) {
  if (!p.name) return null;
  if (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name) || ts.isPrivateIdentifier(p.name)) return p.name.text;
  return null;
}

function getProp(obj, name) {
  if (!obj || !ts.isObjectLiteralExpression(obj)) return undefined;
  for (const p of obj.properties) {
    if ((ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p) || ts.isMethodDeclaration(p)) && propName(p) === name) return p;
  }
  return undefined;
}
function propValue(p) {
  if (!p) return undefined;
  if (ts.isPropertyAssignment(p)) return p.initializer;
  if (ts.isShorthandPropertyAssignment(p)) return p.name;
  return undefined;
}

function normRecv(text) {
  return text.replace(/\s+/g, "").replace(/!/g, "").replace(/\?\./g, ".");
}

function isSimpleExpr(e) {
  if (ts.isIdentifier(e) || e.kind === ts.SyntaxKind.ThisKeyword) return true;
  if (ts.isPropertyAccessExpression(e)) return isSimpleExpr(e.expression);
  if (ts.isNonNullExpression(e) || ts.isParenthesizedExpression(e)) return isSimpleExpr(e.expression);
  return false;
}

// ---------------------------------------------------------------------------------------------------------------
// analysis of one file

function scriptKind(file) {
  const ext = path.extname(file).toLowerCase();
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".jsx") return ts.ScriptKind.JSX;
  if (ext === ".js" || ext === ".mjs" || ext === ".cjs") return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function parse(file, text) {
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, scriptKind(file));
}

function analyze(sf, rel) {
  const text = sf.text;
  const lines = text.split(/\r?\n/);
  const bindings = new Map(); // local name -> {module, kind: default|named|namespace|cjs, name, node, decl}
  const fileSdks = new Set();
  let importsLLM = false;
  const found = []; // {site, node, info}
  const prod = isProd(rel);

  const pos = (node) => sf.getLineAndCharacterOfPosition(node.getStart(sf));
  const src = (node) => node.getText(sf);
  function mkSite(node, kind, fields) {
    const { line, character } = pos(node);
    const ln = (lines[line] || "").trim();
    return {
      id: `ts:${rel}:${line + 1}:${character}:${kind}`,
      lang: "ts", file: rel, line: line + 1, col: character, kind,
      sdk: fields.sdk, surface: fields.surface ?? null,
      stream: fields.stream ?? null, async: null,
      model: fields.model ?? null, base_url: fields.base_url ?? null,
      receiver: fields.receiver ?? null, prod, snippet: ln.length > 160 ? ln.slice(0, 160) : ln,
    };
  }

  function noteModule(m) {
    if (!isLLMModule(m)) return;
    importsLLM = true;
    if (m === "openai" || m.startsWith("openai/")) fileSdks.add("openai");
    else if (m.startsWith("@anthropic-ai/")) fileSdks.add("anthropic");
    else if (PROVIDER_SDK[m]) fileSdks.add(PROVIDER_SDK[m][0]);
  }
  function bind(local, info) {
    bindings.set(local, info);
    noteModule(info.module);
  }

  // -- pass 1: module bindings (import, import =, require, dynamic import)
  function findLoad(init) {
    const e = skipOuter(init);
    if (isRequireCall(e)) return { module: e.arguments[0].text, how: "require" };
    if (ts.isPropertyAccessExpression(e) && isRequireCall(skipOuter(e.expression))) return { module: skipOuter(e.expression).arguments[0].text, how: "prop", prop: e.name.text };
    if (isImportCall(e)) return { module: e.arguments[0].text, how: "import" };
    let hit = null;
    const walk = (n) => {
      if (hit) return;
      if ((isImportCall(n) || isRequireCall(n)) && isLLMModule(n.arguments[0].text)) hit = { module: n.arguments[0].text, how: "import" };
      else ts.forEachChild(n, walk);
    };
    walk(e);
    return hit;
  }
  function visit1(node) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const m = node.moduleSpecifier.text;
      noteModule(m);
      const c = node.importClause;
      if (c) {
        const typeOnly = !!c.isTypeOnly;
        if (c.name) bind(c.name.text, { module: m, kind: "default", name: "default", node: c.name, decl: node, typeOnly });
        if (c.namedBindings) {
          if (ts.isNamespaceImport(c.namedBindings)) bind(c.namedBindings.name.text, { module: m, kind: "namespace", name: "*", node: c.namedBindings, decl: node, typeOnly });
          else for (const el of c.namedBindings.elements)
            bind(el.name.text, { module: m, kind: "named", name: (el.propertyName || el.name).text, node: el, decl: node, typeOnly: typeOnly || !!el.isTypeOnly });
        }
      }
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference) && ts.isStringLiteralLike(node.moduleReference.expression)) {
      bind(node.name.text, { module: node.moduleReference.expression.text, kind: "cjs", name: "default", node, decl: node });
    } else if (ts.isVariableDeclaration(node) && node.initializer) {
      const ld = findLoad(node.initializer);
      if (ld) {
        if (ts.isIdentifier(node.name)) {
          if (ld.how === "require") bind(node.name.text, { module: ld.module, kind: "cjs", name: "default", node, decl: node, require: true });
          else if (ld.how === "prop") bind(node.name.text, { module: ld.module, kind: ld.prop === "default" ? "default" : "named", name: ld.prop, node, decl: node, require: true });
          else bind(node.name.text, { module: ld.module, kind: "namespace", name: "*", node, decl: node });
        } else if (ts.isObjectBindingPattern(node.name)) {
          for (const el of node.name.elements) {
            if (!ts.isIdentifier(el.name)) continue;
            const imported = el.propertyName && (ts.isIdentifier(el.propertyName) || ts.isStringLiteral(el.propertyName)) ? el.propertyName.text : el.name.text;
            bind(el.name.text, { module: ld.module, kind: imported === "default" ? "default" : "named", name: imported, node: el, decl: node, require: ld.how !== "import" });
          }
        }
      }
    }
    ts.forEachChild(node, visit1);
  }
  visit1(sf);

  function resolveRef(expr) {
    const e = skipOuter(expr);
    if (!e) return null;
    if (ts.isIdentifier(e)) {
      const b = bindings.get(e.text);
      if (!b) return null;
      if (b.kind === "namespace") return { module: b.module, name: "*", binding: b };
      return { module: b.module, name: b.kind === "cjs" ? "default" : b.name, binding: b };
    }
    if (ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.expression)) {
      const b = bindings.get(e.expression.text);
      if (b && (b.kind === "namespace" || b.kind === "cjs")) return { module: b.module, name: e.name.text, binding: b };
    }
    return null;
  }

  // same-file const string / object lookup
  const varDecls = new Map(); // name -> [VariableDeclaration]
  (function collect(n) {
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name)) {
      if (!varDecls.has(n.name.text)) varDecls.set(n.name.text, []);
      varDecls.get(n.name.text).push(n);
    }
    ts.forEachChild(n, collect);
  })(sf);
  function constString(id) {
    for (const d of varDecls.get(id.text) || []) {
      if (!(d.parent && ts.isVariableDeclarationList(d.parent) && d.parent.flags & ts.NodeFlags.Const)) continue;
      const init = d.initializer && skipOuter(d.initializer);
      if (init && ts.isStringLiteralLike(init)) return init.text;
    }
    return null;
  }
  function staticString(expr) {
    const e = skipOuter(expr);
    if (!e) return null;
    if (ts.isStringLiteralLike(e)) return e.text;
    if (ts.isIdentifier(e)) return constString(e);
    return null;
  }
  function objectArg(expr, before) {
    const e = skipOuter(expr);
    if (!e) return null;
    if (ts.isObjectLiteralExpression(e)) return e;
    if (ts.isIdentifier(e)) {
      // nearest preceding same-name declaration with an object-literal initializer
      let best = null;
      for (const d of varDecls.get(e.text) || []) {
        const init = d.initializer && skipOuter(d.initializer);
        if (init && ts.isObjectLiteralExpression(init) && d.getStart(sf) < before) best = init;
      }
      return best;
    }
    return null;
  }
  function valueOf(expr) {
    if (!expr) return null;
    const s = staticString(expr);
    if (s !== null) return { value: s, static: true };
    const t = src(skipOuter(expr) || expr).replace(/\s+/g, " ");
    return { value: t.length > 120 ? t.slice(0, 120) : t, static: false };
  }

  // -- pass 2: clients, receivers, LangChain instances, agents, Vercel provider variables
  const recvSdk = new Map(); // normalized receiver text -> {sdk, model}
  const lcInst = new Map(); // receiver -> {sdk, model}
  const agents = new Map(); // receiver -> {node, model, used}
  const aiProviders = new Map(); // local var -> sdk (const p = createOpenAI(...))
  const clientNodes = new Set();

  function assignTarget(node) {
    let p = node.parent;
    let child = node;
    while (p && (ts.isParenthesizedExpression(p) || ts.isAwaitExpression(p) || ts.isAsExpression(p) || ts.isNonNullExpression(p) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(p)))) {
      child = p;
      p = p.parent;
    }
    if (!p) return null;
    if (ts.isVariableDeclaration(p) && p.initializer === child && ts.isIdentifier(p.name)) return p.name.text;
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.EqualsToken && p.right === child) return normRecv(src(p.left));
    if (ts.isPropertyDeclaration(p) && p.initializer === child) return `this.${propName(p)}`;
    return null;
  }

  function clientFields(info, args) {
    const opts = args && args.length ? skipOuter(args[0]) : null;
    let model = null;
    let base_url = null;
    let api_key = null;
    if (opts && ts.isObjectLiteralExpression(opts)) {
      let kp = null;
      for (const k of ["apiKey", "openAIApiKey", "anthropicApiKey"]) if ((kp = getProp(opts, k))) break;
      if (kp) {
        const v = valueOf(propValue(kp)) || { value: null, static: false };
        const text = v.static ? "" : String(v.value || "");
        // a key read from the request/user/profile object: per-tenant credentials, not the operator's env key
        const m = text.match(/\b(profile|user|session|req|request|body|params|tenant|workspace|account)\??\.[\w.?]*/);
        api_key = { value: v.static ? null : text.slice(0, 120), static: !!v.static, per_tenant: !!m, why: m ? m[0] : null };
      }
      const mp = getProp(opts, "model") || getProp(opts, "modelName");
      model = mp ? valueOf(propValue(mp)) : null;
      let bp = null;
      for (const k of ["baseURL", "baseUrl", "basePath", "serverURL", "host", "endpoint"]) if ((bp = getProp(opts, k))) break;
      if (!bp && info.lc) {
        const cfg = propValue(getProp(opts, info.patch === "langchain-anthropic" ? "clientOptions" : "configuration"));
        const c = cfg && skipOuter(cfg);
        if (c && ts.isObjectLiteralExpression(c)) bp = getProp(c, "baseURL");
        else if (c) base_url = { value: null, static: false };
        if (!bp && info.lc) bp = getProp(opts, "anthropicApiUrl");
      }
      if (bp) base_url = valueOf(propValue(bp)) || { value: null, static: false };
      else if (!base_url && opts.properties.some((p) => ts.isSpreadAssignment(p))) base_url = { value: null, static: false };
    } else if (opts && info.patch) {
      base_url = { value: null, static: false };
    }
    return { model, base_url, api_key };
  }

  function addClient(node, info, args, anchor) {
    const f = clientFields(info, args);
    const site = mkSite(anchor || node, "client", { sdk: info.sdk, surface: info.surface, model: f.model, base_url: f.base_url });
    if (f.api_key) site.api_key = f.api_key;
    site.wrapped = /\brouter(Options|Fetch)\(/.test(node.getText(sf));  // already goes through the vendored runtime
    found.push({ site, node, info });
    clientNodes.add(node);
    const r = assignTarget(node);
    if (r) site.target = r;
    if (r) {
      recvSdk.set(r, { sdk: info.sdk });
      if (info.lc && info.surface !== "OpenAIEmbeddings") lcInst.set(r, { sdk: info.sdk, model: f.model });
    }
    return r;
  }

  function visit2(node) {
    if (ts.isNewExpression(node)) {
      const ref = resolveRef(node.expression);
      if (ref) {
        const info = newClass(ref.module, ref.name);
        if (info && info.agent) {
          const r = assignTarget(node);
          const opts = node.arguments && node.arguments.length ? objectArg(node.arguments[0], node.getStart(sf)) : null;
          agents.set(r || `@${node.getStart(sf)}`, { node, model: opts ? vercelModel(propValue(getProp(opts, "model"))) : null, used: false });
        } else if (info) addClient(node, info, node.arguments);
      }
    } else if (ts.isCallExpression(node)) {
      const ref = resolveRef(node.expression);
      if (ref) {
        const info = factory(ref.module, ref.name);
        if (info) {
          const r = addClient(node, info, node.arguments);
          if (r && !r.includes(".")) aiProviders.set(r, info.sdk);
        }
      }
    }
    ts.forEachChild(node, visit2);
  }

  // default provider instances are reported at their import specifier
  for (const [local, b] of bindings) {
    if (b.typeOnly || b.kind !== "named") continue;
    const info = defaultInstance(b.module, b.name);
    if (!info) continue;
    const site = mkSite(b.node, "client", { sdk: info.sdk, surface: info.surface, model: null, base_url: null });
    site.target = local;
    found.push({ site, node: b.node, info: { ...info, local, binding: b } });
  }

  // Vercel model expression -> {sdk, model}
  function vercelModel(expr, depth = 0) {
    if (!expr) return { sdk: "vercel-ai", model: null };
    const e = skipOuter(expr);
    if (ts.isStringLiteralLike(e)) return { sdk: "vercel-ai-gateway", model: { value: e.text, static: true } };
    if (ts.isCallExpression(e)) {
      let base = e.expression;
      if (ts.isPropertyAccessExpression(base)) base = base.expression;
      let sdk = null;
      const bt = skipOuter(base);
      const receiver = ts.isIdentifier(bt) ? bt.text : null; // the provider instance the model comes from: custom('gpt-4o')
      const ref = resolveRef(base);
      if (ref) {
        const di = defaultInstance(ref.module, ref.name);
        if (di) sdk = di.sdk;
      } else {
        const b = skipOuter(base);
        if (ts.isIdentifier(b) && aiProviders.has(b.text)) sdk = aiProviders.get(b.text);
        else if (ts.isCallExpression(b)) {
          const r2 = resolveRef(b.expression);
          const f = r2 && factory(r2.module, r2.name);
          if (f) sdk = f.sdk;
        }
      }
      if (sdk) {
        const m = e.arguments.length ? staticString(e.arguments[0]) : null;
        return { sdk, receiver, model: m !== null ? { value: m, static: true } : e.arguments.length ? valueOf(e.arguments[0]) : null };
      }
    }
    if (ts.isIdentifier(e) && depth < 2) {
      for (const d of varDecls.get(e.text) || []) if (d.initializer) {
        const r = vercelModel(d.initializer, depth + 1);
        if (r.sdk !== "vercel-ai") return r;
      }
    }
    return { sdk: "vercel-ai", model: valueOf(e) };
  }

  visit2(sf);

  // -- pass 2b: type annotations give receiver SDKs; LangChain derivations
  function typeClient(t) {
    if (!t) return null;
    if (ts.isUnionTypeNode(t)) {
      for (const x of t.types) {
        const r = typeClient(x);
        if (r) return r;
      }
      return null;
    }
    if (ts.isTypeReferenceNode(t) && ts.isIdentifier(t.typeName)) {
      const b = bindings.get(t.typeName.text);
      if (!b) return null;
      const info = newClass(b.module, b.kind === "cjs" ? "default" : b.name);
      return info && !info.agent ? info : null;
    }
    return null;
  }
  (function visitTypes(n) {
    if ((ts.isPropertyDeclaration(n) || ts.isParameter(n) || ts.isVariableDeclaration(n) || ts.isPropertySignature(n)) && n.type && n.name && ts.isIdentifier(n.name)) {
      const info = typeClient(n.type);
      if (info) {
        const name = n.name.text;
        const keys = [];
        if (ts.isPropertyDeclaration(n) || ts.isPropertySignature(n)) keys.push(`this.${name}`);
        else if (ts.isParameter(n)) {
          keys.push(name);
          if (n.modifiers && n.modifiers.some((m) => [ts.SyntaxKind.PrivateKeyword, ts.SyntaxKind.PublicKeyword, ts.SyntaxKind.ProtectedKeyword, ts.SyntaxKind.ReadonlyKeyword].includes(m.kind))) keys.push(`this.${name}`);
        } else keys.push(name);
        for (const k of keys) if (!recvSdk.has(k)) recvSdk.set(k, { sdk: info.sdk });
      }
    }
    ts.forEachChild(n, visitTypes);
  })(sf);
  for (let round = 0; round < 3; round++) {
    (function derive(n) {
      if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && LC_DERIVE.has(n.expression.name.text)) {
        const base = lcInst.get(normRecv(src(n.expression.expression)));
        if (base) {
          const r = assignTarget(n);
          if (r && !lcInst.has(r)) lcInst.set(r, base);
        }
      }
      ts.forEachChild(n, derive);
    })(sf);
  }

  // -- pass 3: calls, raw_http, env
  function inferSdk(recv, surface) {
    const hit = recvSdk.get(recv);
    if (hit) return hit.sdk;
    if (surface.startsWith("messages") && fileSdks.has("anthropic")) return "anthropic";
    if (fileSdks.has("openai")) return "openai";
    if (fileSdks.has("anthropic")) return "anthropic";
    for (const s of fileSdks) return s;
    return "other";
  }

  function visit3(node) {
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      const ref = resolveRef(callee);
      let handled = false;
      if (ref && ref.module === "ai" && AI_FUNCS[ref.name]) {
        const [surface, stream] = AI_FUNCS[ref.name];
        const opts = node.arguments.length ? objectArg(node.arguments[0], node.getStart(sf)) : null;
        const vm = opts ? vercelModel(propValue(getProp(opts, "model"))) : { sdk: "vercel-ai", model: null };
        found.push({ site: mkSite(node, "call", { sdk: vm.sdk, surface, stream, model: vm.model, receiver: vm.receiver || null }), node, info: null });
        handled = true;
      }
      if (!handled && ts.isPropertyAccessExpression(callee)) {
        const names = [];
        let e = callee;
        while (ts.isPropertyAccessExpression(e)) {
          names.unshift(e.name.text);
          e = skipOuter(e.expression);
        }
        const method = callee.name.text;
        const recvNode = callee.expression;
        const recv = normRecv(src(recvNode));
        if (method in LC_CALLS && lcInst.has(recv)) {
          const inst = lcInst.get(recv);
          found.push({ site: mkSite(node, "call", { sdk: inst.sdk, surface: "framework.chat", stream: LC_CALLS[method], model: inst.model, receiver: src(recvNode) }), node, info: null });
          handled = true;
        } else if ((method === "generate" || method === "stream") && agents.has(recv)) {
          const ag = agents.get(recv);
          ag.used = true;
          const vm = ag.model || { sdk: "vercel-ai", model: null };
          found.push({ site: mkSite(node, "call", { sdk: vm.sdk, surface: "framework.chat", stream: method === "stream", model: vm.model, receiver: src(recvNode) }), node, info: null });
          handled = true;
        } else if (importsLLM) {
          const m = matchChain(names);
          if (m && names.length >= m.len) {
            let r = callee;
            for (let i = 0; i < m.len; i++) r = r.expression;
            const rtext = names.length > m.len || r ? src(r) : null;
            const opts = node.arguments.length ? objectArg(node.arguments[0], node.getStart(sf)) : null;
            let stream = m.stream ?? false;
            if (m.stream === undefined && opts) {
              const sp = propValue(getProp(opts, "stream"));
              if (sp) {
                const s = skipOuter(sp);
                stream = s.kind === ts.SyntaxKind.TrueKeyword ? true : s.kind === ts.SyntaxKind.FalseKeyword ? false : null;
              }
            } else if (m.stream === undefined && node.arguments.length && !opts && STREAMABLE.has(m.surface)) stream = null;
            const mp = opts ? getProp(opts, "model") : null;
            const model = mp ? valueOf(propValue(mp)) : null;
            found.push({ site: mkSite(node, "call", { sdk: inferSdk(normRecv(rtext || ""), m.surface), surface: m.surface, stream, model, receiver: rtext }), node, info: null });
          }
        }
      }
    } else if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) {
      const parent = node.parent;
      const isSpecifier = parent && (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent) || ts.isExternalModuleReference(parent));
      if (!isSpecifier) {
        const t = src(node);
        const m = /api\.(openai|anthropic)\.com/.exec(t);
        if (m) {
          const surface = /\/chat\/completions/.test(t) ? "chat.completions" : /\/responses/.test(t) ? "responses" : /\/messages/.test(t) ? "messages"
            : /\/embeddings/.test(t) ? "embeddings" : /\/models/.test(t) ? "models" : /\/images/.test(t) ? "images" : /\/audio/.test(t) ? "audio"
            : /\/files/.test(t) ? "files" : /\/completions/.test(t) ? "completions.legacy" : "other";
          found.push({ site: mkSite(node, "raw_http", { sdk: m[1], surface }), node, info: null });
        }
      }
      if (ts.isTemplateExpression(node)) {
        // still walk substitutions for nested calls/env reads
        for (const span of node.templateSpans) visit3(span.expression);
      }
      return;
    } else if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      const obj = skipOuter(node.expression);
      if (obj && ts.isPropertyAccessExpression(obj) && ts.isIdentifier(obj.expression) && obj.expression.text === "process" && obj.name.text === "env") {
        const name = ts.isPropertyAccessExpression(node) ? node.name.text : ts.isStringLiteralLike(node.argumentExpression) ? node.argumentExpression.text : null;
        if (name && /^(OPENAI|ANTHROPIC)_/.test(name)) found.push({ site: mkSite(node, "env", { sdk: name.startsWith("OPENAI") ? "openai" : "anthropic", surface: name }), node, info: null });
      }
    } else if (ts.isVariableDeclaration(node) && ts.isObjectBindingPattern(node.name) && node.initializer) {
      const init = skipOuter(node.initializer);
      if (ts.isPropertyAccessExpression(init) && ts.isIdentifier(init.expression) && init.expression.text === "process" && init.name.text === "env") {
        for (const el of node.name.elements) {
          const name = el.propertyName && ts.isIdentifier(el.propertyName) ? el.propertyName.text : ts.isIdentifier(el.name) ? el.name.text : null;
          if (name && /^(OPENAI|ANTHROPIC)_/.test(name)) found.push({ site: mkSite(el, "env", { sdk: name.startsWith("OPENAI") ? "openai" : "anthropic", surface: name }), node: el, info: null });
        }
      }
    }
    ts.forEachChild(node, visit3);
  }
  visit3(sf);

  // Agents never invoked in this file: count the construction as the call site.
  for (const [, ag] of agents) {
    if (ag.used) continue;
    const vm = ag.model || { sdk: "vercel-ai", model: null };
    found.push({ site: mkSite(ag.node, "call", { sdk: vm.sdk, surface: "framework.chat", stream: null, model: vm.model }), node: ag.node, info: null });
  }

  // de-duplicate by id (keep first)
  const seen = new Set();
  const out = [];
  for (const f of found) {
    if (seen.has(f.site.id)) continue;
    seen.add(f.site.id);
    out.push(f);
  }
  out.sort((a, b) => a.site.line - b.site.line || a.site.col - b.site.col);
  return { found: out, bindings };
}

// ---------------------------------------------------------------------------------------------------------------
// scan

function walk(root, onFile) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let ents;
    try {
      ents = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    ents.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const ent of ents) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (SKIP_DIRS.has(ent.name) || ent.name.startsWith(".")) continue;
        stack.push(p);
      } else if (ent.isFile()) {
        const ext = path.extname(ent.name).toLowerCase();
        if (!SOURCE_EXTS.has(ext)) continue;
        if (/\.d\.[cm]?ts$/.test(ent.name) || /\.min\.[cm]?js$/.test(ent.name)) continue;
        onFile(p);
      }
    }
  }
}

function relOf(root, p) {
  return path.relative(root, p).split(path.sep).join("/");
}

function scan(root) {
  root = path.resolve(root);
  const sites = [];
  const errors = [];
  let files = 0;
  const all = [];
  walk(root, (p) => all.push(p));
  all.sort();
  for (const p of all) {
    const rel = relOf(root, p);
    try {
      const st = fs.statSync(p);
      if (st.size > MAX_BYTES) continue;
      const text = fs.readFileSync(p, "utf8");
      const sf = parse(p, text);
      files++;
      const { found } = analyze(sf, rel);
      for (const f of found) sites.push(f.site);
    } catch (e) {
      errors.push({ file: rel, error: String(e && e.message ? e.message : e) });
    }
  }
  return { root, files_scanned: files, sites, errors };
}

// ---------------------------------------------------------------------------------------------------------------
// apply

function nearestUp(startDir, stopDir, name) {
  let d = startDir;
  while (true) {
    const p = path.join(d, name);
    if (fs.existsSync(p)) return p;
    if (d === stopDir) return null;
    const up = path.dirname(d);
    if (up === d || !(up + path.sep).startsWith(stopDir + path.sep) && up !== stopDir) return null;
    d = up;
  }
}

const tsconfigCache = new Map();
function moduleResolutionOf(tsconfigPath) {
  if (tsconfigCache.has(tsconfigPath)) return tsconfigCache.get(tsconfigPath);
  let mr = null;
  try {
    const cfg = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
    const host = { ...ts.sys, readDirectory: () => [] };
    const parsed = ts.parseJsonConfigFileContent(cfg.config || {}, host, path.dirname(tsconfigPath));
    const o = parsed.options;
    mr = o.moduleResolution;
    if (mr === undefined) {
      if (o.module === ts.ModuleKind.Node16 || o.module === ts.ModuleKind.NodeNext) mr = o.module === ts.ModuleKind.Node16 ? ts.ModuleResolutionKind.Node16 : ts.ModuleResolutionKind.NodeNext;
      else if (ts.ModuleKind.Preserve !== undefined && o.module === ts.ModuleKind.Preserve) mr = ts.ModuleResolutionKind.Bundler;
    }
  } catch {}
  tsconfigCache.set(tsconfigPath, mr);
  return mr;
}

const BUNDLERS = ["next", "vite", "webpack", "esbuild", "tsup", "rollup", "parcel", "@rspack/core", "pkgroll", "unbuild", "@vitejs/plugin-react"];
function readPkg(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
  } catch {
    return null;
  }
}

function runtimePlan(fileAbs, root, isJsFile, fileFormat) {
  const dir = path.dirname(fileAbs);
  const pj = nearestUp(dir, root, "package.json");
  const pkgRoot = pj ? path.dirname(pj) : root;
  const pkg = (pj && readPkg(pkgRoot)) || {};
  const tsconfig = nearestUp(dir, root, "tsconfig.json");
  const pkgHasTsconfig = !!tsconfig && (path.dirname(tsconfig) + path.sep).startsWith(pkgRoot + path.sep);
  const libDir = fs.existsSync(path.join(pkgRoot, "src")) && fs.statSync(path.join(pkgRoot, "src")).isDirectory() ? path.join(pkgRoot, "src", "lib") : path.join(pkgRoot, "lib");
  const isModulePkg = pkg.type === "module";
  if (isJsFile && !pkgHasTsconfig) {
    const ext = path.extname(fileAbs).toLowerCase();
    let name;
    if (fileFormat === "cjs") name = isModulePkg || ext === ".cjs" ? "routerRuntime.cjs" : "routerRuntime.js";
    else name = isModulePkg && (ext === ".js" || ext === ".jsx") ? "routerRuntime.js" : "routerRuntime.mjs";
    return { runtimeAbs: path.join(libDir, name), format: fileFormat, js: true, explicitExt: true };
  }
  const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}), ...(pkg.peerDependencies || {}) };
  const hasBundler = BUNDLERS.some((b) => b in deps);
  const mr = tsconfig ? moduleResolutionOf(tsconfig) : null;
  const nodeMr = mr === ts.ModuleResolutionKind.Node16 || mr === ts.ModuleResolutionKind.NodeNext;
  const needJs = nodeMr || (isModulePkg && !hasBundler && (!tsconfig || mr !== ts.ModuleResolutionKind.Bundler));
  return { runtimeAbs: path.join(libDir, "routerRuntime.ts"), format: "ts", js: false, needJs };
}

function specifierFor(fileAbs, plan) {
  let rel = path.relative(path.dirname(fileAbs), plan.runtimeAbs).split(path.sep).join("/");
  if (!rel.startsWith(".")) rel = "./" + rel;
  if (plan.js) return rel; // explicit .js/.mjs/.cjs
  rel = rel.replace(/\.ts$/, "");
  return plan.needJs ? rel + ".js" : rel;
}

function runtimeText(runtimeSrc, plan) {
  if (!plan.js) return runtimeSrc;
  const out = ts.transpileModule(runtimeSrc, {
    compilerOptions: { module: plan.format === "cjs" ? ts.ModuleKind.CommonJS : ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, removeComments: false },
    fileName: "routerRuntime.ts",
  });
  return out.outputText;
}

// --- text editing helpers

function lineStartOf(text, posn) {
  const i = text.lastIndexOf("\n", posn - 1);
  return i + 1;
}
function lineEndOf(text, posn) {
  const i = text.indexOf("\n", posn);
  return i < 0 ? text.length : i;
}
function indentAt(text, posn) {
  const s = lineStartOf(text, posn);
  const m = /^[ \t]*/.exec(text.slice(s));
  return m ? m[0] : "";
}
// position just after the trailing comma that follows `from` (skipping whitespace/comments), or -1
function commaAfter(text, from) {
  let i = from;
  while (i < text.length) {
    const c = text[i];
    if (c === " " || c === "\t" || c === "\n" || c === "\r") i++;
    else if (text.startsWith("//", i)) i = lineEndOf(text, i);
    else if (text.startsWith("/*", i)) {
      const j = text.indexOf("*/", i + 2);
      i = j < 0 ? text.length : j + 2;
    } else if (c === ",") return i + 1;
    else return -1;
  }
  return -1;
}

// Append `propText` as the last property of an object literal (minimal edit, style-preserving).
function insertProperty(sf, obj, propText, edits) {
  const text = sf.text;
  const props = obj.properties;
  if (props.length === 0) {
    edits.push({ start: obj.getStart(sf), end: obj.end, text: `{ ${propText} }` });
    return;
  }
  const last = props[props.length - 1];
  const comma = commaAfter(text, last.end);
  const multiline = sf.getLineAndCharacterOfPosition(last.getStart(sf)).line !== sf.getLineAndCharacterOfPosition(obj.getStart(sf)).line ||
    sf.getLineAndCharacterOfPosition(obj.end).line !== sf.getLineAndCharacterOfPosition(last.end).line;
  if (multiline) {
    const indent = indentAt(text, last.getStart(sf));
    const after = comma >= 0 ? comma : last.end;
    const rest = text.slice(after, lineEndOf(text, after));
    const restIsTrivia = /^\s*(\/\/.*)?$/.test(rest);
    const at = restIsTrivia ? lineEndOf(text, after) : after;
    const line = `\n${indent}${propText}${comma >= 0 ? "," : ""}`;
    if (comma < 0 && at === last.end) edits.push({ start: at, end: at, text: "," + line });
    else {
      if (comma < 0) edits.push({ start: last.end, end: last.end, text: "," });
      edits.push({ start: at, end: at, text: line });
    }
  } else if (comma >= 0) {
    edits.push({ start: comma, end: comma, text: ` ${propText}` });
  } else {
    edits.push({ start: last.end, end: last.end, text: `, ${propText}` });
  }
}

function anyFetch(exprText, isJs) {
  return isJs ? `${exprText}?.fetch` : `(${exprText} as any)?.fetch`;
}

// RouterOpts a sites-file entry may carry (approved levers; see routerRuntime.ts RouterOpts)
const SITE_OPTS = { operatorKeyOnly: "boolean", unlockHosts: "strings", dropBetas: "strings" };

// The opts literal for a site ("" when none), or { error }.
function optsText(opts) {
  if (opts == null) return "";
  if (typeof opts !== "object" || Array.isArray(opts)) return { error: "opts must be an object" };
  const parts = [];
  for (const [k, v] of Object.entries(opts)) {
    const t = SITE_OPTS[k];
    if (!t) return { error: `unknown opts key ${k}` };
    if (t === "boolean" ? typeof v !== "boolean" : !Array.isArray(v) || !v.every((x) => typeof x === "string" && x)) return { error: `opts.${k} must be ${t === "boolean" ? "a boolean" : "an array of strings"}` };
    if (v === false || (Array.isArray(v) && !v.length)) continue;
    parts.push(`${k}: ${t === "boolean" ? v : `[${v.map((x) => JSON.stringify(x)).join(", ")}]`}`);
  }
  return parts.length ? `{ ${parts.join(", ")} }` : "";
}

// routerOptions()/routerFetch() argument list: the base fetch, then the site's opts literal if any.
function rargs(base, o) {
  return o ? `${base || "undefined"}, ${o}` : base;
}

// Inject router fetch into an options object literal. Returns action name.
function patchObjectLiteral(sf, obj, isJs, edits, needs, o = "") {
  const fp = getProp(obj, "fetch");
  if (fp) {
    if (ts.isPropertyAssignment(fp)) {
      edits.push({ start: fp.initializer.getStart(sf), end: fp.initializer.end, text: `routerFetch(${rargs(fp.initializer.getText(sf), o)})` });
    } else if (ts.isShorthandPropertyAssignment(fp)) {
      edits.push({ start: fp.getStart(sf), end: fp.end, text: `fetch: routerFetch(${rargs("fetch", o)})` });
    } else return { error: "fetch is declared as a method; not patched" };
    needs.add("routerFetch");
    return { action: "wrap_fetch" };
  }
  const spreads = obj.properties.filter((p) => ts.isSpreadAssignment(p));
  let arg = "";
  if (spreads.length) {
    const s = skipOuter(spreads[spreads.length - 1].expression);
    if (isSimpleExpr(s)) arg = anyFetch(s.getText(sf), isJs);
  }
  insertProperty(sf, obj, `...routerOptions(${rargs(arg, o)})`, edits);
  needs.add("routerOptions");
  return { action: "append_router_options" };
}

function wrapExpression(sf, e, isJs, edits, needs, o = "") {
  const t = e.getText(sf);
  const inner = skipOuter(e);
  const text = isSimpleExpr(inner)
    ? `{ ...${t}, ...routerOptions(${rargs(anyFetch(t, isJs), o)}) }`
    : `((o${isJs ? "" : ": any"}) => ({ ...o, ...routerOptions(${rargs("o?.fetch", o)}) }))(${t})`;
  edits.push({ start: e.getStart(sf), end: e.end, text });
  needs.add("routerOptions");
  return { action: "wrap_options_expression" };
}

function patchOptionsCall(sf, node, isJs, edits, needs, o = "") {
  const args = node.arguments;
  if (!args) {
    // `new OpenAI` without parentheses
    edits.push({ start: node.end, end: node.end, text: `({ ...routerOptions(${rargs("", o)}) })` });
    needs.add("routerOptions");
    return { action: "add_options" };
  }
  if (args.length === 0) {
    edits.push({ start: args.pos, end: args.pos, text: `{ ...routerOptions(${rargs("", o)}) }` });
    needs.add("routerOptions");
    return { action: "add_options" };
  }
  const a0 = args[0];
  if (ts.isSpreadElement(a0)) return { error: "options are passed as a spread argument list; not patched" };
  const inner = skipOuter(a0);
  if (ts.isObjectLiteralExpression(inner)) return patchObjectLiteral(sf, inner, isJs, edits, needs, o);
  return wrapExpression(sf, a0, isJs, edits, needs, o);
}

function patchLangChain(sf, node, key, isJs, edits, needs, o = "") {
  const args = node.arguments;
  if (!args || args.length === 0) {
    const text = `{ ${key}: { ...routerOptions(${rargs("", o)}) } }`;
    if (!args) edits.push({ start: node.end, end: node.end, text: `(${text})` });
    else edits.push({ start: args.pos, end: args.pos, text });
    needs.add("routerOptions");
    return { action: `add_${key}` };
  }
  if (args.length > 1) return { error: `${key} passed as a second constructor argument (legacy LangChain signature); not patched` };
  const a0 = args[0];
  if (ts.isSpreadElement(a0)) return { error: "options are passed as a spread argument list; not patched" };
  const obj = skipOuter(a0);
  if (ts.isObjectLiteralExpression(obj)) {
    const cp = getProp(obj, key);
    if (!cp) {
      insertProperty(sf, obj, `${key}: { ...routerOptions(${rargs("", o)}) }`, edits);
      needs.add("routerOptions");
      return { action: `add_${key}` };
    }
    if (ts.isShorthandPropertyAssignment(cp)) {
      edits.push({ start: cp.getStart(sf), end: cp.end, text: `${key}: { ...${key}, ...routerOptions(${rargs(anyFetch(key, isJs), o)}) }` });
      needs.add("routerOptions");
      return { action: `wrap_${key}` };
    }
    if (!ts.isPropertyAssignment(cp)) return { error: `${key} is not a plain property; not patched` };
    const c = skipOuter(cp.initializer);
    if (ts.isObjectLiteralExpression(c)) {
      const r = patchObjectLiteral(sf, c, isJs, edits, needs, o);
      return r.error ? r : { action: `${r.action}_in_${key}` };
    }
    wrapExpression(sf, cp.initializer, isJs, edits, needs, o);
    return { action: `wrap_${key}` };
  }
  const t = a0.getText(sf);
  const text = isSimpleExpr(obj)
    ? (isJs
      ? `{ ...${t}, ${key}: { ...${t}?.${key}, ...routerOptions(${rargs(`${t}?.${key}?.fetch`, o)}) } }`
      : `{ ...${t}, ${key}: { ...(${t} as any)?.${key}, ...routerOptions(${rargs(`(${t} as any)?.${key}?.fetch`, o)}) } }`)
    : `((o${isJs ? "" : ": any"}) => ({ ...o, ${key}: { ...o?.${key}, ...routerOptions(${rargs(`o?.${key}?.fetch`, o)}) } }))(${t})`;
  edits.push({ start: a0.getStart(sf), end: a0.end, text });
  needs.add("routerOptions");
  return { action: `wrap_options_expression_${key}` };
}

// major version of @ai-sdk/openai declared by the nearest package.json (null if unknown)
function aiSdkOpenAIMajor(fileAbs) {
  let d = path.dirname(fileAbs);
  while (true) {
    const pkg = readPkg(d);
    if (pkg) {
      const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}), ...(pkg.peerDependencies || {}) };
      const range = deps["@ai-sdk/openai"];
      if (range) {
        const m = /(\d+)/.exec(String(range));
        return m ? +m[1] : null;
      }
    }
    const up = path.dirname(d);
    if (up === d) return null;
    d = up;
  }
}

// Replace a default provider import specifier (openai / anthropic) with its factory.
function patchDefaultProvider(sf, f, bindings, edits, needs, consts, o = "") {
  const b = f.info.binding;
  if (b.require || !ts.isImportSpecifier(b.node)) return { error: "default provider loaded via require/dynamic import; not patched" };
  const decl = b.decl;
  const factoryName = f.info.factory;
  // existing factory import from the same module?
  let factoryLocal = null;
  for (const [local, x] of bindings) if (x.module === b.module && x.kind === "named" && x.name === factoryName && !x.typeOnly) factoryLocal = local;
  const el = b.node;
  const elements = el.parent.elements;
  if (factoryLocal) {
    if (elements.length === 1) {
      // whole declaration only imported the instance
      let end = decl.end;
      if (sf.text[end] === "\n") end++;
      else if (sf.text.startsWith("\r\n", end)) end += 2;
      edits.push({ start: decl.getStart(sf), end, text: "" });
    } else {
      const idx = elements.indexOf(el);
      if (idx < elements.length - 1) edits.push({ start: el.getStart(sf), end: elements[idx + 1].getStart(sf), text: "" });
      else edits.push({ start: elements[idx - 1].end, end: el.end, text: "" });
    }
  } else {
    edits.push({ start: el.getStart(sf), end: el.end, text: factoryName });
    factoryLocal = factoryName;
  }
  // @ai-sdk/openai < 2: the default instance is compatibility "strict" while createOpenAI() defaults to "compatible"
  const strict = factoryName === "createOpenAI" && aiSdkOpenAIMajor(sf.fileName) !== null && aiSdkOpenAIMajor(sf.fileName) < 2;
  consts.push(`const ${f.info.local} = ${factoryLocal}({ ${strict ? 'compatibility: "strict", ' : ""}...routerOptions(${rargs("", o)}) })`);
  needs.add("routerOptions");
  return { action: "default_provider_to_factory" };
}

function fileStyle(sf) {
  const imp = sf.statements.find((s) => ts.isImportDeclaration(s));
  const ref = imp || sf.statements[0];
  let quote = '"';
  if (imp && ts.isStringLiteral(imp.moduleSpecifier)) quote = imp.moduleSpecifier.getText(sf)[0];
  else {
    const m = /require\((['"])/.exec(sf.text) || /(['"])/.exec(sf.text);
    if (m) quote = m[1];
  }
  const semi = ref ? /;\s*$/.test(ref.getText(sf)) : true;
  return { quote, semi };
}

function usesCjs(sf, fileAbs) {
  const ext = path.extname(fileAbs).toLowerCase();
  if (ext === ".cjs") return true;
  if (ext === ".mjs") return false;
  if (sf.statements.some((s) => ts.isImportDeclaration(s) || ts.isExportDeclaration(s) || ts.isExportAssignment(s))) return false;
  return /\brequire\s*\(/.test(sf.text);
}

// Insert / merge the runtime import and any default-provider consts.
function importEdits(sf, specifier, needs, consts, cjs, edits) {
  const { quote, semi } = fileStyle(sf);
  const sc = semi ? ";" : "";
  const names = ["routerOptions", "routerFetch"].filter((n) => needs.has(n));
  const q = (s) => `${quote}${s}${quote}`;
  let existing = null;
  for (const s of sf.statements) {
    if (ts.isImportDeclaration(s) && ts.isStringLiteral(s.moduleSpecifier) && /routerRuntime(\.[cm]?[jt]s)?$/.test(s.moduleSpecifier.text)) existing = s;
  }
  let lastImport = null;
  for (const s of sf.statements) if (ts.isImportDeclaration(s)) lastImport = s;
  let block = "";
  if (existing) {
    const nb = existing.importClause && existing.importClause.namedBindings;
    if (nb && ts.isNamedImports(nb)) {
      const have = nb.elements.map((e) => e.name.text);
      const missing = names.filter((n) => !have.includes(n));
      if (missing.length) edits.push({ start: nb.elements[nb.elements.length - 1].end, end: nb.elements[nb.elements.length - 1].end, text: missing.map((m) => `, ${m}`).join("") });
    }
  } else if (cjs) {
    block = `const { ${names.join(", ")} } = require(${q(specifier)})${sc}\n`;
  } else {
    block = `import { ${names.join(", ")} } from ${q(specifier)}${sc}\n`;
  }
  if (!block && !consts.length) return;
  if (lastImport && !cjs) {
    // right after the import block: "<last import>\n<runtime import>\n\n<consts>" (the line's own newline follows)
    const at = lineEndOf(sf.text, lastImport.end);
    let t = block ? "\n" + block.replace(/\n$/, "") : "";
    if (consts.length) t += "\n\n" + consts.map((c) => c + sc).join("\n");
    edits.push({ start: at, end: at, text: t });
    return;
  }
  // no import declarations: after the shebang and directive prologue ("use client", "use strict")
  let at = 0;
  if (sf.text.startsWith("#!")) at = lineEndOf(sf.text, 0) + 1;
  for (const s of sf.statements) {
    if (ts.isExpressionStatement(s) && ts.isStringLiteral(s.expression)) at = lineEndOf(sf.text, s.end) + 1;
    else break;
  }
  if (at > sf.text.length) at = sf.text.length;
  edits.push({ start: at, end: at, text: block + consts.map((c) => `${c}${sc}\n`).join("") });
}

function applyEdits(text, edits) {
  // right to left; for equal positions apply later edits first so earlier-planned text stays in front
  const sorted = edits.map((e, i) => ({ ...e, i })).sort((a, b) => b.start - a.start || b.end - a.end || b.i - a.i);
  let out = text;
  let prevStart = Infinity;
  for (const e of sorted) {
    if (e.end > prevStart) throw new Error("overlapping edits");
    out = out.slice(0, e.start) + e.text + out.slice(e.end);
    prevStart = e.start;
  }
  return out;
}

function parseId(id) {
  const parts = id.split(":");
  if (parts.length < 5) return null;
  return { lang: parts[0], kind: parts[parts.length - 1], col: +parts[parts.length - 2], line: +parts[parts.length - 3], rel: parts.slice(1, -3).join(":") };
}

function apply(root, runtimePath, siteIds) {
  root = path.resolve(root);
  const runtimeSrc = fs.readFileSync(runtimePath, "utf8");
  const result = { edits: [], runtime_files: [], files_changed: [] };
  const byFile = new Map();
  const siteOpts = new Map(); // id -> opts from a { id, opts } entry
  for (let id of siteIds) {
    if (id && typeof id === "object") {
      siteOpts.set(String(id.id), id.opts);
      id = id.id;
    }
    const p = parseId(String(id));
    if (!p || p.lang !== "ts") {
      result.edits.push({ site_id: id, file: p ? p.rel : null, action: "none", ok: false, reason: "not a TypeScript/JavaScript site id" });
      continue;
    }
    if (!byFile.has(p.rel)) byFile.set(p.rel, []);
    byFile.get(p.rel).push(id);
  }
  const runtimesWritten = new Map(); // abs -> content
  const changed = new Set();

  for (const [rel, ids] of byFile) {
    const abs = path.join(root, rel);
    let text;
    try {
      text = fs.readFileSync(abs, "utf8");
    } catch (e) {
      for (const id of ids) result.edits.push({ site_id: id, file: rel, action: "none", ok: false, reason: "file not found" });
      continue;
    }
    const sf = parse(abs, text);
    const { found, bindings } = analyze(sf, rel);
    const byId = new Map(found.map((f) => [f.site.id, f]));
    const isJs = /\.(c|m)?jsx?$/i.test(abs);
    const edits = [];
    const needs = new Set();
    const consts = [];
    const fileResults = [];
    for (const id of ids) {
      const f = byId.get(id);
      const rec = { site_id: id, file: rel, action: "none", ok: false, reason: null };
      fileResults.push(rec);
      if (!f) {
        rec.reason = "site not found (file changed since scan?)";
        continue;
      }
      if (f.site.kind !== "client" || !f.info) {
        rec.reason = `not a client construction site (kind ${f.site.kind}); call sites are never patched`;
        continue;
      }
      if (!f.info.patch) {
        rec.reason = f.info.reason || NO_ROUTE(f.info.sdk);
        continue;
      }
      const nodeText = f.node.getText(sf);
      if (/\brouter(Options|Fetch)\b/.test(nodeText)) {
        rec.action = "skip";
        rec.ok = true;
        rec.reason = "already patched";
        continue;
      }
      const o = optsText(siteOpts.get(id));
      if (o.error) {
        rec.reason = o.error;
        continue;
      }
      const local = [];
      const localNeeds = new Set();
      const localConsts = [];
      let r;
      if (f.info.patch === "options") r = patchOptionsCall(sf, f.node, isJs, local, localNeeds, o);
      else if (f.info.patch === "langchain") r = patchLangChain(sf, f.node, "configuration", isJs, local, localNeeds, o);
      else if (f.info.patch === "langchain-anthropic") r = patchLangChain(sf, f.node, "clientOptions", isJs, local, localNeeds, o);
      else if (f.info.patch === "default-provider") r = patchDefaultProvider(sf, f, bindings, local, localNeeds, localConsts, o);
      else r = { error: "unknown patch kind" };
      if (r.error) {
        rec.reason = r.error;
        continue;
      }
      // reject edits overlapping ones already planned
      const overlaps = local.some((e) => edits.some((x) => e.start < x.end && x.start < e.end));
      if (overlaps) {
        rec.reason = "overlaps another patched site in the same expression";
        continue;
      }
      edits.push(...local);
      for (const n of localNeeds) needs.add(n);
      consts.push(...localConsts);
      rec.action = r.action;
      rec.ok = true;
    }
    result.edits.push(...fileResults);
    if (!edits.length) continue;

    const cjs = isJs && usesCjs(sf, abs);
    const plan = runtimePlan(abs, root, isJs, cjs ? "cjs" : "esm");
    const specifier = specifierFor(abs, plan);
    importEdits(sf, specifier, needs, consts, cjs, edits);
    const next = applyEdits(text, edits);
    if (next !== text) {
      fs.writeFileSync(abs, next);
      changed.add(rel);
    }
    const rtContent = runtimeText(runtimeSrc, plan);
    if (!runtimesWritten.has(plan.runtimeAbs)) {
      runtimesWritten.set(plan.runtimeAbs, rtContent);
      let cur = null;
      try {
        cur = fs.readFileSync(plan.runtimeAbs, "utf8");
      } catch {}
      if (cur !== rtContent) {
        fs.mkdirSync(path.dirname(plan.runtimeAbs), { recursive: true });
        fs.writeFileSync(plan.runtimeAbs, rtContent);
        changed.add(relOf(root, plan.runtimeAbs));
      }
    }
  }
  result.runtime_files = [...runtimesWritten.keys()].map((p) => relOf(root, p)).sort();
  result.files_changed = [...changed].sort();
  return result;
}

// ---------------------------------------------------------------------------------------------------------------
// CLI

function usage(code) {
  const msg = [
    "usage:",
    "  node router_migrate/ts/tool.mjs scan <root>",
    "  node router_migrate/ts/tool.mjs apply <root> --runtime <rendered routerRuntime.ts> --sites <json file: array of site ids or { id, opts }>",
  ].join("\n");
  (code ? process.stderr : process.stdout).write(msg + "\n");
  process.exit(code);
}

function main(argv) {
  const [cmd, ...rest] = argv;
  if (!cmd || cmd === "-h" || cmd === "--help") usage(cmd ? 0 : 2);
  const flags = {};
  const pos = [];
  for (let i = 0; i < rest.length; i++) {
    if (rest[i].startsWith("--")) flags[rest[i].slice(2)] = rest[++i];
    else pos.push(rest[i]);
  }
  if (cmd === "scan") {
    if (pos.length !== 1) usage(2);
    process.stdout.write(JSON.stringify(scan(pos[0]), null, 1) + "\n");
  } else if (cmd === "apply") {
    if (pos.length !== 1 || !flags.runtime || !flags.sites) usage(2);
    const ids = JSON.parse(fs.readFileSync(flags.sites, "utf8"));
    if (!Array.isArray(ids)) throw new Error("--sites must be a JSON array of site ids (or { id, opts } entries)");
    process.stdout.write(JSON.stringify(apply(pos[0], flags.runtime, ids), null, 1) + "\n");
  } else usage(2);
}

main(process.argv.slice(2));
