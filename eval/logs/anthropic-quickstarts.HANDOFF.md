# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. browser-use-demo/browser_use_demo/loop.py:137 (anthropic messages.beta) - flagged

`response = client.beta.messages.create(**api_kwargs)`

- Why: anthropic beta API (anthropic-beta header) is unmeasured on Router; the runtime keeps it on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. computer-use-demo/computer_use_demo/loop.py:168 (anthropic messages.beta) - flagged

`raw_response = client.beta.messages.with_raw_response.create(`

- Why: anthropic beta API (anthropic-beta header) is unmeasured on Router; the runtime keeps it on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. browser-toolset/typescript/run.ts:65 (anthropic messages.beta) - flagged

`const runner = client.beta.messages.toolRunner({`

- Why: anthropic beta API (anthropic-beta header) is unmeasured on Router; the runtime keeps it on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. computer-toolset/typescript/run.ts:53 (anthropic messages.beta) - flagged

`const runner = client.beta.messages.toolRunner({`

- Why: anthropic beta API (anthropic-beta header) is unmeasured on Router; the runtime keeps it on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
