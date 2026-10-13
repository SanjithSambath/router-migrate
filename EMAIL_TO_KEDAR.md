# Draft: not sent

**To:** Kedar Thakkar
**Subject:** router-migrate: work trial results

Hi Kedar,

router-migrate is done. The one-page summary is WRITEUP.md, and the per-repo numbers are in eval/SCOREBOARD.md (and scoreboard.html).

The short version:

- **Coverage.** Across 15 pinned open-source repos (9 Python, 6 TS) it migrated 48 of 170 production LLM call sites to Router.
  - That is 75% of the eligible sites that call OpenAI/Anthropic directly.
  - It left tests and typecheck unchanged in all 15 repos.
  - Every migrated site replayed cleanly against live Router, for a total spend under a tenth of a cent.
- **Against the docs' migration prompt.** I ran that prompt with a Claude agent on the same repos. It moved more sites (90 vs 48), but it left
  failing tests or new type errors in 8 of 15 repos (llm went from 1208 to 1014 passing). In the repos it didn't break, it moved 32
  sites.
- **Design.** Instead of rewriting call sites to the Responses API, router-migrate patches only client constructors.
  - It vendors a small runtime that translates chat.completions to /v1/responses (streams included) under the SDK.
  - The runtime does nothing until RAMP_ROUTER_API_KEY is set.
  - When it is set, anything Router can't serve exactly goes to the original provider unchanged.
- **Router issues I hit live, in the order I'd fix them:**
  - no server-side state: previous_response_id and store are unsupported, so Responses chains break on turn 2;
  - the flex tier is applied by default, and can change between turns;
  - the catalog lists models the key gets 403 on;
  - Claude via Responses doesn't cache without prompt_cache_key;
  - switchyard changes model family mid-conversation.

I'd value your read on two judgment calls:
- per-tenant provider keys are flagged rather than consolidated onto one Router key;
- traffic on other vendors or gateways is kept rather than re-pointed at a different model.

Both cost coverage on purpose. A shared chain store and Router-side support for per-tenant keys would unlock most of what's left.

Thanks,
Sanjith
