# Draft: not sent

**To:** Kedar Thakkar
**Subject:** router-migrate: v1 results, the v2 Improve MVP, v3 agent-native results on fresh repos

Hi Kedar,

A short summary of where router-migrate stands. The one-pager is WRITEUP.md, the numbers are in eval/SCOREBOARD.md and
eval/SCOREBOARD_V2.md, and each repo has a frozen dashboard at eval/reports/<repo>/REPORT.html.

**v1, lossless migration.** On 15 pinned repos it moved 48 of 170 production LLM call sites onto Router, with tests and
typecheck unchanged in all 15. Every migrated site replayed cleanly against live Router.

**v2 MVP, migrate → unlock → improve.**
- **One command,** `router-migrate up`, opens a local Router-styled dashboard with "improve" cards. `--agent` gives the same
  flow to coding agents.
- **What you approve:**
  - unlocks that move more traffic;
  - same-model changes (spend tags, cache keys, Flex for background jobs, fallbacks), applied through a policy compiled into
    the runtime;
  - model-change recommendations, which are never applied automatically.
- **Results:** with every unlock and same-model card approved, it's 49 sites (reported next to the lossless 48, never summed),
  still 0 repos with worse tests, and 30 cards in total.
- **Small gain, on purpose:** the rules only move traffic when the model is exactly one Router serves.
- **Live measurement:** for under a tenth of a cent, Flex is exactly half price on gpt-5.4-mini, and one Flex call on
  gpt-5-nano took 61 s, so it's offered for background jobs only.

**v3, agent-native.** The held-out check showed rules can't judge config, tenancy or dead code, so v3 lets a coding agent
make those calls, with tools that decide the facts and a gate it can't skip: `router-migrate run . --agent claude`
applies the deterministic floor, the agent works only what's left with five commands (`run`, `check`, `wrap`, `claim`,
`finish`), and anything that fails the gate is reverted to the floor.
- On 10 repos neither v1 nor v2 ever saw, against blind inventories committed before any agent run, it was the only
  approach whose report had no wrong moves: 10 correct, 0 wrong, 0 of 10 repos broken, $1.27 median agent cost per repo.
- An agent given Router's docs prompt from the same starting point moved the same 10 correct sites plus 16 that should
  have stayed put; from the raw source it moved fewer and broke one repo.
- It's k=1 on 10 repos, so indicative. k=3 on a larger fresh set is the first next step. Details in eval/v3/RESULTS.md.

**The overfitting check.** I also ran the frozen tool on 30 repos it had never seen, judged against blind inventories.
- Only 53% of its "migrated" claims held up, against roughly 89% on the dev set. It misses LLM calls written in unfamiliar
  ways, and it doesn't resolve config defaults.
- It's still mostly safe at runtime: 27 of the 33 wrong claims would never actually move traffic.
- Fixing those patterns and re-testing on a fresh set is the first next step.

**Router issues worth knowing,** in the order I'd fix them:
- no server-side state for Responses chains;
- Flex is on by default;
- the catalog lists models the key can't use;
- incomplete replies report zero tokens and $0;
- `allow_flex_tier` is silently ignored with a `models` list.

Happy to walk through the dashboard.

Thanks,
Sanjith
