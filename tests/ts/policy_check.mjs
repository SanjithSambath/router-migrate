// v2 compiled policy in the rendered routerRuntime.ts, checked against router-sim with the shared parity fixtures
// (tests/fixtures/policy/*.json), plus ROUTER_MIGRATE_POLICY=off, v1 byte-for-byte bodies and once-only logging.
// Local only: the "provider" is faked, Router is router-sim on 127.0.0.1.
// usage: node policy_check.mjs <rendered routerRuntime.ts> <fixtures dir>   (env: RAMP_ROUTER_API_KEY, RAMP_ROUTER_BASE_URL)
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [rtPath, fixDir] = process.argv.slice(2);
const rt = await import(pathToFileURL(rtPath).href);
const sim = process.env.RAMP_ROUTER_BASE_URL;
const lastReq = async () => (await (await fetch(sim + "/__last")).json());
const urlOf = (input) => (typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url);
const json = (o) => new Response(JSON.stringify(o), { status: 200, headers: { "content-type": "application/json" } });

// Router traffic goes to router-sim (raw bodies recorded); provider traffic is faked and counted.
const routerBodies = [];
const providerCalls = [];
const base = async (input, init) => {
  const url = urlOf(input);
  if (url.startsWith(sim)) {
    routerBodies.push(init.body);
    return fetch(input, init);
  }
  providerCalls.push(url);
  return json({ id: "p", object: "chat.completion", created: 0, model: "provider", choices: [{ index: 0, message: { role: "assistant", content: "from-provider" }, finish_reason: "stop" }] });
};
const rf = rt.routerFetch(base);

const LOGS = [];
const realWarn = console.warn;
console.warn = (...a) => LOGS.push(a.map(String).join(" "));
const resetLogs = () => {
  LOGS.length = 0;
  const m = globalThis[Symbol.for("router-migrate.policyLogs")];
  if (m instanceof Map) m.clear();
};

function setPolicy(policy, hash = "0123456789abcdef") {
  if (policy == null) delete globalThis.__ROUTER_MIGRATE_POLICY__;
  else globalThis.__ROUTER_MIGRATE_POLICY__ = { policy: structuredClone(policy), hash };
}

async function withEnv(vars, fn) {
  const old = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
  try { return await fn(); } finally { for (const [k, v] of Object.entries(old)) if (v === undefined) delete process.env[k]; else process.env[k] = v; }
}

/** Sends an SDK-level body through the runtime's fetch path; returns [router-sim record, raw Router body string]. */
async function send(requestPath, request) {
  const anthropic = requestPath.startsWith("/v1/messages");
  const url = (anthropic ? "https://api.anthropic.com" : "https://api.openai.com") + requestPath;
  const headers = anthropic
    ? { "content-type": "application/json", "x-api-key": "sk-ant-provider", "anthropic-version": "2023-06-01" }
    : { "content-type": "application/json", authorization: "Bearer sk-provider" };
  const nBodies = routerBodies.length;
  providerCalls.length = 0;
  const r = await rf(url, { method: "POST", headers, body: JSON.stringify(request) });
  await r.text();
  assert.equal(providerCalls.length, 0, "request must not go to the provider");
  assert.equal(routerBodies.length, nBodies + 1, "exactly one Router request");
  return [await lastReq(), routerBodies[routerBodies.length - 1]];
}

/** Recursive subset match: every key in want is in got with an equal value (objects recurse; arrays/scalars compare exactly). */
function subset(got, want, where = "body") {
  for (const [k, v] of Object.entries(want)) {
    assert.ok(got && typeof got === "object" && k in got, `${where}.${k} missing`);
    if (v && typeof v === "object" && !Array.isArray(v)) subset(got[k], v, `${where}.${k}`);
    else assert.deepEqual(got[k], v, `${where}.${k}`);
  }
}

const results = [];
async function t(name, fn) {
  try { await fn(); results.push(["ok", name]); } catch (e) { results.push(["FAIL", name, String((e && e.stack) || e)]); }
}

const fixtures = fs.readdirSync(fixDir).filter((f) => f.endsWith(".json")).sort()
  .map((f) => JSON.parse(fs.readFileSync(path.join(fixDir, f), "utf8")));

