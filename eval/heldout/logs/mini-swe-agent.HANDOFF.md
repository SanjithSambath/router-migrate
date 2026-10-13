# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. src/minisweagent/models/portkey_model.py:92 (openai chat.completions) - handoff

`return self.client.chat.completions.create(`

- Why: client is constructed outside this repo or not resolvable (unresolved); pass it through router_runtime.wrap_openai()
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. src/minisweagent/models/portkey_response_model.py:75 (openai responses) - handoff

`return self.client.responses.create(`

- Why: client is constructed outside this repo or not resolvable (unresolved); pass it through router_runtime.wrap_openai()
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
