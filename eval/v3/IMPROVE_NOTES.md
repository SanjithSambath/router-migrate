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
- **All six levers fire on the showcase repo** (tests/test_v2.py's synthetic app: a nightly job with a long prompt, a chat
  route, an xAI client, an Anthropic beta call): 10 cards, one or more per lever, from a real `router-migrate run . --agent
  claude` ($0.44 migrate + $0.29 improve; eval/v3/improve_showcase/ and the screenshots).
- **Cost.** $2.32 of agent time for the 4 repos with features (median $0.54, 2-6 minutes). Nothing is applied until a human
  approves a card; the live dashboard's Apply runs `router-migrate apply --decisions` and a quick scope gate, then asks for
  `finish` (the full gate) before anything ships.
- **Still a heuristic: model ownership.** Model-change cards are shown only where the app or its operator owns the model;
  ownership still comes from v2's heuristic, which labels some config-chosen models `library_param` (7 features here),
  withholding model cards there. Making ownership an agent judgment is a Track 2 item.