// ------------------------------------------------------------------ shared parity fixtures
for (const fx of fixtures) {
  await t(`fixture ${fx.name}`, async () => {
    setPolicy(fx.policy);
    try {
      const [rec] = await send(fx.request_path, fx.request);
      assert.equal(rec.path, fx.expect.router_path);
      assert.ok(rec.valid, `router-sim rejected the body: ${rec.error}`);
      subset(rec.request, fx.expect.body_has);
      for (const k of fx.expect.body_lacks) assert.ok(!(k in rec.request), `body has ${k}`);
    } finally {
      setPolicy(null);
    }
  });
}

// ------------------------------------------------------------------ v1 byte for byte without (or with a disabled / invalid) policy
await t("no policy / ROUTER_MIGRATE_POLICY=off / invalid policy => identical v1 bodies", async () => {
  const invalid = { version: 1, defaults: { model: "gpt-5-nano" } };
  for (const fx of fixtures) {
    setPolicy(null);
    const [, v1] = await send(fx.request_path, fx.request);
    if (fx.policy) {
      setPolicy(fx.policy);
      const [, off] = await withEnv({ ROUTER_MIGRATE_POLICY: "off" }, () => send(fx.request_path, fx.request));
      assert.equal(off, v1, `${fx.name}: ROUTER_MIGRATE_POLICY=off changed the body`);
      const [, offUpper] = await withEnv({ ROUTER_MIGRATE_POLICY: " OFF " }, () => send(fx.request_path, fx.request));
      assert.equal(offUpper, v1, `${fx.name}: ROUTER_MIGRATE_POLICY=OFF changed the body`);
    }
    setPolicy(invalid);
    const [, inv] = await send(fx.request_path, fx.request);
    assert.equal(inv, v1, `${fx.name}: an invalid policy changed the body`);
    setPolicy(null);
  }
});

await t("ROUTER_MIGRATE_POLICY=off disables an applicable policy", async () => {
  const fx = fixtures.find((f) => f.name === "flex_on_openai_drops_service_tier_default");
  assert.ok(fx, "fixture present");
  setPolicy(fx.policy);
  try {
    const [rec] = await withEnv({ ROUTER_MIGRATE_POLICY: "off" }, () => send(fx.request_path, fx.request));
    assert.ok(rec.valid);
    assert.equal(rec.request.service_tier, "default");
    assert.ok(!("allow_flex_tier" in rec.request));
    const [on] = await send(fx.request_path, fx.request); // same process, env back on: policy applies again
    assert.equal(on.request.allow_flex_tier, true);
  } finally {
    setPolicy(null);
  }
});

await t("invalid policies are rejected whole", async () => {
  const req = { model: "gpt-5-nano", messages: [{ role: "user", content: "hi" }], max_tokens: 32, metadata: { feature: "src/app.py:ask" } };
  const ok = { allow_flex_tier: true };
  const bad = [
    { version: 2, defaults: ok }, { defaults: ok }, { version: 1, defaults: ok, extra: {} }, { version: "1", defaults: ok },
    { version: 1, defaults: ok, features: { "src/app.py:ask": { temperature: 0 } } },
    { version: 1, defaults: ok, globs: { "src/*": { metadata: { team: 1 } } } },
    { version: 1, defaults: ok, features: { x: { fallbacks: [] } } },
    { version: 1, defaults: ok, features: { x: { fallbacks: Array(15).fill("gpt-4.1-mini") } } },
    { version: 1, defaults: { ...ok, provider_timeout: 0 } }, { version: 1, defaults: { ...ok, provider_timeout: true } },
    { version: 1, defaults: { ...ok, prompt_cache_key: 5 } }, { version: 1, defaults: { allow_flex_tier: "yes" } },
    { version: 1, defaults: ok, features: [] }, { version: 1, defaults: null },
  ];
  for (const p of bad) {
    setPolicy(p);
    const [rec] = await send("/v1/chat/completions", req);
    assert.ok(!("allow_flex_tier" in rec.request), `accepted invalid policy ${JSON.stringify(p)}`);
    assert.equal(rec.request.service_tier, "default");
    assert.equal(rt.routerPolicyFor("src/app.py:ask"), null);
  }
  setPolicy({ version: 1, defaults: ok });
  assert.equal((await send("/v1/chat/completions", req))[0].request.allow_flex_tier, true, "control: the valid policy applies");
  setPolicy({ policy: "not an object" }.policy); // a non-object global
  assert.ok(!("allow_flex_tier" in (await send("/v1/chat/completions", req))[0].request));
  setPolicy(null);
});

