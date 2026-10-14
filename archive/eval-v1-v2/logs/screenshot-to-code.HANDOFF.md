# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. backend/agent/providers/openai.py:544 (openai responses) - flagged

`stream = await self._client.responses.create(**params)  # type: ignore`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from function parameter 'openai_api_key']
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. backend/agent/providers/anthropic/provider.py:400 (anthropic messages.stream) - flagged

`async with self._client.messages.stream(**stream_kwargs) as stream:`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from function parameter 'anthropic_api_key']
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
