# 2-minute demo: router-migrate v3 on a repo it has never seen

Repo: [n3d1117/chatgpt-telegram-bot](https://github.com/n3d1117/chatgpt-telegram-bot) @ `df4dcaa` (one of the 10 fresh v3
eval repos). Every output below is from the recorded eval run: `eval/v3/runs/C/chatgpt-telegram-bot/` (run log, diff,
ledger, transcript) and `eval/v3/runs/A/chatgpt-telegram-bot/` (the rules-only floor).

## 0:00 The one command

```bash
router-migrate run . --test-cmd "<the repo's tests>" --agent claude
```

The deterministic floor runs first, then a coding agent works only what the rules couldn't decide, then the gate. No
Router key is needed for the migration; the gate's Router-on test run uses a local simulator.

## 0:15 What rules alone do (the floor)

The rules find 7 production LLM call sites and move **none** of them:

```
worklist: 19 of 19 items still need a claim
  S1  bot/openai_helper.py:261  openai chat.completions  -- flagged: per-tenant provider credentials: all tenants would
      share one Router key (billing/isolation change) [api_key from function parameter 'config']
  S2..S4  (same, lines 316, 459, 612)
  H5..H19 hotspots: model-name literals no scanner site explains
```

The client takes its key from a `config` parameter, and the rules can't tell whose key that is, so they refuse to touch
it. That caution is right for a rule; it is a judgment, not a fact.

## 0:35 What the agent does with it

It reads where `config` comes from, records the judgment **with evidence**, and applies the deterministic wrap:

```
claim bot/openai_helper.py:268 --outcome migrated
  why: Client wrapped at construction; api_key is a single operator key from OPENAI_API_KEY read once at startup,
       not per-tenant
  evidence: bot/openai_helper.py:113, bot/main.py:34, bot/main.py:111
```

The whole code change is one line (plus the import guard the codemod adds):

```diff
-        self.client = openai.AsyncOpenAI(api_key=config['api_key'], http_client=http_client)
+        self.client = _router_wrap_openai(openai.AsyncOpenAI(api_key=config['api_key'], http_client=http_client))
```

It dismisses the 15 model-name hotspots as `not_a_site` (lists of model names, not calls) and keeps the images and audio
calls (`kept R_NO_ENDPOINT`: Router has no images or audio endpoint).

## 1:10 The gate it can't skip

```
agent: 0, 45 turns, 5.7 min, $1.30
finish: OK
result: {"migrated_static": 4, "claims": {"not_a_site": 15, "migrated": 8, "kept": 3}, "migrated_confirmed_claims": 4, ...}
```

`finish` re-checked every claim against the tool's facts (a "migrated" claim the tool contradicts is rejected), ran the
repo's tests against the baseline (5/5) and again with Router on against router-sim, and checked that every edited file
holds a claimed site. Had any of that failed three times, it would have reverted the agent's edits to the floor.

## 1:35 The result, scored blind

| | moved | correct | wrong | broken |
|---|---|---|---|---|
| rules only | 0 | 0 | 0 | no |
| agent + Router's docs prompt (same floor, same abilities) | 4 | 4 | 0 | no (263-line diff, $3.63) |
| **agent + toolkit** | **4** | **4** | **0** | **no (9-line diff, $1.30)** |

All four chat calls should move: a blind adjudicator, checking the code, agreed the key is the operator's env var.
Across all 10 fresh repos, the toolkit arm is the only one with no wrong moves (eval/v3/RESULTS.md).

## 1:55 Where to look

- The agent's ledger in the report: `eval/v3/reports/chatgpt-telegram-bot/REPORT.html` (Migration tab, "Agent ledger").
- Design and thesis: SPEC_V3.md. Results: eval/v3/RESULTS.md.