// ------------------------------------------------------------------ feature id, globs, metadata cap, fallbacks + caller tier
await t("feature id derived from the call stack when the caller sets none", async () => {
  setPolicy({ version: 1, globs: { "tests/ts/policy_check.mjs:*": { provider_timeout: 77 }, "tests/*:*": { provider_timeout: 11 } } });
  try {
    const [rec] = await send("/v1/chat/completions", { model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }] });
    assert.ok(rec.valid, rec.error);
    assert.match(rec.request.metadata.feature, /^tests\/ts\/policy_check\.mjs:/);
    assert.equal(rec.request.provider_timeout, 77, "longest matching glob wins; `*` does not cross '/'");
  } finally {
    setPolicy(null);
  }
});

await t("glob semantics", async () => {
  const g = (p, s) => rt.globToRegExp(p).test(s);
  assert.ok(g("src/*", "src/app.py:ask")); assert.ok(!g("src/*", "src/a/app.py:ask"));
  assert.ok(g("src/**", "src/a/b/app.py:ask")); assert.ok(g("**:ask", "src/a/app.py:ask"));
  assert.ok(g("src/app.?y:*", "src/app.py:f")); assert.ok(g("src/[ab]pp.py:*", "src/app.py:f")); assert.ok(!g("src/[!ab]pp.py:*", "src/app.py:f"));
  assert.ok(g("a.b(c)+", "a.b(c)+")); assert.ok(!g("a.b", "axb"));
});

await t("routerPolicyFor: precedence and metadata merge", async () => {
  setPolicy(fixtures.find((f) => f.name === "precedence_feature_over_glob_over_defaults").policy);
  try {
    const p = rt.routerPolicyFor("src/app.py:ask");
    assert.deepEqual(p.settings, { provider_timeout: 50, metadata: { a: "d", b: "g", c: "f" } });
    assert.equal(p.hash, "0123456789abcdef");
    assert.deepEqual(rt.routerPolicyFor("lib/x.py:f").settings, { provider_timeout: 30, metadata: { a: "d", b: "d" } });
    assert.deepEqual(rt.routerPolicyFor().settings, { provider_timeout: 30, metadata: { a: "d", b: "d" } });
  } finally {
    setPolicy(null);
  }
});

await t("metadata 16-key cap drops policy keys first (least specific first)", async () => {
  const reqMeta = Object.fromEntries(Array.from({ length: 13 }, (_, i) => [`k${i}`, "r"]));
  reqMeta.feature = "src/app.py:ask"; // 14 request keys
  setPolicy({ version: 1, defaults: { metadata: { d1: "d", d2: "d", k0: "policy" } }, features: { "src/app.py:ask": { metadata: { f1: "f" } } } });
  try {
    const [rec] = await send("/v1/chat/completions", { model: "gpt-4o-mini", messages: [{ role: "user", content: "hi" }], metadata: reqMeta });
    assert.ok(rec.valid, rec.error);
    const m = rec.request.metadata;
    assert.equal(Object.keys(m).length, 16);
    assert.equal(m.k0, "r", "request keys win");
    assert.equal(m.f1, "f"); assert.equal(m.d1, "d"); assert.ok(!("d2" in m));
  } finally {
    setPolicy(null);
  }
});

await t("fallbacks keep a caller service_tier; image input needs image-capable candidates", async () => {
  setPolicy({ version: 1, features: { "src/app.py:ask": { fallbacks: ["gpt-4", "claude-haiku-4-5"], allow_flex_tier: true } } });
  try {
    const content = [{ type: "text", text: "what is this" }, { type: "image_url", image_url: { url: "data:image/png;base64,iVBORw0KGgo=" } }];
    const [rec] = await send("/v1/chat/completions", { model: "gpt-5-nano", messages: [{ role: "user", content }], service_tier: "priority", metadata: { feature: "src/app.py:ask" } });
    assert.ok(rec.valid, rec.error);
    assert.deepEqual(rec.request.models, ["gpt-5-nano", "claude-haiku-4-5"]);
    assert.equal(rec.request.service_tier, "priority");
    assert.ok(!("model" in rec.request) && !("allow_flex_tier" in rec.request));
  } finally {
    setPolicy(null);
  }
});

