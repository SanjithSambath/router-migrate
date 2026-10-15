# Release gate: open vs v6, round r6

**SHIP: open vs v6 on r6 (W-T-L base 5-0-0, open 5-0-0; 5 valid pairs)**

Rule: SHIP if on both rubrics wins >= losses (ties count as neither), the candidate never fails the outside check where the baseline passes, and its verified-route count is never below the baseline's on a repo. Fewer than 3 valid pairs: INSUFFICIENT.

## Head-to-head, base rubric (W-T-L 5-0-0)

| repo | result | margin | why |
|---|---|---|---|
| computer_use_ootb | win | clear | Both moved the same single routable call site (the OpenAI planner) with the same dated-snapshot model_map and the same honest 'stays' reasoning, so the migration is a near tie — the decision is the system-level work on top. B's is deeper an |
| obsidian-copilot | win | large | A moved every OpenAI/Anthropic-served call including the ChatOpenRouter inner client (which B's own validator flagged as unwrapped), verified it with 116 live Router requests, and then did real system work: four built-and-tested branches wh |
| obsidian-textgenerator-plugin | win | clear | Both engineers found the same single chokepoint (ProxyService.getFetch) and wrapped it cleanly with equal behaviour-preservation discipline, but B went a layer deeper into what actually reaches Router: it caught that @langchain/anthropic re |
| openui | win | clear | Both moved the same single piece of real traffic (the one shared OpenAI client behind /v1/chat/completions) with the same care about what must stay — Groq, the LiteLLM sidecar, Ollama, the model probes, and the browser client that points at |
| page-assist | win | clear | A moved more real traffic and verified it harder (29-site inventory, 55/55 live requests across labelled features, plus Router added as a user-selectable provider — the only activation path that actually works in a keyless browser extension |

## Head-to-head, open rubric (W-T-L 5-0-0)

| repo | result | margin | why |
|---|---|---|---|
| computer_use_ootb | win | large | Both moved the same single routable call the same way with equivalent verification, so the decision rests on system-level judgment, and there B is far deeper: measured per-turn cost across six catalog vision models (a 20x spread), proved gp |
| obsidian-copilot | win | large | Both engineers found the single chokepoint (ChatModelManager) and moved the same OpenAI/Anthropic traffic with behaviour intact, so the migrations are near-equivalent — but A also wrapped ChatOpenRouter's inner client, which B's own validat |
| obsidian-textgenerator-plugin | win | large | A moved more traffic and proved it (37 live requests, a real bypass fixed in ChatAnthropic's clientOptions, and the presence_penalty field that was silently keeping every Custom-provider request off Router), then did the CTO work the bar as |
| openui | win | large | A moved the same single production call as B but then did the CTO job: it measured Router's levers live (tier, fallbacks, prompt_cache_key paying +46% on one model and -24% on another, reasoning effort, per-surface spend labels), found thre |
| page-assist | win | large | Both moved the same two code paths with comparable care and honesty, but B's system-level work is on a different level: it found the largest cost item in the product (page/document context pinned to the last message, which defeats prompt ca |

## Outside check (candidate / baseline)

| repo | check ok | verified | unverified | not routing | tests |
|---|---|---|---|---|---|
| computer_use_ootb | yes / yes | 1 / 1 | 0 / 0 | 0 / 0 | passed / passed |
| obsidian-copilot | yes / yes | 5 / 1 | 7 / 9 | 0 / 0 | running / passed |
| obsidian-textgenerator-plugin | yes / yes | 5 / 1 | 7 / 0 | 3 / 3 | passed / passed |
| openui | yes / yes | 1 / 1 | 0 / 0 | 0 / 0 | passed / passed |
| page-assist | yes / yes | 6 / 1 | 15 / 0 | 1 / 1 | passed / passed |

