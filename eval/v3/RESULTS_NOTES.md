## Reading the results

**Headline (adjudicated ground truth: 16 production sites should move to Router across the 10 repos).**
- **C (agent + toolkit) is the only arm with no wrong moves**: 10 correct, 0 wrong, 0/10 repos broken, at $23.13 in total
  ($1.27 median per repo, 6 minutes median). Every site it reports as migrated is one the code routes and the ground truth
  says should move.
- **B' (agent + Router's docs prompt, same floor, same abilities)** also moves 10 correct sites but 16 wrong ones, at
  $25.05. **A (rules only)** moves 6 correct and 14 wrong. **B (docs prompt from the untouched source)** moves 6 correct
  and 7 wrong and broke 1 repo (a new type error), with a 3,212-line total diff.
- "Wrong" here means traffic the code claims to move that should stay put: mostly config-driven or user-defined provider
  lists that point at other vendors in production. The runtime never actually reroutes those requests, so they are
  report-precision errors rather than production breakage, but the report is what a customer acts on.
- Against the **raw** inventory (before adjudication), B' moves more correct sites (15) than C (10), with 11 wrong. The
  difference is almost entirely OpenManus: the inventory said its five calls migrate; C's agent read the shipped example
  configs, found the OpenAI client pointed at Anthropic's OpenAI-compatible endpoint (which the runtime never reroutes) and
  claimed them kept; the blind adjudicator agreed with C.

**What C missed (6 of 16).** obsidian-copilot's OpenAI and Anthropic branches: C's agent made exactly the right edit
(`routerFetch()` in the two provider configs of a dynamic constructor table), but the static oracle cannot see that
construction, so the claims are recorded *unconfirmed* and not credited. obsidian-textgenerator-plugin's three LangChain
providers built through a shared `new (this.llmClass)(config)`: no arm moved them. And openui's `openai.models.list()`
connectivity check (which the adjudicator marked ambiguous: the rubric's migrated rule names only generation surfaces).

**Primary hypothesis (spec 5.9).** C breaks no more repos than A (0 vs 0) and moves more correct sites than A (10 vs 6
adjudicated; 10 vs 11 raw). C breaks no more repos than B' (0 vs 0) and fewer than B (0 vs 1). With n=10 and k=1 every
broken-rate interval overlaps (0/10 bounds the rate at <=30.8%); treat this as indicative, as the spec says.

**Where the toolkit's value shows.** The agent made 11 edits across 10 repos (B: 176, B': 96) and ran the test suite
itself once (B: 66, B': 55), because `finish` runs the gates (eval/v3/tool_usage.json). Most of C's time went to
judgments recorded with evidence (330 `claim` calls, 71 `check`, 4 `wrap`; 11 of 458 tool calls errored, each with a
`next` hint). The cost outlier, obsidian-copilot ($9.21, 45 minutes), was a toolkit bug: the hotspot list included ~590
lines from TS test and story files. Fixed after the eval (hotspots: 658 -> 37 on that repo; ~1,000 -> 179 across the set),
so C's numbers above are from the frozen toolkit, not the fix.

**Caveats.**
- Ground truth is one blind agent's inventory per repo plus a blind adjudicator on the 25 sites where an arm disagreed;
  the adjudicator marked 2 of 25 ambiguous (llmcord's operator-written provider list; openui's `models.list`).
- k=1 per arm. The agent model was the CLI default (claude-sonnet-5) for B, B' and C.
- Arm B ran with a harness bug in its test command (`env K=V ...` applied the recipe's dummy env vars only to the first
  command, and openui's command could not run at all), so B could run tests less reliably than intended on 6 repos.
  B' and C ran after the fix.
- Eval agents were not network-sandboxed. B's agent on llmcord listed `eval/v3/` (file names, including inventory file
  names, appeared in its output) while looking for the simulator script; it did not open an inventory file.
- Scoring credits a site as moved only when the code routes it (measure-only planner) and the arm reports it migrated.
  Two scorer fixes were made after runs and applied to every arm: recognizing hand-written Router clients whose base URL
  is assigned elsewhere (B and B' were under-credited before), and pairing rewritten calls with inventory sites by file
  when line matching fails.
