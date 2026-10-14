# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. app/api/chat/anthropic/route.ts:63 (anthropic messages) - flagged

`const response = await anthropic.messages.create({`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from profile.anthropic_api_key]
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. app/api/chat/openai/route.ts:27 (openai chat.completions) - flagged

`const response = await openai.chat.completions.create({`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from profile.openai_api_key]
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. app/api/chat/tools/route.ts:62 (openai chat.completions) - flagged

`const firstResponse = await openai.chat.completions.create({`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from profile.openai_api_key]
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. app/api/chat/tools/route.ts:201 (openai chat.completions) - flagged

`const secondResponse = await openai.chat.completions.create({`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from profile.openai_api_key]
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 5. app/api/command/route.ts:23 (openai chat.completions) - flagged

`const response = await openai.chat.completions.create({`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from profile.openai_api_key]
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
