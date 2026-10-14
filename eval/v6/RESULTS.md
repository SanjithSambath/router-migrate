# v6 end-to-end runs (2026-10-14)

First runs of v6 on real repositories, with a migration key (live Router checks). Headless runs used
`eval/v6/run_e2e.py`: the agent migrates and improves, `finish` checks, a scripted reviewer approves up to two improvements
that passed their check and requests one undo, and an agent apply session carries the requests out. k = 1 per repository.
The improve rubric (`docs/plans/v6-improve-rubric.md`) has not been scored yet.

Per-run state (report, checks, requests, apply log, REPORT.html) is in `eval/v6/runs/<repo>/`.

| Repository | Path | Calls on Router (before the scripted undo) | Improvements | Apply session | Agent time, cost |
|---|---|---|---|---|---|
| sec-insights | headless | 3 verified, 2 stay (embeddings) | 1 branch: keep the chat prompt prefix cached (about -42% input cost on later turns); 2 notes | applied i1, undid m1; check passed | 17 + 4 min, $8.57 + $2.54 |
| AI-Scientist | headless | 9 verified, 5 unverified, 1 stays (Gemini, Llama via OpenRouter) | 0 branches (caching and one-client folded into the migration); 1 needs-decision, 2 notes | undid m5; check passed | 16 + 2 min, $8.50 + $1.68 |
| chatgpt-telegram-bot | headless | 7 verified, 3 stay (images, voice) | 1 branch: a cheaper model for long-chat summaries (-93%, 6 live samples); 1 note | applied i1, undid m2; check passed | 12 + 7 min, $7.21 + $4.02 |
| agent.email | Claude Code (this session as the agent) | 2 of 2 verified live (the shared Anthropic client) | 2 branches: classifier on Haiku 5.5 (about -88%), composer on Opus 5.5 (-20%); 3 notes | not run | not recorded |

Every run passed the outside check (tests and typecheck vs the baseline, existing tests unchanged, valid Router requests)
after migration and after each apply session.

## What the runs showed

- **v6 works end to end headless:** migrate, improve, finish, requests, an agent-run apply with undo, the check after.
- **Few improvements.** 4 branches across 4 repositories. The agents reviewed broadly and recorded why they didn't build
  more (e.g. the cheaper OpenAI models reject sec-insights' `temperature=0`, measured live; Router fallbacks would swap
  models silently on rate limits). The prompt's restraint ("a small change with evidence beats a large one", build and
  test every idea) and Responses-only levers (Flex, fallbacks, cache keys) limit the count. Open question below.
- **Bugs found and fixed during the runs:** the legacy `run --budget` flag collided with the prompt filler; requests were
  not marked as taken when an apply session started; files tracked by git but matching `.gitignore` were reported as
  deleted; route truth kept counting calls whose decision had been undone, and checks recorded before the undo.

## Open (paused)

- Let the agent propose ideas without building them (approve, then the apply session builds), to raise the number of
  recommendations without the per-idea build cost.
- agent.email: Router serves Claude on `/v1/responses` too, so porting its calls to the OpenAI-style API would enable
  fallbacks and automatic cache keys (not Flex: Anthropic models have no tiers on Router); it is a large port (tool loops,
  Anthropic server tools to Responses `web_search`, citation rendering).
- Score the runs against the frozen rubric; a clean-subagent run on agent.email; phone-width check of the restored
  traffic-flow illustration.
