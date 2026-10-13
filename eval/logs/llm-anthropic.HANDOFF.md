# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. llm_anthropic.py:1799 (anthropic messages.beta) - flagged

`return client.beta.messages.count_tokens(**count_kwargs)`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
