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
