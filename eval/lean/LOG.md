# Lean-scaffold experiments (overnight, from 2026-10-14)

Question: does a lighter scaffold and a CTO-framed prompt ("less is more") produce better migrations and better,
broader improvements, with output still structured for the dashboard? Each round runs arms (prompt + scaffold
switches) headless on the same repos; a blind `claude -p` judge scores each run 1-10 (eval/lean/judge.py); every run
must still pass the outside check. k = 1 per arm and repo, so differences under ~0.5 are noise.

## Round 1 (3 repos: sec-insights, AI-Scientist, chatgpt-telegram-bot)

| arm | prompt | scaffold | overall | improvement depth | improvements/repo | min | $ |
|---|---|---|---|---|---|---|---|
| v6 | migrate_v6.md (~150 lines) | full | 7.3 | 6.0 | 1.7 | 18.6 | 8.4 |
| lean | arms/cto_lean.md (~55) | full | **8.0** | **8.0** | **6.0** | 18.0 | 8.0 |
| min | arms/cto_min.md (~25) | full | 7.7 | 7.0 | 4.0 | 18.5 | 6.8 |
| min_bare | arms/cto_min.md | no scanner map, check only | 7.7 | 7.0 | 4.0 | 17.9 | 8.5 |

All 12 runs passed the outside check; migration coverage was equal across arms (8.3-8.7). The CTO framing with levels
(quick-win / change / rework) and unbuilt proposals tripled the improvements without hurting migration. Common misses
in every arm: Flex for unattended work never considered; cross-references to ids that don't exist; arithmetic and price
slips; "loses: nothing" overclaimed; little thought about the shape of the work (fan-out, retries x timeouts,
per-conversation caching). The judge discounted live evidence because the outside check didn't record it.

Changes for round 2: the outside check records the agent's live Router checks (`validation.migration.live`);
lean2 = lean + one Flex fact + a reread-before-finish line; ultra (10 lines) and min_nofacts test the floor;
lean is rerun to measure noise; two more repos (llmcord, OpenManus).

## Round 2 (5 repos: + llmcord, OpenManus)

11 of 20 runs first died in an API outage (ENOTFOUND); runs whose transcript ends in an API error are now marked
invalid, skipped by the judge, and were rerun. All 20 final runs are valid and passed the outside check.

Absolute 1-10 scores bunched at 8 (every arm 7.8-8.4), so a pairwise judge was added (eval/lean/pairwise.py): for each
repo the reviewer sees two runs, order randomised and arms hidden, and picks the better one.

| arm | prompt | overall (1-10) | improvements/repo (built) | min | $ | head-to-head vs lean (W-L, net margin) |
|---|---|---|---|---|---|---|
| lean | arms/cto_lean.md | 8.4 | 6.6 (2.6) | 14.9 | 7.0 | reference |
| lean2 | lean + Flex fact + reread | 8.0 | 6.8 (2.4) | 15.1 | 7.4 | 3-2, +3 |
| min_nofacts | cto_min.md, no fact sheet | 7.8 | 4.8 (2.0) | 13.3 | 6.9 | 2-3, -2 |
| ultra | arms/cto_ultra.md (10 lines) | 8.0 | 3.6 (3.6) | 32.1 | 13.0 | **5-0, +10** |

The 10-line prompt won every head-to-head. The judge's reasons are consistent: more live verification against real
Router (it found defects the simulator misses, e.g. a function result sent without its call, a streamed tool call
dropped on Router's leading chunk), measured claims, and fewer wrong ones (lean's headline cache policy was a no-op
because the runtime already sets prompt_cache_key). Its costs: twice the time and spend, and with nothing saying
unbuilt proposals are allowed it builds every improvement, so it proposes half as many and uses no levels. Removing
the fact sheet hurts. Takeaway: cutting *procedure* helps, cutting *facts* hurts.

Round 3 builds on ultra, with ultra as the pairwise reference: ultra (rerun, noise), ultra_levels (+ one line: rank by
level, build what you're sure of, propose the rest unbuilt), ultra_nomap, ultra_nofacts.
