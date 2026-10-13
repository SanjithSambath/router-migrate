# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. app/api/container_files/content/route.ts:13 (openai files) - handoff

`? 'https://api.openai.com/v1/containers/${containerId}/files/${fileId}/content'`

- Why: raw HTTP to api.openai.com files: rewrite onto the OpenAI/Anthropic SDK (then the runtime applies) or call Router directly
- Done when: the request is sent with the OpenAI/Anthropic SDK through the runtime, or to Router directly with a User-Agent header
