# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. app/langgraph/agent/agent.ts:18 (langchain-openai framework.chat) - flagged

`const message = await llm.invoke([`

- Why: client not resolved statically; 8 of 11 langchain-openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
