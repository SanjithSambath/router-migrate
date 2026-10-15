# Round r4: each arm vs ultra_levels, head-to-head (blind, order randomised)

| arm | repo | result | margin | why |
|---|---|---|---|---|
| open | AI-Scientist | win | clear | Both moved essentially the same traffic with honest accounting of what stayed (OpenRouter/DeepSeek/Gemini/Bedrock/Vertex, aider's Claude path, the n=5 ensemble), but A's system-level work goes a layer deeper: real per-stage spend labels that make 'what did this paper cost, by stage' answerable and g |
| open | OpenManus | win | clear | Both wrapped the same single AsyncOpenAI client and verified all five call sites, but B actually closed the loop that decides whether anything routes at all — the shipped configs select claude-3-7-sonnet, which A itself reports leaves an unmodified deployment sending zero requests to Router and only |
| open | chatgpt-telegram-bot | win | clear | Both moved the same four chat call sites and independently found and fixed the same two real breaks (missing assistant function_call item, role-only opening chunk), so the migrations are a wash; the difference is the system-level work. A ranks by measured money and latency across five models and fiv |
| open | llmcord | win | clear | Both moved the one real call identically and measured the same big levers (cheap model, cacheable prefix, Flex-is-free) with live evidence, but B's system-level work is broader and mostly built rather than proposed: nine validated branches covering the silent-failure path, the multiplicative prompt  |
| open | sec-insights | win | clear | Both moved identical traffic with the same two-client hook and both verified it live, so the decision rests on system-level judgment — and only A does the thing the brief asks for: it attacks calls rather than just configuring them (deleting the one-tool inner financials agent, skipping sourceless q |
| ultra_facts2 | AI-Scientist | win | slight | Both move essentially the same traffic with the same two policy unlocks (drop seed, map retired snapshots), and both verify live, so the decision turns on the system-level judgment and on honesty about what did not move. B reads this product correctly — aider is the majority of the spend and is genu |
| ultra_facts2 | OpenManus | loss | slight | B actually moved the deployment OpenManus ships (the OpenAI SDK pointed at api.anthropic.com), verified it live on that host, and priced the trade-off honestly, whereas A's wrap routes nothing for the shipped config and defers the unlock to a proposal; B also caught the one defect that would have br |
| ultra_facts2 | chatgpt-telegram-bot | loss | clear | Both made the same correct one-line wrap and the same function_call-history fix, but B also found and fixed a second real break A shipped past — Router's translated stream emits the role chunk alone, so with the default STREAM=true a plugin turn returned an empty reply and never ran the plugin; A's  |
| ultra_facts2 | llmcord | win | clear | A ships the same wrap but catches the production trap that makes B's migration a no-op in the built image (the Dockerfile copies only llmcord.py and config.yaml, so router_runtime.py would be absent and the import guard would silently degrade), and is far more honest about what translation costs (ex |
| ultra_facts2 | sec-insights | win | slight | Both moved the same two client constructions with the same reversibility, but B's judgment reaches the bar the task sets: it caught a real user-facing break (the translated stream's leading content "" chunk making OpenAIAgentWorker latch is_function=False, so every chat turn returned empty and skipp |

| arm | wins | ties | losses | net margin (slight 1, clear 2, large 3) |
|---|---|---|---|---|
| open | 5 | 0 | 0 | +10 |
| open_oldfacts | 0 | 0 | 0 | +0 |
| ultra_facts2 | 3 | 0 | 2 | +1 |
