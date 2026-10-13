# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. src/khoj/processor/conversation/anthropic/utils.py:111 (anthropic messages.beta) - flagged

`with client.beta.messages.stream(`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. src/khoj/processor/conversation/openai/utils.py:171 (openai chat.completions.stream) - flagged

`with client.beta.chat.completions.stream(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. src/khoj/processor/conversation/openai/utils.py:215 (openai chat.completions.parse) - flagged

`chunk = client.beta.chat.completions.parse(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. src/khoj/processor/conversation/openai/utils.py:371 (openai chat.completions) - flagged

`response: openai.AsyncStream[ChatCompletionChunk] | ChatCompletion = await client.chat.completions.create(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 5. src/khoj/processor/conversation/openai/utils.py:500 (openai responses) - flagged

`model_response: OpenAIResponse = client.responses.create(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 6. src/khoj/processor/conversation/openai/utils.py:619 (openai responses) - flagged

`async with client.responses.stream(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 7. src/khoj/processor/operator/grounding_agent.py:52 (openai chat.completions) - flagged

`grounding_response: ChatCompletion = await self.client.chat.completions.create(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 8. src/khoj/processor/operator/grounding_agent_uitars.py:148 (openai chat.completions) - flagged

`response: ChatCompletion = await self.client.chat.completions.create(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 9. src/khoj/processor/operator/operator_agent_anthropic.py:431 (anthropic messages.beta) - flagged

`response = await client.beta.messages.create(`

- Why: Anthropic beta header: the runtime keeps these calls on Anthropic
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 10. src/khoj/processor/operator/operator_agent_openai.py:218 (openai responses) - flagged

`response: Response = await client.responses.create(`

- Why: client not resolved statically; 4 of 8 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
