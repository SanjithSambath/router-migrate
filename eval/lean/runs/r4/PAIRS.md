# Round r4: each arm vs ultra_levels, head-to-head (blind, order randomised)

| arm | repo | result | margin | why |
|---|---|---|---|---|
| open | AI-Scientist | win | clear | Both engineers moved the same client surface, hit the same two real blockers (seed, retired dated ids), and found the same pre-existing anthropic TypeError, but A's system-level work is deeper and more specific to this app: stage-grain spend labels that un-collapse the single dispatcher (B's labels  |
| open | OpenManus | win | clear | Both wrapped the same single AsyncOpenAI client and verified all five call sites live, so the migration itself is a tie — but B carried it to the point where the shipped configuration actually routes (catalog default model, plus a fix for the pydantic default_factory bug that made `[llm.<name>]` sec |
| open | chatgpt-telegram-bot | win | clear | Both ran the same single-client wrap and found the same two Responses breaks (missing assistant function_call, role-only stream preamble), so the migration is a wash — the difference is the system-level pass, where B built more of the right things and reasoned harder about this specific app: a real  |
| open | llmcord | win | clear | Both wrap the same single call site identically, but B found the gap that decides whether the migration is real in production — the Dockerfile copied only llmcord.py, so a built image would silently fall back to the no-op wrapper and route nothing — and then went deeper on the system: nine measured, |
| open | sec-insights | win | clear | Both shipped the identical two-client migration with equal verification, so the decision rests on the improvements: A found the app-specific waste an expert would attack — a one-tool inner agent that costs a decision call and a round trip per document, a question engine offered with no sources, and  |
| ultra_facts2 | AI-Scientist | loss | slight | B's migration stands on its own: the pre-existing anthropic-SDK TypeError is fixed inside the migration, so the default --model pipeline actually executes and was verified end-to-end on the real Router (generate_ideas, a full 12-key review), and B additionally got aider's OpenAI-provider LiteLLM tra |
| ultra_facts2 | OpenManus | loss | slight | Both engineers made the same correct single-wrap move of all five app/llm.py call sites and both wrote unusually honest, well-ranked improvement sets, but A actually moved the deployment OpenManus ships (unlocking api.anthropic.com, with the vendor/billing shift stated plainly) where B only document |
| ultra_facts2 | chatgpt-telegram-bot | loss | clear | Both wrapped the single AsyncOpenAI client correctly and moved the same four chat call sites with the same three hard-limit stays, but B found and fixed one more real break that A shipped past: with STREAM=true (the repo default) Router's translated stream emits a role-only chunk that A's migration  |
| ultra_facts2 | llmcord | win | clear | Both moved the same single call site with the identical wrap, but B's migration is the one that actually works in this repo's real deployment — it caught that the Dockerfile copies only llmcord.py and config.yaml, so the built image would have had no router_runtime.py and the wrap would have silentl |
| ultra_facts2 | sec-insights | loss | slight | B's package is the one I'd ship and act on: all three call sites verified with no hand-edits to the vendored runtime, three improvements actually built and measured (labels, per-workload policy, a cross-provider fallback whose candidate was chosen by discovering the capability guard silently drops c |

| arm | wins | ties | losses | net margin (slight 1, clear 2, large 3) |
|---|---|---|---|---|
| open | 5 | 0 | 0 | +10 |
| open_oldfacts | 0 | 0 | 0 | +0 |
| ultra_facts2 | 1 | 0 | 4 | -3 |
