# Release gate: open vs ultra_levels, round r4

**SHIP: open vs ultra_levels on r4 (W-T-L base 5-0-0, open 5-0-0; 5 valid pairs)**

Rule: SHIP if on both rubrics wins >= losses (ties count as neither), the candidate never fails the outside check where the baseline passes, and its verified-route count is never below the baseline's on a repo. Fewer than 3 valid pairs: INSUFFICIENT.

## Head-to-head, base rubric (W-T-L 5-0-0)

| repo | result | margin | why |
|---|---|---|---|
| sec-insights | win | clear | Both shipped the identical two-client migration with equal verification, so the decision rests on the improvements: A found the app-specific waste an expert would attack — a one-tool inner agent that costs a decision call and a round trip p |
| AI-Scientist | win | clear | Both engineers moved the same client surface, hit the same two real blockers (seed, retired dated ids), and found the same pre-existing anthropic TypeError, but A's system-level work is deeper and more specific to this app: stage-grain spen |
| chatgpt-telegram-bot | win | clear | Both ran the same single-client wrap and found the same two Responses breaks (missing assistant function_call, role-only stream preamble), so the migration is a wash — the difference is the system-level pass, where B built more of the right |
| llmcord | win | clear | Both wrap the same single call site identically, but B found the gap that decides whether the migration is real in production — the Dockerfile copied only llmcord.py, so a built image would silently fall back to the no-op wrapper and route  |
| OpenManus | win | clear | Both wrapped the same single AsyncOpenAI client and verified all five call sites live, so the migration itself is a tie — but B carried it to the point where the shipped configuration actually routes (catalog default model, plus a fix for t |

## Head-to-head, open rubric (W-T-L 5-0-0)

| repo | result | margin | why |
|---|---|---|---|
| sec-insights | win | clear | Both moved identical traffic with the same two-client hook and both verified it live, so the decision rests on system-level judgment — and only A does the thing the brief asks for: it attacks calls rather than just configuring them (deletin |
| AI-Scientist | win | clear | Both moved essentially the same traffic with honest accounting of what stayed (OpenRouter/DeepSeek/Gemini/Bedrock/Vertex, aider's Claude path, the n=5 ensemble), but A's system-level work goes a layer deeper: real per-stage spend labels tha |
| chatgpt-telegram-bot | win | clear | Both moved the same four chat call sites and independently found and fixed the same two real breaks (missing assistant function_call item, role-only opening chunk), so the migrations are a wash; the difference is the system-level work. A ra |
| llmcord | win | clear | Both moved the one real call identically and measured the same big levers (cheap model, cacheable prefix, Flex-is-free) with live evidence, but B's system-level work is broader and mostly built rather than proposed: nine validated branches  |
| OpenManus | win | clear | Both wrapped the same single AsyncOpenAI client and verified all five call sites, but B actually closed the loop that decides whether anything routes at all — the shipped configs select claude-3-7-sonnet, which A itself reports leaves an un |

## Outside check (candidate / baseline)

| repo | check ok | verified | unverified | not routing | tests |
|---|---|---|---|---|---|
| sec-insights | yes / yes | 3 / 3 | 0 / 0 | 0 / 0 | passed / passed |
| AI-Scientist | yes / yes | 7 / 7 | 6 / 0 | 0 / 0 | passed / passed |
| chatgpt-telegram-bot | yes / yes | 4 / 4 | 0 / 0 | 0 / 0 | passed / passed |
| llmcord | yes / yes | 1 / 1 | 0 / 0 | 0 / 0 | passed / passed |
| OpenManus | yes / yes | 5 / 5 | 0 / 0 | 0 / 0 | passed / passed |

