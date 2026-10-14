# router-migrate v3: an agent-native migration architecture (thesis, spec, results)

**One sentence:** the coding agent runs the migration; router-migrate gives it instruments that establish facts and a gate
it cannot skip; nothing the agent claims counts until a tool confirms it, and the deterministic floor is never lowered.

**Status.** Track 1 (section 5) is built and measured on 10 repos nobody tuned on (section 6). Track 2 is the next-steps
list in WRITEUP.md. This is revision 3 of this file: revision 1 proposed eight primitives and nine recipes; revision 2
(after an adversarial review) cut that to five commands and a patch stack; revision 3 rewrites the document around what
building and measuring it taught. Section 10 lists every change and why. DECISIONS.md D13 has the engineering log.

---

## 0. The problem and the evidence

A codebase calls LLM providers in production; Router can serve some of those calls behind one key. A migration tool is
judged on how much traffic it moves and whether anything breaks, and the report it writes is what the customer acts on.

| What we measured | Where | What it says |
|---|---|---|
| Rules only (v1/v2): 48 of 170 dev sites moved, **0 of 15 repos broken**, median diff 11 lines | eval/SCOREBOARD.md | Deterministic transport (a one-line wrap plus a vendored runtime) is safe. |
| A Claude agent with Router's docs prompt, no tests: **8 of 15 repos broken**; 32 sites moved in the 7 it left intact; median diff 565 lines | README, eval/baseline.json, D9 | An unstructured agent over-edits and breaks things. |
| Held-out (30 unseen repos): the scanner found **71%** of Router-relevant sites; **53%** of its "migrated" claims were right | eval/heldout/AUDIT.md | Rules miss what a reader would find and can't judge runtime values. |
| Of the 32 wrong "migrated" claims, 30 depend on **where a value comes from at runtime** (a config base URL pointing at another vendor, a provider-dispatch default, a per-user key, dead code) | eval/heldout/audit.json | That is a reading task. The runtime never mis-routes these (it only reroutes requests addressed to OpenAI/Anthropic), so they are report errors, not outages, but the report is the product. |

So the safe part is solved and the remaining gap is judgment. The question v3 answers: what is the right way to add an
agent's judgment without giving up the floor's safety?

## 1. Thesis: the architecture, from first principles

**What coding agents are good and bad at today.** They read code well, follow data flow across files, find calls that
don't match any pattern, and edit precisely when told what to change. They are bad at four things that matter here: they
cannot observe facts outside the repo (what Router accepts, what a vendored runtime will do with a request); they
over-edit when the goal is open-ended (the 565-line median diff); they declare success they haven't checked; and they
stop when they think they're done, not when they are.

**Three kinds of work, three owners.** Each belongs to whoever has an oracle for it:

| Work | Example | Owner | Why |
|---|---|---|---|
| **Facts** | does Router serve this request; does the runtime reroute this call in the code as it stands; did tests, typecheck or imports get worse | a tool, not overridable | there is an oracle; an agent's opinion adds only risk |
| **Judgments** | where a config value points in production; whose key this is; whether code is reachable | the agent, citing lines a tool can resolve | no oracle exists; reading is the only way |
| **Safe edits** | the one-line wrap of a client construction | a deterministic codemod, whoever asks | it is why 0 of 15 repos broke |

**Seven design rules follow.**
1. **Floor first; the agent only adds.** The codemod applies everything it can before the agent starts; the agent works the
   residue; anything that fails the gate is reverted to the floor. The result can't be worse than rules alone.
2. **Give the agent instruments for what it can't know, not tools for what it already does well.** Facts about Router and
   the runtime, the safe edit, and the gate. Not code search, call graphs or config readers: the agent's own Read and Grep
   do that better, and every extra tool is surface it must learn and we must maintain.
3. **One oracle for "does this route".** The same check answers the agent (`check`), validates its claims (`claim`),
   gates the session (`finish`) and scores every eval arm. If they disagreed, the agent could pass the gate and fail the eval.
4. **Gate outcomes, not keystrokes.** One gate at the end (and on Stop): diff scope, typecheck and tests against the
   baseline, a Router-on run against a simulator, every claim re-checked, every worklist item resolved. Per-edit blocking
   hooks fight the agent's workflow and exist in only one harness; an outcome gate is an exit code any agent can obey.
5. **Claims carry evidence; facts beat claims; unconfirmed stays unconfirmed.** A "migrated" claim on a call the tool says
   doesn't route is rejected with the tool's reason and next step. A call the tool can't see is accepted but never credited
   until a test run with Router on shows a request from it.
