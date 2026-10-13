# HANDOFF: tasks for a coding agent

router-migrate moved what it could deterministically. Each task below needs judgment. Work through them in order,
then run `router-migrate verify .` until it is green. Specs: `.router-migrate/handoff.json`.

Rules (measured on the live Router API): only /v1/responses, /v1/messages and GET /v1/models exist; use undated catalog
model ids; no store/previous_response_id; stop is 501 on OpenAI models; Router keys come from RAMP_ROUTER_API_KEY.

## 1. app/api/chat/azure/route.ts:54 (openai chat.completions) - flagged

`const response = await azureOpenai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 2. app/api/chat/custom/route.ts:40 (openai chat.completions) - flagged

`const response = await custom.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 3. app/api/chat/groq/route.ts:26 (openai chat.completions) - flagged

`const response = await groq.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 4. app/api/chat/mistral/route.ts:27 (openai chat.completions) - flagged

`const response = await mistral.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 5. app/api/chat/openai/route.ts:27 (openai chat.completions) - flagged

`const response = await openai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 6. app/api/chat/openrouter/route.ts:27 (openai chat.completions) - flagged

`const response = await openai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 7. app/api/chat/perplexity/route.ts:26 (openai chat.completions) - flagged

`const response = await perplexity.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 8. app/api/chat/tools/route.ts:62 (openai chat.completions) - flagged

`const firstResponse = await openai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 9. app/api/chat/tools/route.ts:201 (openai chat.completions) - flagged

`const secondResponse = await openai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report

## 10. app/api/command/route.ts:23 (openai chat.completions) - flagged

`const response = await openai.chat.completions.create({`

- Why: client not resolved statically; 12 of 16 openai clients in the repo are patched
- Done when: the site routes to Router under router-sim (`router-migrate verify`), or the reason it must stay is written in the report
