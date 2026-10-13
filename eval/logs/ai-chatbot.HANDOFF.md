# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. app/(chat)/actions.ts:28 (vercel-ai framework.chat) - handoff

`const { text } = await generateText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. app/(chat)/api/chat/route.ts:269 (vercel-ai framework.chat) - handoff

`const result = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. artifacts/code/server.ts:18 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. artifacts/code/server.ts:40 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 5. artifacts/sheet/server.ts:11 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 6. artifacts/sheet/server.ts:33 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 7. artifacts/text/server.ts:11 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 8. artifacts/text/server.ts:35 (vercel-ai framework.chat) - handoff

`const { stream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 9. lib/ai/tools/request-suggestions.ts:42 (vercel-ai framework.chat) - handoff

`const { partialOutputStream } = streamText({`

- Why: Vercel AI Gateway is a different gateway; switching to createOpenAI({fetch: routerFetch()}) is a product decision
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
