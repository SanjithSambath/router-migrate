## Reading the results

- **The Improve phase only works on traffic that moved.** After arm C, 4 of the 10 fresh repos have migrated production
  features (9 in total); the other 6 had none to improve, either because the agent correctly kept their traffic on its
  provider (user-defined provider lists, another vendor's host) or because its edits sat where the static oracle can't
  confirm them (obsidian-copilot's dynamic constructor table). Those 6 still get a REPORT.html with the setup checklist.
- **Judgments turned 4 cards into 15.** With no judgments the tool could only propose spend tags (1 per repo). The agent's
  `model` judgments (the production default behind a config value, e.g. sec-insights' `OPENAI_CHAT_LLM_NAME = gpt-4o-mini`)
  let the tool compute capability-matched fallbacks and newer-model candidates; its `cacheable` judgments found AI-Scientist's
  18,200- and 7,150-token static review and ideation prompts. Every one of the 11 added cards shows its agent rationale with
  cited lines, apart from the tool's facts.
- **The agent disagreed with the filename heuristic on 8 of 9 features' latency**, each with cited callers: AI-Scientist's
  calls run inside an unattended multi-hour pipeline (`background`, the heuristic said `user_waiting`); the Telegram bot,
  openui and sec-insights stream to a waiting user (`interactive`). A wrong `background` would offer Flex (up to a minute
  per call) to a chat; a wrong `user_waiting` would hide a half-price lever from a batch job.
- **No Flex card fired, correctly.** The one background workload (AI-Scientist) runs on `gpt-4o-2024-05-13` and Claude 3.5
  Sonnet, which have no Flex tier on Router; the considered-not-recommended list says so. **No unlock card fired**: the only
  candidate (computer_use_ootb) needs the computer-use beta, which can't be dropped.
- **Cost.** $2.32 of agent time for the 4 repos with features (median $0.54, 2-6 minutes). Nothing is applied until a human
  approves a card; the live dashboard's Apply runs `router-migrate apply --decisions` and a quick scope gate, then asks for
  `finish` (the full gate) before anything ships.
- **Budgets are repo-specific.** The per-key cap item lists what routes through the key (migrated features plus applied
  unlocks): the list price per model with the features on it, a per-1,000-calls figure at a stated token shape (the
  agent's judged static prompt where it judged one, otherwise 1,000 in / 500 out, marked assumed), the features whose model
  is picked at runtime (7 of the 10 repos: nothing in code or config to price), and the spend tag to watch. No cap number is
  invented: volume is unknown to the tool and to Router.
- **Still a heuristic: model ownership.** Model-change cards are shown only where the app or its operator owns the model;
  ownership still comes from v2's heuristic, which labels some config-chosen models `library_param` (7 features here),
  withholding model cards there. Making ownership an agent judgment is a Track 2 item.


## End to end, as a person runs it (eval/v3/e2e/, eval/v3/e2e_run.py)

One command per repo, `router-migrate run <repo> --agent claude` (floor -> agent migrates -> `finish` gate -> agent judges
-> cards), then the live dashboard driven by headless Chromium on its 127.0.0.1 session URL (approve each applicable card in
the UI, click "Apply N changes", read the result panel), then `finish` again on the applied repo, then the Router request body
each feature now sends, built offline by the repo's own vendored runtime (nothing sent; $0 Router spend).

| repo | floor -> routes now | migrate agent | gate | improve agent | cards (levers) | approved in the UI -> applied | quick gate | `finish` after Apply |
|---|---|---|---|---|---|---|---|---|
| sec-insights (fresh) | 2 -> 2 | $0.69, 5.9 min | 15/15 tests | 2 judged, $0.32, 1.5 min | 5 (3) | 3 -> 3 | ok | OK, 15/15 |
| showcase | 2 -> 2, 4 with unlocks | $0.41, 1.3 min | 1/1 | 2 judged, $0.37, 1.8 min | 10 (6) | 6 -> 6 | ok | OK, 1/1 |

What each routed feature sends after Apply (eval/v3/e2e/<repo>/policy_probe.json):
- **showcase** `nightly_summaries.py:summarize_all` (background): `gpt-5.4-mini` with `allow_flex_tier: true` and its
  `prompt_cache_key` (**Flex and caching, applied**); `chat.py:answer`: `models: [gpt-4-turbo, gpt-5.5, claude-opus-5]`
  (fallbacks); `xai.py:generate`: `grok-4.3` through Router instead of api.x.ai (**vendor-endpoint unlock, applied**: the
  client is patched with `unlock_hosts`); `agent.py:run`: Router's /v1/messages with the prompt-caching beta dropped
  (**stated-loss unlock, applied**). Every one carries `metadata.product=showcase`. The fallback card for the nightly job
  was left undecided: it is the either/or partner of the Flex card (below).
- **sec-insights** both LlamaIndex features: `models: [gpt-4o-mini, gpt-6-luna, claude-haiku-5-5]` with
  `metadata.product=sec-insights`. Its two model cards stay recommendations (never applied).

**chatgpt-telegram-bot, twice.** Run 1 (live in the browser pane): floor 0, the agent migrated the 4 per-tenant-credential
sites (+4, $0.91), gate 5/5, improve 5 cards ($0.46), all 5 applied from the dashboard in two rounds, `finish` OK, every
feature sending a fallback models list. Run 2, same command on a fresh copy: the agent claimed the same 4 sites `flagged`
(all tenants would share one Router key; $0.95), the gate passed, and Improve correctly had nothing to improve (0 cards; the
dashboard's Apply stays disabled). Both choices are defensible and the ledger records why; the decision belongs to the user
(Track 2: ask before migrating per-tenant credentials). Run 1's artifacts were overwritten by run 2; its numbers are from
the run log.

**What closing the loop found** (DECISIONS D15; each fixed with a test): the live page and Apply raced on one snapshot
index (the gate saw the state dir as edits); Flex and fallbacks on one feature are either/or (a models list is served
without Flex) and now say so on both cards before approval; an applied unlock came back as a new card (the codemod moved
its line); Apply re-counted earlier cards; the budget counted kept features and missed unlocks; policy applies were not in
the ledger.