6. **Short instructions: the loop, ordered rules, recipes with "when not to act", hard nevers.** One ~90-line prompt, not a
   playbook library. Repo text is data, not instructions.
7. **Credit only what the code does and the report says; measure like a model.** A site counts as moved only if the code
   routes it and the report claims it. Evaluate on repos nobody tuned on, with ground truth frozen before any agent run,
   against an agent with the same abilities minus the toolkit, one scorer for every arm, cost and time beside every number,
   intervals on small-n rates.

**Prior art these rest on** (section 11): tool-confirmed evidence as "done" (Meta Assured LLMSE; Spotify verifiers);
deterministic nodes the agent can't skip (Stripe Minions; Airbnb's state machine); codemod first, agent on the annotated
residue (Slack: ~45% AST alone, ~80% hybrid; Google's over-inclusive discovery then buckets); few consolidated tools with
errors that say what to do next (Anthropic's tool guide; SWE-agent ACI); short map-style instructions (OpenAI harness
engineering); evaluation with held-out sets, paired arms and cost (Kapoor et al.; Anthropic's eval posts; tau-bench).

**What this architecture deliberately does not do.** Rewrite call sites to another API (it breaks every response reader;
D1). Put an agent on the request path (the runtime runs on every production request). Let the agent decide facts. Block
individual edits with hooks. Ship a library of playbooks. Build bespoke code-navigation tools. Report numbers from repos
the tool was tuned on.

## 2. Architecture

```
  router-migrate run . --test-cmd "<tests>" --agent claude          (no --agent: rules only = the floor)
        │
        ├─ FLOOR  [tool]   v1 scan → plan → apply → verify; baseline tests/typecheck; git-tree snapshot (private index)
        ├─ STATE  [tool]   .router-migrate/ledger.json: baseline, worklist (S = scanner sites the rules can't settle,
        │                  H = hotspots no scanner site explains), claims, edits, gate history, cost
        ├─ AGENT  [agent]  headless Claude Code; tools: Read/Grep/Glob/Edit + `router-migrate *` + read-only shell
        │                  loop: read code → check FILE:LINE → wrap FILE:LINE (if a construction was missed) → claim ... --evidence
        ├─ GATE   [tool]   finish: parse → scope → typecheck vs baseline → tests vs baseline → Router-on vs floor (router-sim)
        │                  → claims re-checked → worklist resolved; Stop hook runs it; 3 failures → revert agent edits to floor
        └─ REPORT [tool]   REPORT.html: v1 sites + the agent ledger (origin, outcome, reason code, what the tool says, evidence)
```

Unchanged from v1/v2 and never touched by the agent: the runtime (Python and TS), the codemod, router-sim, verify, the v2
policy compiler and cards. Other agents (Codex) follow AGENTS.md; `finish` is their gate.

## 3. The oracle

The oracle is the v1 planner in **measure-only** mode on the code as it stands: a site routes if its client goes through the
vendored runtime (or points at Router natively), the surface is one Router serves, the host is OpenAI/Anthropic by literal
or default, and the model resolves in the catalog (or is chosen at runtime with catalog ids in the repo).

- **Three-valued verdict.** `routes`; `wont_route` with the reason; `routes_if` with the runtime condition (a base URL or
  provider dispatch from config). The agent settles a condition with evidence; unsettled, the site is reported
  *conditional*. "Model in catalog" is not a condition: the runtime decides that per request and falls back, so it never
  makes a report wrong.
- **Tenancy is a judgment.** The floor never wraps a client whose key looks per-tenant. If the agent reads the code, decides
  the key is the operator's, and wraps the client, the oracle reports that the code routes.
- **Validated.** On the 15 dev repos the oracle agrees with plan.py on **170/170** sites after the floor, and the dev set still
  migrates **48** (eval/v3/check_agreement.json). Getting there found three real bugs (D13).
- **Known blind spot.** Dynamic construction (`new (this.llmClass)(cfg)`, constructor lookup tables) is invisible to it.
  Claims there stay *unconfirmed* unless a test run with Router on exercises them.

## 4. The ledger (`.router-migrate/ledger.json`)

```jsonc
{
  "version": 3, "floor": {"tree": "<git tree id>", "migrated": 5},
  "baseline": {"test_cmd": "...", "before": {"passed": 4, "failed": 0}, "router_on_floor": {...},
               "typecheck_cmd": "...", "typecheck_before": {"exit": 0, "errors": 0}},
  "worklist": [{"id": "S1", "type": "site", "file": "bot/openai_helper.py", "line": 261, "what": "openai chat.completions",
                "why": "flagged: per-tenant provider credentials ...", "tool_outcome": "flagged"},
               {"id": "H5", "type": "hotspot", "file": "...", "line": 22, "what": "model-literal", "why": "..."}],
  "claims": {"bot/openai_helper.py:268": {"outcome": "migrated", "reason_code": null, "why": "single operator key from
             OPENAI_API_KEY read once at startup", "evidence": ["bot/main.py:34", "bot/main.py:111"], "verdict": "routes",
             "origin": "scanner", "confirmed": "static", "feature": "bot/openai_helper.py:__common_get_chat_response"}},
  "edits": [{"by": "codemod", "what": "floor"}, {"by": "agent-wrap", "file": "bot/openai_helper.py", "line": 107}],
  "finish": {"attempts": 0, "history": [{"ok": true, "gates": [], "info": {"tests": {...}, "sim": {...}}}]},
  "cost": {"agent_usd": 1.30, "agent_minutes": 5.7, "agent_turns": 45},
  "result": {"migrated_confirmed_claims": 4, "migrated_unconfirmed_claims": 4, "conditional_unresolved": 0}
}
```

The CLI owns every write; the agent writes only through `claim` and `wrap`. Facts are recomputed on demand, never cached.
`confirmed` is `static` (the oracle says it routes), `dynamic` (the Router-on run saw a request from that call's
`file:function`, which the runtime sends as `metadata.feature`) or `unconfirmed` (reported apart, never credited).

## 5. Track 1: what was built

### 5.1 Commands

Five, all with concise text by default, `--json` for detail, at most 200 lines, and every error naming the next command.

| Command | Kind | What it does |
|---|---|---|
| `run PATH [--test-cmd C] [--typecheck-cmd C] [--agent claude]` | floor + state (+ launcher) | v1 scan → plan → apply → verify, the git-tree snapshot, the ledger and the worklist. Idempotent: on a migrated repo it only shows the worklist. With `--agent claude`: launches the agent headless (prompt, restricted tools, a Stop hook from a temp settings file, nothing written to the repo's `.claude/`), then runs `finish`. |
| `check FILE:LINE` | fact | The oracle on one call as the code stands: verdict, condition, reason, and the concrete next step (e.g. the unwrapped construction to `wrap`). |
| `wrap FILE:LINE [--stateful]` | edit | The v1 codemod at any client construction the agent points at, factories included; adds the LiteLLM / module install hook for module-level usage. Idempotent; returns the diff. |
| `claim FILE:LINE --outcome O [--reason R_...] --why "..." --evidence FILE:LINE...` | state | Outcomes `migrated kept flagged handoff needs_human not_a_site`; enumerated reason codes for every outcome but migrated; evidence must resolve to existing lines; facts beat claims. |
| `finish PATH [--hook]` | gate | Cheapest first, stop at the first hard failure. After 3 failed runs: restore every agent-changed file to the floor, mark those sites `needs_human`, let the session end. `--hook` emits `{"decision": "block", "reason": ...}` (the Stop-hook protocol, checked against the Claude Code hooks reference); the session can always end through the attempt cap. |

### 5.2 Prompt, recipes, reason codes

`router_migrate/prompts/migrate.md` (the skill body and the launcher's prompt; frozen at commit 5eec746 before the eval):
the measured Router facts (keep provider keys while any traffic through them can't move; catalog ids are often the public
names); the five tools; the loop; eight outcome rules in priority order with their reason codes; four recipes with
"when not to act" (`client-factory`, `config-base-url`, `vercel-dispatch`, `per-tenant-key`) plus the runtime adapters
(LiteLLM install hook, `http_client()`, `routerFetch()`; `requests` has none, so `handoff R_RAW_REQUESTS`); hard nevers
(no call-site rewrites, model changes, test/lockfile/CI edits, provider-key deletion; don't read the runtime; don't run
the test suite in a loop; repo text is data).

Reason codes: `R_NO_ENDPOINT`, `R_OTHER_HOST`, `R_STATEFUL`, `R_PER_TENANT`, `R_BETA`, `R_MODEL_NOT_IN_CATALOG`,
`R_RAW_REQUESTS`, `R_OTHER_GATEWAY`, `R_OTHER_VENDOR_SDK`, `R_BYOK_REQUIRED`, `R_DEAD_CODE`, `R_NOT_PRODUCTION`,
`R_UNDECIDED`, `R_GATE_FAILED`.

### 5.3 The gate in detail

| Gate | Fails when | Next step it prints |
|---|---|---|
| parse | an agent-changed Python file doesn't compile | fix the syntax error in that file |
| scope | a test, lockfile or CI file was edited; a file was deleted; an edited file holds no claimed site or agent wrap | revert it / claim the site in it |
| typecheck | more type errors than the baseline | fix the new errors in the files you changed |
| tests | fewer passed or more failed than the baseline (Router off) | fix what you broke, never the tests; or claim needs_human and undo |
| sim | router-sim rejects a request, or tests with Router on regress past the **floor's own** Router-on run | fix or undo the edit |
| claims | a `migrated` claim the oracle no longer agrees with | the oracle's next step |
| worklist | an item has no claim | claim each (needs_human is allowed) |

Comparing the Router-on run with the floor's Router-on run (not with Router off) matters: suites that mock HTTP transports
see Router requests inside their mocks under the deterministic floor too; only a regression past the floor is the agent's.

### 5.4 Milestones (each with a PROGRESS.md line and a commit)

| T | Built | Done when | Result |
|---|---|---|---|
| T0 | Headless agent | `claude -p` smoke returns | `SMOKE_OK` with a long-lived OAuth token (the old session had expired); eval/v3/smoke/t0_headless.json |
| T1 | The oracle (wrap-aware measure-only planner) + `check` with the three-valued trace | `check` agrees with plan.py on all 170 dev sites; dev still 48 | 170/170, 48 (eval/v3/check_agreement.json) |
| T2 | `run`, `wrap`, `claim`, `finish`, ledger, revert, hook mode | golden tests: failing work reverts to the floor; invalid claims rejected with `next` | tests/test_agent_v3.py (10 tests at eval time, 11 now) |
| T3 | Prompt, skill v3 loop, `run --agent claude` | on 3 dev repos, `finish` exit 0 and 0 broken | swarm, shell_gpt, aicommits: all pass, $0.79-1.19 each (eval/v3/smoke/dev_smoke.json) |
| T4 | 10 fresh repos, blind inventories, test recipes; independent breakage check | pinned and inventoried before any arm C run | committed 488677a, before the first arm C commit |
| T5 | Eval harness, arms A/B/B'/C, blind adjudication, REPORT.html ledger table | RESULTS.md with per-repo table, $ and minutes | eval/v3/RESULTS.md (section 6) |
| T6 | WRITEUP v3 section, email draft, DEMO_V3.md (2-minute demo on a fresh repo), "What Router should build" | every number traces to a file | WRITEUP.md, EMAIL_TO_KEDAR.md (draft, not sent), DEMO_V3.md |

## 6. Evaluation and results

**Set.** 10 repos screened as held-out candidates but never used, inventoried or tuned on (eval/v3/REPOS.json): 7 Python,
3 TS; bots, agents, RAG apps, Obsidian plugins, a browser extension. Ground truth: one blind agent per repo applied the
held-out rubric to the pinned source; inventories were committed before any arm C run. Every site where an arm disagreed
with the inventory on whether traffic should move (25) was then judged by a separate agent from the code, with readings
anonymized and source markers stripped (eval/v3/adjudication/).

**Arms** (same pinned commits, fresh per-trial git history, no Router key: the agents only ever talked to router-sim; k=1;
the agent model was the CLI default, claude-sonnet-5):
- **A** rules only (`router-migrate run`).
- **B** Router's docs prompt verbatim + the same abilities as C (test command, a Router-on run against router-sim), from the
  untouched source: what a customer gets today by pasting the docs prompt into a coding agent.
- **B'** the same, starting from A's floor: isolates the toolkit.
- **C** agent + toolkit (`router-migrate run --agent claude`, from the same floor).

**Scoring** (one scorer for every arm): broken = any regression in the repo's tests or typecheck vs a baseline measured in the
same harness, or in an independent check the agent never sees (import smoke of changed modules vs pristine; held-back replay
of every routed site through the runtime to router-sim). Moved = the oracle says the code routes it **and** the arm reports
it migrated, matched to ground truth by file and line (or by file and call family when an arm rewrote the call).

**Results** (eval/v3/RESULTS.md; adjudicated ground truth: 16 sites should move):

| arm | broken | correct moves | wrong moves | precision | agent $ total (median per repo) | diff lines | edits by the agent | test runs by the agent |
|---|---|---|---|---|---|---|---|---|
| A rules only | 0/10 | 6 | 14 | 30% | 0 | 177 | - | - |
| B docs prompt, from source | 1/10 | 6 | 7 | 46% | 35.27 (3.47) | 3,212 | 176 | 66 |
| B' docs prompt, from the floor | 0/10 | 10 | 16 | 38% | 25.05 (2.09) | 997 | 96 | 55 |
| **C toolkit** | **0/10** | **10** | **0** | **100%** | **23.13 (1.27)** | 191 | 11 | 1 |

**What it shows.**
- The toolkit's value is report precision at no cost in safety: the same correct moves as an equally capable agent from the
  same floor, none of its 16 wrong ones, at lower cost and with a tenth of the edits.
- Against the raw inventory (21 should move), B' moves more correct sites (15, with 11 wrong) than C (10, 0 wrong). The
  difference is OpenManus, where C's agent read the shipped config (an OpenAI client pointed at Anthropic's endpoint, which
  the runtime never reroutes) and claimed "kept"; the blind adjudicator agreed. The headline holds either way: C is the only
  arm whose report claims no wrong move.
- C's six misses: obsidian-copilot's two provider branches (the right edit, inside a dynamic constructor table the oracle
  can't see, so unconfirmed and uncredited); obsidian-textgenerator's three LangChain providers built through one dynamic
  constructor (no arm moved them); one ambiguous `models.list`.
- The spec's hypothesis (C breaks no more than A and moves more correct sites than A; C breaks no more than B') holds on the
  adjudicated truth and holds on breakage either way; with n=10 and k=1 every interval overlaps (0/10 bounds the rate at
  <=30.8%). Indicative, as planned.

**What the transcripts taught** (eval/v3/tool_usage.json): C's agents made 458 `router-migrate` calls with 11 errors, each
followed by its `next` hint. The costliest run (obsidian-copilot, $9.21, 45 minutes) was spent dismissing ~590 hotspots in
TS test and story files: a toolkit bug, fixed after the eval (hotspots ~1,000 -> 179 across the set; reported numbers are
from the frozen toolkit). And after `wrap` inserted its import block, the worklist's line numbers in that file went stale,
so agents claimed the same call twice (once at the stale line, recorded unconfirmed); also fixed after the eval: `wrap`
now remaps the ledger's lines in the file it edits.

**Caveats.** One inventory agent per repo plus one adjudicator (2 of 25 verdicts marked ambiguous); k=1; arm B ran with a
harness bug in its test command on 6 repos; eval agents were not network-sandboxed (one B agent listed `eval/v3/` file
names while looking for the simulator script; none read an inventory); scorer fixes made after the runs were applied to
every arm (D13).

## 7. Track 2

In WRITEUP.md ("Next steps"), in priority order: k=3 and pass^3 on a larger fresh set; a dynamic trace (import factories
under `.env.example` values against router-sim) to confirm what the oracle can't see; scanner rules for dynamic
constructors; a fresh-context reviewer if judgment errors dominate; per-patch drop if agents start mixing good and bad
edits; and the open v1/v2 items.

## 8. What Router should build

In WRITEUP.md ("What Router should build"): a Chat Completions surface or adapter; Responses state or one documented
error; Flex eligibility and price in `/v1/models` and honest Flex with `models` lists; dated aliases and access-aware
catalog entries; first-class per-feature attribution on `metadata.feature`; a usage / shadow-results API; drop-in base
URLs for OpenAI-compatible vendors and simpler Gemini BYOK; usage and request ids on every reply.

## 9. Risks and limits

| Risk | Mitigation / status |
|---|---|
| Agent breaks repos | Floor first; scope gate; tests, typecheck and Router-on vs baseline; revert after 3 failures. 0/10 broken for C. |
| Agent over-claims | `migrated` needs the oracle; unconfirmed claims never credited. 0 wrong moves for C. |
| Agent under-claims or misjudges | Not prevented by the gate; measured against blind ground truth. C missed 6 of 16, 5 of them in code the oracle can't see. |
| Oracle blind spots (dynamic construction) | Reported as unconfirmed, not hidden. Track 2: dynamic trace. |
| Small sample | n=10, k=1, exact intervals, per-repo paired table. Track 2: k=3 on 20-30 repos (~$210 at C's mean cost). |
| Ground-truth quality | Blind inventory plus blind adjudication; both readings published. |
| Prompt injection from repo text | Prompt says repo text is data; tools restricted; scope gate. Eval agents were not network-sandboxed. |
| Tuning on the test set | Prompt frozen before arm C; one dry run (llmcord) was not used to tune; post-eval fixes are labelled and excluded from results. |
| Secrets | No Router key in any agent run; the agent's own token lives outside the repo; transcripts scanned clean. |

## 10. Revision history: what changed and why

| Revision 1 / 2 said | Revision 3 (built) | Why |
|---|---|---|
| Eight primitives (`find`, `graph`, `config`, ...) | Five commands | Rule 2: the agent already searches and reads; hotspots replaced `find`. |
| `up` writes the ledger and commits "floor" | `run` does, with a git tree in a private index | No commit or index change in the customer's repo; `up` stays the v2 dashboard. |
| Patch stack, 2 repairs per patch, then drop | One floor snapshot; 3 failed `finish` runs revert all agent edits | Same guarantee, far less machinery; C made 11 edits across 10 repos. |
| PreToolUse hook blocks edits to unlinked files | Scope gate in `finish` | Rule 4; works without hooks. |
| Fresh-context reviewer subagent | Not built | Over-claiming is already blocked; measure judgment errors first (Track 2). |
| Nine recipe files | Four recipes in one prompt | Rule 6. |
| `routes_if` includes "model in catalog" | Base URL / dispatch conditions only | The runtime handles models per request. |
| Per-tenant key is a fact | A judgment the agent can override by wrapping with evidence | Tenancy has no oracle; on chatgpt-telegram-bot the heuristic was wrong and only C moved the 4 calls. |
| Router-on gate vs Router-off baseline | vs the floor's Router-on run | Transport mocks see Router requests under the floor too. |
| Agent may run the repo's tests | Only `finish` does | B and B' ran test suites 66 and 55 times; C once. |
| Every unexplained LLM-looking line is a hotspot | Test/story files excluded, model literals one per file (after the eval) | 590 of 659 items on one repo were noise. |
| Arm B' only; unmatched sites adjudicated | Arms B and B'; all 25 disagreements adjudicated blind | B is today's customer path; the raw inventory was wrong on 9 sites. |
| "90 moved" for the docs-prompt baseline | 32 moved in the 7 repos it didn't break | Nothing in the repo supported 90. |
| Hook fields to be verified | Verified: Stop hook gets `stop_hook_active`; `decision: block` + `reason` continues the session | Claude Code hooks reference. |
| Held-out 30 relabelled "seen", arm A regression | Cut; the dev-set agreement run covers arm A | Time; logged in D13. |

## 11. Sources

- Google: https://arxiv.org/html/2501.06972 , https://arxiv.org/html/2504.09691 ,
  https://research.google/blog/accelerating-code-migrations-with-ai/
- Amazon Q Code Transformation: https://aws.amazon.com/blogs/aws/upgrade-your-java-applications-with-amazon-q-code-transformation-preview/
- Airbnb: https://medium.com/airbnb-engineering/accelerating-large-scale-test-migration-with-llms-9565c208023b
- Slack: https://slack.engineering/balancing-old-tricks-with-new-feats-ai-powered-conversion-from-enzyme-to-react-testing-library-at-slack/
- Meta: https://arxiv.org/html/2402.09171 , https://arxiv.org/abs/2402.04380
- Stripe Minions: https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2
- Spotify: https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1 (parts 2 and 3)
- OpenAI harness engineering: https://openai.com/index/harness-engineering/
- Shopify Roast: https://shopify.engineering/introducing-roast
- SWE-agent ACI: https://arxiv.org/html/2405.15793
- Anthropic: https://www.anthropic.com/engineering/writing-tools-for-agents ,
  https://www.anthropic.com/engineering/building-effective-agents ,
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents ,
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents ,
  https://www.anthropic.com/research/statistical-approach-to-model-evals
- Claude Code: https://code.claude.com/docs/en/best-practices , https://code.claude.com/docs/en/hooks
- Kapoor et al.: https://arxiv.org/html/2407.01502 ; tau-bench: https://arxiv.org/abs/2406.12045 ;
  agentic eval variance: https://arxiv.org/html/2602.07150v2 ; METR on mergeability:
  https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/
- Our data: eval/SCOREBOARD.md, eval/SCOREBOARD_V2.md, eval/heldout/AUDIT.md, eval/v3/ (REPOS, inventory, adjudication,
  runs, results.json, RESULTS.md, tool_usage.json, check_agreement.json), DECISIONS.md D1-D13.
