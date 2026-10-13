# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. gptcli/providers/anthropic.py:86 (anthropic messages.stream) - flagged

`with client.messages.stream(**kwargs) as completion:`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-2, claude-3, claude-3-7
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. gptcli/providers/anthropic.py:109 (anthropic messages) - flagged

`response = client.messages.create(**kwargs, stream=False)`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-2, claude-3, claude-3-7
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
