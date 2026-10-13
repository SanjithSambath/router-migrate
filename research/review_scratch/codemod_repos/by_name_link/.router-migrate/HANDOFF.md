# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. b/groq_agent.py:6 (openai chat.completions) - flagged

`return self.client.chat.completions.create(model='llama-3.3-70b', messages=[])`

- Why: model 'llama-3.3-70b' is not in the Router catalog (not in Router catalog); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only
