# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. src/utils/modelCache.ts:91 (openai models) - handoff

`url: 'https://api.openai.com/v1/models',`

- Why: raw HTTP to api.openai.com models: rewrite onto the OpenAI/Anthropic SDK (then the runtime applies) or call Router directly
- Done when: the request is sent with the OpenAI/Anthropic SDK through the runtime, or to Router directly with a User-Agent header

## 2. src/utils/modelCache.ts:132 (anthropic models) - handoff

`url: 'https://api.anthropic.com/v1/models',`

- Why: raw HTTP to api.anthropic.com models: rewrite onto the OpenAI/Anthropic SDK (then the runtime applies) or call Router directly
- Done when: the request is sent with the OpenAI/Anthropic SDK through the runtime, or to Router directly with a User-Agent header
