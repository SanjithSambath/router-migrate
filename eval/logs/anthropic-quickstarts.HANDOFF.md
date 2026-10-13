# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. agents/agent.py:113 (anthropic messages) - flagged

`response = self.client.messages.create(`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. browser-use-demo/browser_use_demo/loop.py:137 (anthropic messages.beta) - flagged

`response = client.beta.messages.create(**api_kwargs)`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. browser-use-demo/browser_use_demo/loop.py:140 (anthropic messages) - flagged

`response = client.messages.create(**api_kwargs)`

- Why: per-tenant provider credentials: all tenants would share one Router key (billing/isolation change) [api_key from function parameter 'api_key']
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. computer-use-demo/computer_use_demo/loop.py:168 (anthropic messages.beta) - flagged

`raw_response = client.beta.messages.with_raw_response.create(`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 5. browser-toolset/typescript/run.ts:65 (anthropic messages.beta) - flagged

`const runner = client.beta.messages.toolRunner({`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 6. computer-toolset/typescript/run.ts:53 (anthropic messages.beta) - flagged

`const runner = client.beta.messages.toolRunner({`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
