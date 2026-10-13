# Draft: not sent

**To:** Kedar Thakkar
**Subject:** router-migrate: v1 results, the v2 Improve MVP, and an honest overfitting check

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
