# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. ce3.py:338 (anthropic messages) - flagged

`response = self.client.messages.create(`

- Why: model chosen at runtime and none of the repo's anthropic model names are in Router's catalog: claude-3-5-sonnet-20241022
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. Claude-Eng-v2/main.py:664 (anthropic messages) - flagged

`response = client.beta.prompt_caching.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only

## 3. Claude-Eng-v2/main.py:1227 (anthropic messages) - flagged

`response = client.beta.prompt_caching.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only

## 4. Claude-Eng-v2/main.py:1503 (anthropic messages) - flagged

`response = client.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only

## 5. Claude-Eng-v2/main.py:1755 (anthropic messages) - flagged

`response = client.beta.prompt_caching.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only

## 6. Claude-Eng-v2/main.py:1886 (anthropic messages) - flagged

`tool_response = client.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only

## 7. tools/toolcreator.py:90 (anthropic messages) - flagged

`response = self.client.messages.create(`

- Why: model 'claude-3-5-sonnet-20241022' is not in the Router catalog (dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)); the runtime leaves it on the provider. Map it explicitly with ROUTER_MIGRATE_MODEL_MAP if a catalog model is an acceptable substitute
- Done when: either a catalog model is chosen deliberately (ROUTER_MIGRATE_MODEL_MAP or code change) or the site is documented as provider-only
