# router-migrate v2 scoreboard (MVP: migrate -> unlock -> improve)

Generated 2026-10-13 14:22 by `eval/run_eval_v2.py` on the 15 pinned dev repos (the v1 set).

**Totals.**
- **Lossless (v1, automatic):** 48 of 170 production sites.
- **With unlocks accepted:** 49. This is reported beside the lossless number, not inside it.
- **Tests or typecheck worse:** 0 repos lossless, 0 repos with every unlock and same-model card applied (Router off).
- **Cards:** 30 (model 10, same_model 19, unlock 1); 20 meet their lever's evidence bar. Card precision is unmeasured (no adjudication in the MVP).
- **Live spend for v2 (M0 + unlock replays):** $0.000795.

Cards by lever: `attr.metadata` 10, `fallback.candidates` 9, `model.newer_cheaper_same_family` 10, `unlock.vendor_endpoint` 1.

| repo | lossless | unlock cards | with unlocks | tests before | tests after unlocks + policy | cards (unlock / same-model / model) | bar met | model cards on non-owned sites | policy status | report |
|---|---|---|---|---|---|---|---|---|---|---|
| openai/swarm | 1 | 0 | 1 | 6 passed | 6 passed | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/swarm/REPORT.html) |
| TheR1D/shell_gpt | 1 | 0 | 1 | 29 passed | 29 passed | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/shell_gpt/REPORT.html) |
| microsoft/TaskWeaver | 3 | 0 | 3 | 100 passed | 100 passed | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/TaskWeaver/REPORT.html) |
| simonw/llm | 8 | 0 | 8 | 1208 passed | 1208 passed | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/llm/REPORT.html) |
| abi/screenshot-to-code | 0 | 0 | 0 | 280 passed | 280 passed | 0 / 0 / 0 | 0 | 0 | ok | [REPORT.html](reports/screenshot-to-code/REPORT.html) |
| anthropics/anthropic-quickstarts | 4 | 0 | 4 | 189 passed | 189 passed | 0 / 0 / 1 | 0 | 0 | ok | [REPORT.html](reports/anthropic-quickstarts/REPORT.html) |
| simonw/llm-anthropic | 3 | 0 | 3 | 123 passed | 123 passed | 0 / 0 / 0 | 0 | 0 | ok | [REPORT.html](reports/llm-anthropic/REPORT.html) |
| di-sukharev/opencommit | 2 | 0 | 2 | 243 passed | 243 passed (tsc 58 -> 58) | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/opencommit/REPORT.html) |
| mckaywrigley/chatbot-ui | 0 | 0 | 0 | 2 passed, 1 failed | 2 passed, 1 failed (tsc 10 -> 10) | 0 / 0 / 0 | 0 | 0 | ok | [REPORT.html](reports/chatbot-ui/REPORT.html) |
| openai/openai-responses-starter-app | 1 | 0 | 1 | tsc 0 errors | tsc 0 errors (tsc 0 -> 0) | 0 / 2 / 1 | 2 | 0 | ok | [REPORT.html](reports/openai-responses-starter-app/REPORT.html) |
| vercel/ai-chatbot | 0 | 0 | 0 | tsc 0 errors | tsc 0 errors (tsc 0 -> 0) | 0 / 0 / 0 | 0 | 0 | ok | [REPORT.html](reports/ai-chatbot/REPORT.html) |
| Nutlope/aicommits | 4 | 0 | 4 | 79 passed | 79 passed (tsc 0 -> 0) | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/aicommits/REPORT.html) |
| assafelovic/gpt-researcher | 2 | 0 | 2 | 570 passed | 570 passed | 0 / 1 / 0 | 1 | 0 | ok | [REPORT.html](reports/gpt-researcher/REPORT.html) |
| langchain-ai/langchain-nextjs-template | 8 | 0 | 8 | 4 passed | 4 passed (tsc 1 -> 1) | 0 / 9 / 8 | 9 | 0 | ok | [REPORT.html](reports/langchain-nextjs-template/REPORT.html) |
| mem0ai/mem0 | 11 | 1 | 12 | 2007 passed | 2007 passed | 1 / 1 / 0 | 2 | 0 | ok | [REPORT.html](reports/mem0/REPORT.html) |

## Definitions

- **lossless**: v1's automatic pass (unchanged from eval/SCOREBOARD.md).
- **unlock cards**: `unlock.vendor_endpoint` (another vendor's endpoint, model is an exact Router catalog id) and `unlock.stated_loss` (only droppable Anthropic beta flags).
- **with unlocks**: lossless + the sites those cards move once approved.
- **tests after unlocks + policy**: every unlock and same-model card approved, `apply --decisions` run, tests and typecheck re-run with Router off.
- **model cards** (`model.deprecated`, `model.newer_cheaper_same_family`, `model.right_size`): recommendations only, never applied; only for `app_literal` / `operator_config` model owners.
- **bar met**: the card's evidence reaches its lever's minimum (SPEC_V2 levers table). Model cards need L2 replay, which the MVP does not build.
- **policy status**: `router-migrate status` after apply (compiled hash current, no orphan entries).
