# Round r5: each arm vs open, head-to-head (blind, order randomised)

| arm | repo | result | margin | why |
|---|---|---|---|---|
| open_nolevers | chatgpt-telegram-bot | loss | clear | A's migration actually preserves behaviour in the app's default configuration: it found, instrumented (chunk dumps) and fixed the streamed function-call detection that bails at the role-only chunk Router's Responses stream opens with — B ships that path untouched, so with STREAM=true and plugins ena |
| open_nopolicy | AI-Scientist | loss | clear | Both moved essentially the same traffic and were honest about what stayed, but B's system-level judgment is the one a CTO can act on: it priced the stages from real prompts, found that aider's two coding loops dominate the bill, and built the --coder-model change that actually gets that stage onto R |
| open_nopolicy | OpenManus | loss | slight | Both moved the same five call sites behind the one AsyncOpenAI client with the same honest list of what stays (Bedrock, Azure, the VMind subprocess), but A shipped more of Router's actual surface in the migration itself — spend label, per-call-site prompt cache key, provider_timeout, model map, then |
| open_nopolicy | chatgpt-telegram-bot | loss | clear | A moved the same four calls but caught a second real break B shipped past — Router's streams open with a role-only chunk, so the old detection loop silently discarded every streamed plugin call in the default STREAM=true configuration — and A is the only one who actually reached Router-only surfaces |

| arm | wins | ties | losses | net margin (slight 1, clear 2, large 3) |
|---|---|---|---|---|
| open_noadapt | 0 | 0 | 0 | +0 |
| open_nolevers | 0 | 0 | 1 | -2 |
| open_nopolicy | 0 | 0 | 3 | -5 |