await t("fallbacks restore v1's service_tier default when flex is dropped", async () => {
  setPolicy({ version: 1, defaults: { allow_flex_tier: true, fallbacks: ["gpt-5-mini"] } });
  try {
    const [rec] = await send("/v1/responses", { model: "gpt-5-nano", input: "hi" });
    assert.ok(rec.valid, rec.error);
    assert.deepEqual(rec.request.models, ["gpt-5-nano", "gpt-5-mini"]);
    assert.equal(rec.request.service_tier, "default");
    assert.ok(!("allow_flex_tier" in rec.request));
  } finally {
    setPolicy(null);
  }
});

await t("caller prompt_cache_key / provider_timeout are kept", async () => {
  setPolicy({ version: 1, defaults: { prompt_cache_key: "pol-$feature", provider_timeout: 30 } });
  try {
    let [rec] = await send("/v1/responses", { model: "gpt-4o-mini", input: "hi", prompt_cache_key: "mine", provider_timeout: 5, metadata: { feature: "f" } });
    assert.equal(rec.request.prompt_cache_key, "mine"); assert.equal(rec.request.provider_timeout, 5);
    [rec] = await send("/v1/responses", { model: "gpt-4o-mini", input: "hi", metadata: { feature: "f" } });
    assert.equal(rec.request.prompt_cache_key, "pol-f"); assert.equal(rec.request.provider_timeout, 30);
  } finally {
    setPolicy(null);
  }
});

// ------------------------------------------------------------------ logging: hash once, each guard skip once per (feature, key), no content
await t("logs: hash once, skips once per (feature, key), /v1/messages once, no request content", async () => {
  resetLogs();
  setPolicy({ version: 1, features: { "src/a.py:f": { allow_flex_tier: true }, "src/b.py:g": { allow_flex_tier: true } }, defaults: { metadata: { team: "t" } } }, "feedfacecafebeef00");
  try {
    const SECRET = "SECRET-PROMPT-8c1f";
    const chat = (feature) => ({ model: "claude-haiku-4-5", messages: [{ role: "user", content: SECRET }], max_tokens: 32, metadata: { feature } });
    for (let i = 0; i < 3; i++) await send("/v1/chat/completions", chat("src/a.py:f"));
    await send("/v1/chat/completions", chat("src/b.py:g"));
    await send("/v1/chat/completions", chat("src/b.py:g"));
    for (let i = 0; i < 2; i++) await send("/v1/messages", { model: "claude-haiku-4-5", max_tokens: 32, messages: [{ role: "user", content: SECRET }] });
    const count = (re) => LOGS.filter((l) => re.test(l)).length;
    assert.equal(count(/^router-migrate policy feedface$/), 1, JSON.stringify(LOGS));
    assert.equal(count(/allow_flex_tier skipped for src\/a\.py:f/), 1, JSON.stringify(LOGS));
    assert.equal(count(/allow_flex_tier skipped for src\/b\.py:g/), 1, JSON.stringify(LOGS));
    assert.equal(count(/not applied on \/v1\/messages/), 1, JSON.stringify(LOGS));
    assert.equal(LOGS.length, 4, JSON.stringify(LOGS));
    assert.ok(!LOGS.some((l) => l.includes(SECRET)), "logs must not carry request content");
    resetLogs();
    setPolicy({ version: 1, defaults: { model: "x" } }, "badbadbadbad");
    for (let i = 0; i < 2; i++) await send("/v1/chat/completions", chat("src/a.py:f"));
    assert.deepEqual(LOGS, ["router-migrate policy badbadba invalid: ignored (v1 behaviour)"]);
    resetLogs();
    setPolicy(null);
    await send("/v1/chat/completions", chat("src/a.py:f"));
    await send("/v1/messages", { model: "claude-haiku-4-5", max_tokens: 32, messages: [{ role: "user", content: "hi" }] });
    assert.deepEqual(LOGS, [], "no policy => no policy logs");
  } finally {
    setPolicy(null);
    resetLogs();
  }
});

console.warn = realWarn;
console.log(JSON.stringify(results));
process.exit(results.some((r) => r[0] !== "ok") ? 1 : 0);
