# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. svc/llm.py:10 (openai chat.completions) - flagged

`return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role':'user','content':q}])`

- Why: client construction left unchanged: already patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. svc/llm.py:16 (anthropic messages) - flagged

`return claude.messages.create(model='claude-haiku-4-5', max_tokens=10, messages=[{'role':'user','content':q}])`

- Why: client construction left unchanged: already patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
