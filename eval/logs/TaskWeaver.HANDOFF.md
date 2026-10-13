# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. taskweaver/llm/anthropic.py:80 (anthropic messages.stream) - flagged

`with self.client.messages.stream(**anthropic_kwargs) as stream:`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-opus-20240229
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. taskweaver/llm/anthropic.py:85 (anthropic messages) - flagged

`response = self.client.messages.create(**anthropic_kwargs)`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-opus-20240229
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
