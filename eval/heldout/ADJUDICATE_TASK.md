# Adjudicate held-out disagreements

Two independent sources classified each held-out repo's LLM call sites:
- the tool's plan, in `R/eval/heldout/logs/<name>.plan.json` (field `outcome` + `reason`);
- a blind, rubric-based inventory, in `R/eval/heldout/inventory/<name>.json` (field `expected` + `why`).

`R` is `~/code/router-migrate`. The rubric is `R/eval/heldout/RUBRIC.md`; read it first, since it is the standard you judge
against. The code is at `R/eval/work/heldout/pristine/<name>`, which is **read-only**.

For each disagreement in `R/eval/heldout/adjudicate/<name>.json` for your repos, read the actual code and decide:
- **`"tool"`:** the tool's outcome is correct under the rubric. For a scanner-only site (`expected: "NOT IN INVENTORY"`), this
  means it is a real production LLM site and the tool's outcome is right.
- **`"inventory"`:** the inventory's expected outcome is correct. For a `tool: "NOT FOUND"` site, this means it is a real
  production site the tool failed to find, and the inventory's outcome is right. For a scanner-only site, it means the tool's
  "site" is not a real production LLM site, or is double counted (a false positive).
- **`"both-wrong"`:** neither is right. Give the correct outcome in `"correct_outcome"`.
- **`"ambiguous"`:** the rubric genuinely doesn't decide it. Say which rule is unclear and what each reading gives.

Judge by the rubric's rules and their order, not by what seems sensible. Watch out for:
- **Endpoint:** what the default configuration actually points at, including the shipped `.env`/config files.
- **Keys:** where they really come from.
- **Model literals:** whether they are in `R/router_migrate/data/catalog.json` or `dated_equivalents.json`.
- **Unreachable code:** count it, but note it.

Line numbers may be off by a few lines between the two sources.

Write your verdicts to `R/eval/heldout/adjudication/<name>.json` (one file per repo) as
`{"<key exactly as given>": {"correct": "tool|inventory|both-wrong|ambiguous", "correct_outcome": "...", "note": "<one or two
sentences citing file:line evidence>"}}`.

Do not edit anything else. Don't run router-migrate, install anything, use API keys or make network calls.

When done, reply with:
- counts per verdict;
- the 3 most important patterns you saw, i.e. what the tool systematically gets wrong or right on this code.
