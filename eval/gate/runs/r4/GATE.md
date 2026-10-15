# Release gate: open vs open_oldfacts, round r4

**HOLD: open vs open_oldfacts on r4 (W-T-L base 2-0-3, open 3-0-2; 5 valid pairs)**

Reasons:

- base rubric: 2 wins < 3 losses

Rule: SHIP if on both rubrics wins >= losses (ties count as neither), the candidate never fails the outside check where the baseline passes, and its verified-route count is never below the baseline's on a repo. Fewer than 3 valid pairs: INSUFFICIENT.

## Head-to-head, base rubric (W-T-L 2-0-3)

| repo | result | margin | why |
|---|---|---|---|
| sec-insights | loss | slight | Both moved the same two clients with the same diff and were honest about embeddings, but A drove the real entry point end to end and found that the product's only user-facing path (OpenAIAgent.astream_chat) silently drops every tool call, s |
| AI-Scientist | loss | clear | B moved more of the real traffic (the n=5 review ensemble ported instead of parked, DeepSeek unlocked and mapped) with every route verified by the outside check and 32/32 live requests clean, while A left the pipeline's largest prompt on Op |
| chatgpt-telegram-bot | loss | slight | Both moved the same four chat sites with the same two real fixes and were equally honest about the three endpoints Router can't serve, but A's system-level work is broader and more rigorously measured — it found the silent untranslatable-pa |
| llmcord | win | slight | Both moved the same single call site the same way with honest loss accounting and the same correct top lever (drop the frontier reasoning default), but B's work is more complete as a shipping unit: it caught that the Dockerfile copied only  |
| OpenManus | win | clear | A moved more real traffic (per-role spend labels landed in the migration itself, plus the one-line catalog-default change that is the difference between the shipped config routing everything and routing nothing — measured both ways), and sh |

## Head-to-head, open rubric (W-T-L 3-0-2)

| repo | result | margin | why |
|---|---|---|---|
| sec-insights | loss | slight | Both moved identical traffic with identical diffs and both correctly left embeddings on OpenAI, so the decision rests on system judgment — and A found that the product's only user-facing path (OpenAIAgent.astream_chat) silently drops every  |
| AI-Scientist | loss | slight | B moved the traffic that actually mattered and got it verified — the n=5 review ensemble ported off `n`, DeepSeek unlocked and mapped, every route confirmed (32/32 live) — while preserving behaviour where A gave it up, notably choosing clau |
| chatgpt-telegram-bot | win | slight | Both migrations are equivalent in substance — same four sites routed and verified, same two real breakages found and fixed (unrecorded function call, role-only opening chunk) — so the decision falls on system-level judgment, where B's is mo |
| llmcord | win | slight | Both moved the same single call identically and reached the same core conclusions (cheap default over gpt-5.5-at-high, cacheable prefix, surface failures, catalog-driven vision, client reuse, keep Flex unpinned with measurements), but A shi |
| OpenManus | win | clear | Both wrapped the same single AsyncOpenAI client and moved the same five call sites, but A's system-level work is deeper and verifiably correct: it labels spend per role inside the migration itself, then fixes the three things that decide wh |

## Outside check (candidate / baseline)

| repo | check ok | verified | unverified | not routing | tests |
|---|---|---|---|---|---|
| sec-insights | yes / yes | 3 / 2 | 0 / 1 | 0 / 0 | passed / passed |
| AI-Scientist | yes / yes | 7 / 7 | 6 / 0 | 0 / 0 | passed / passed |
| chatgpt-telegram-bot | yes / yes | 4 / 4 | 0 / 0 | 0 / 0 | passed / passed |
| llmcord | yes / yes | 1 / 1 | 0 / 0 | 0 / 0 | passed / passed |
| OpenManus | yes / yes | 5 / 5 | 0 / 0 | 0 / 0 | passed / passed |

