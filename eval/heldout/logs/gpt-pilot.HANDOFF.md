# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. core/llm/anthropic_client.py:93 (anthropic messages.stream) - flagged

`async with self.client.messages.stream(**completion_kwargs) as stream:`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-5-sonnet-20240620, claude-3-5-sonnet-20241022, claude-3-haiku-20240307
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. core/templates/tree/node_express_mongoose/services/llm.js:44 (anthropic messages) - flagged

`const response = await anthropic.messages.create({`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-5-sonnet-20240620, claude-3-5-sonnet-20241022, claude-3-haiku-20240307
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. core/templates/tree/vite_react/server/services/llmService.js:44 (anthropic messages) - flagged

`const response = await anthropic.messages.create({`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-5-sonnet-20240620, claude-3-5-sonnet-20241022, claude-3-haiku-20240307
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
