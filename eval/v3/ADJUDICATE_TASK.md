# Adjudicate v3 disagreements (blind)

Several independent readers classified LLM call sites in the fresh v3 repos. Where they disagree about whether a site's
traffic should move to Ramp's Router, you decide, from the code and the rubric.

`R` is `~/code/router-migrate`. The rubric is `R/eval/heldout/RUBRIC.md`: read it first; it is the standard you judge
against. The code for repo `<name>` is at `R/eval/work/v3/pristine/<name>` (**read-only**).

Your questions are in `R/eval/v3/adjudication/questions/<name>.json`. Each question is one site (`file`, `line` in the
pristine source; lines may be a few off) with two or more `readings`, each an outcome plus the reader's reason. You are
**not told which reader gave which reading**, and you must not try to find out:
- never read `R/eval/v3/inventory/`, `R/eval/v3/runs/`, `R/eval/v3/results.json`, `R/eval/v3/RESULTS.md`, any
  `.router-migrate/` directory, or `R/eval/work/v3/trials/`;
- never run `router-migrate`, install anything, use API keys, or make network calls.

For each question, read the actual code (follow where the client, base URL, key and model come from: config modules,
shipped example configs and `.env` examples, docs) and decide the correct rubric outcome: `migrated`, `kept`, `flagged`,
`handoff`, or `nonprod` (not production, or not an LLM call site at all). Questions marked `not_in_inventory` ask whether
the site is a real production LLM call site at all and, if so, its outcome.

Judge by the rubric's rules **in their order**. Watch for:
- **Endpoint:** what the default/shipped configuration actually points at. Rule 2 says a base URL chosen from a
  user-defined provider list at runtime is kept. A client whose shipped default is another vendor's host (including
  Anthropic's OpenAI-compatible `api.anthropic.com/v1` used through the OpenAI SDK) is not on "the vendor's default
  endpoint" for that SDK.
- **Keys:** operator env/config vs a per-user field.
- **Models:** catalog ids are in `R/router_migrate/data/catalog.json` (`models`) and `dated_equivalents.json`.
- If the rubric genuinely does not decide it, pick the outcome its rule order gives and say what is ambiguous.

Write `R/eval/v3/adjudication/answers/<name>.json`:
`{"repo": "<name>", "answers": [{"id": "Q1", "expected": "migrated|kept|flagged|handoff|nonprod", "is_site": true,
"ambiguous": false, "why": "<one or two sentences citing file:line evidence>"}]}`

Do not edit anything else. When done, reply with counts per outcome and anything you found ambiguous.
