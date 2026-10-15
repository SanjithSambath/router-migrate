# Methodology

How router-migrate decides who does what, and why. The system is described in [ARCHITECTURE.md](ARCHITECTURE.md); the
numbers are in [EVAL.md](EVAL.md).

## The problem

Moving an app's LLM traffic onto Router has two goals that pull against each other:

1. **Move as much traffic as possible.** Every call left on a provider is spend Router cannot see, tag, cache or route.
2. **Keep every existing behavior.** Tests, keys, features, latency and fallbacks must survive.

Most of the work is judgment. Where does this `base_url` point in production? Whose key is this? Is `seed=0` a contract or
a habit? Is a user waiting on this call? Rules guess these from syntax and overfit. An agent can read the config, the
callers and the docs, and answer them.

But an agent can also be wrong, confidently. It can also make tests pass by deleting what they test. So the design question
is: **which parts can we verify, and who should own the rest?**

## First principles

1. **Constrain outcomes, not process.** Check what the migration produced: parses, tests, valid Router requests, scope. Do
   not script how the agent gets there.
2. **The agent's autonomy scales with what can be verified.** Where code can check the result, the agent is free. Where it
   cannot (cost, latency, a different model), a human decides.
3. **Migrate by default, and state every cost.** If a call can move at a cost a reasonable engineer would accept, move it
   and write down what is lost. Leave it only when Router physically cannot serve it, or the loss is the product.
4. **Facts and arithmetic from code. Judgment from the agent. Trade-offs from humans.** Each actor owns what it is best at
   and nothing else.
5. **The runtime decides per request, and always falls back safely.** Only the request boundary knows every fact. Without
   `RAMP_ROUTER_API_KEY` the runtime does nothing.
6. **One outside check the agent cannot influence.** It re-derives everything from the tree. It trusts no claim.
7. **No destructive edits.** Tests, provider keys and features stay. A migration that passes by deleting things has failed.
8. **Everything is reversible.** Every trade-off has an Undo. `ROUTER_MIGRATE_DISABLE=1` turns it all off.

## Who decides what

| Decision | Code | Agent | Human |
|---|---|---|---|
| Is this an LLM call; which surface? | finds what the AST sees | finds what it misses (factories, provider tables, dynamic constructors) | |
| Does Router serve this surface, model, parameter? | **decides** (catalog, measured facts) | | |
| Does the call route as the code stands? | **decides** (real runtime + router-sim) | | |
| Where config points in production; whose key; is it production code | | **decides**, from code and config | |
| The edit | exact codemods on request | **decides** the method; edits by hand where tools can't reach | |
| Near-lossless trade-offs (drop `seed`, dated id, operator key, perf beta, same-model vendor endpoint) | records, compiles, offers Undo | **takes** them | can undo |
| Latency class and task of each call | shows them on the dashboard | **judges** | |
| Prices, Flex availability, compatible fallbacks, cache threshold | **computes** | | |
| Which improvements to propose | facts and hints (`levers`) | **builds** them as branches, with reasons | |
| Flex on user-facing calls, fallbacks, a different model, vendor ports | checks each branch | builds | **decides** |
| Did anything break? | **decides** (outside check) | can self-test any time | |
| Per-request route in production | **decides** (runtime) | | |

### Why each actor owns its part

- **Code owns facts and arithmetic** because they have one right answer and code gets it every time. Asking an agent
  whether Router serves `gpt-4o-2024-05-13` invites a guess. The catalog says 404.
- **The agent owns judgment** because each judgment is a "read three files and decide" question with no fixed shape.
  Rules for these reached 89% precision on the repos they were built on, 53% on held-out repos and 30% on fresh ones.
- **Humans own trade-offs** because cost, latency and model behavior are product decisions. The agent can estimate a
  Flex saving. It cannot know whether a user will tolerate a minute's wait.
- **The runtime owns the request** because a call's fate can change per request (`n=2` once, a model from config), and
  because a safe fallback is only possible at the boundary.

## Prior art, and what we took from it

| Source | What it says | What we did |
|---|---|---|
| Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | Use the simplest thing that works. Agents fit open-ended problems where steps can't be predicted. Ground the agent in feedback from its environment. | Rules where the answer is fixed; one agent for the open-ended part; `check` as environment feedback. |
| Anthropic, [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Few, high-signal tools. Errors that say what to do next. | Five agent tools. Every "doesn't route" names the lever or hard limit that explains it. |
| Anthropic, [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Smallest set of high-signal tokens. Let the agent fetch the rest just in time. | Setup injects the call map and measured Router facts, not the codebase or our rules. |
| Anthropic, [SWE-bench with Claude 3.5 Sonnet](https://www.anthropic.com/research/swe-bench-sonnet) | A minimal scaffold (a prompt, a shell, an edit tool) and control left to the model. | v4's ordered protocol was replaced by a goal, facts, invariants and optional tools. |
| Anthropic, [Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk) | Gather context, take action, verify, repeat. | `check --run` lets the agent verify its own work against router-sim, offline, as often as it likes. |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | About 100 lines and bash alone are competitive on SWE-bench. | Scaffolding is not where the value is. We kept the tools that compute facts and dropped the ones that police process. |
| Cognition, [Don't build multi-agents](https://cognition.ai/blog/dont-build-multi-agents) | Parallel writers lose shared context and make conflicting decisions. | One session makes every edit. Sub-agents may investigate, never write. |
| Google, [AI for internal code migrations](https://arxiv.org/abs/2501.06972) | LLM edits paired with deterministic AST tooling and validation; humans review. | Codemods and the runtime are deterministic; the agent handles the variable part; the outside check validates. |
| Airbnb, [LLM-driven test migration](https://medium.com/airbnb-engineering/accelerating-large-scale-test-migration-with-llms-9565c208023b) | 3.5K files migrated by a pipeline of per-step validation and retries, tuned on samples. | Validation is mechanical (parse, tests, Router validity), with one retry on facts. |
| Amazon, Q Developer Java upgrades | An agent upgraded tens of thousands of production apps; each step builds and tests. | Same shape: agent changes, build-and-test gates them. |
| METR, [Recent frontier models are reward hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking/) | Capable models tamper with tests and scoring when they can. | The check runs outside the agent's reach. Test, CI and lockfile edits fail scope. |
| [ImpossibleBench](https://arxiv.org/abs/2510.20270) | Coding agents exploit test cases when the task cannot be done honestly, including by editing tests. | No-destructive-edits invariant. We saw it ourselves: the docs-prompt agent deleted gpt-cli's Azure provider and key config while tests still passed. |

## How we got here: v1 to v7

The hypothesis-by-hypothesis version of this history, with the v7 experiments: [EVOLUTION.md](EVOLUTION.md).

Each version answered the previous one's failure. Full specs are in [archive/docs/](../archive/docs/).

| Version | Idea | What it taught |
|---|---|---|
| **v1** ([SPEC](../archive/docs/SPEC.md), [DECISIONS](../archive/docs/DECISIONS.md), [SYSTEM_DESIGN](../archive/docs/SYSTEM_DESIGN.md)) | Deterministic: scan, plan, one-line wrap per client, a vendored runtime that translates at the HTTP boundary. | The runtime design is right: inert by default, per-request fallback, 0 of 15 repos broken. The planner is not: rules on judgments overfit (89% → 53% → 30% precision). |
| **v2** ([SPEC_V2](../archive/docs/SPEC_V2.md)) | Cards: unlocks, Flex, fallbacks, caching, spend tags, model recommendations; a compiled per-feature policy; a dashboard. | The policy mechanism is right. The inputs were heuristics (latency from filenames) and often wrong. Flex and fallbacks on one feature are either/or. |
| **v3** ([SPEC_V3](../archive/docs/SPEC_V3.md)) | An agent works the residue after a deterministic floor, with tools for facts and a gate. | Tools that compute facts remove wrong moves: 10 correct / 0 wrong, against 10 / 16 for the docs prompt. Encoding our rules as prompt text did not help (10 / 7). Pre-applying the floor added nothing (same result at 68% of the cost). A static oracle capped recall. |
| **v4** ([METHODOLOGY v4](../archive/docs/METHODOLOGY.md)) | Migrate by default; trade-offs taken with `adapt` and shown with Undo; the agent produces its own evidence; per-item claims; a Stop-hook gate. | Moved the most traffic (21 of 51 on the 9 repos it finished, 0 wrong, 0 broken) but slowly: 229 minutes, $24.69, one timeout. The pilot found a production `deepcopy` crash every earlier version had shipped. |
| **D3** (bridge) | Facts plus optional tools plus one outside check. No per-item claims, no mandatory proof, no gate loop. | 18 of 64 verified and 28 one approval away, 0 wrong, 0 broken, 0 destructive; $9.87, 71 minutes: about 3× faster at the same judgment. Process obligations, not tests, were v4's slowdown. |
| **v5** | D3 inside the user's own Claude Code session; the agent proposes improvements; automatic lossless levers; the agent picks its own tests; the outside check runs the full suite once, Router on, in the background when slow. | Migration worked on a real repo (agent.email), but Improve produced nothing: the rules engine needed the model at the call site, the app kept it in config behind a wrapper, and the agent had no way to hand over what it understood. Code was still deciding things that take understanding. |
| **v6** ( [plan](plans/v6-agent-first.md)) | Agent-forward: the agent migrates, reviews the system and builds improvements as git branches, and later applies and undoes what the human asks; code keeps only facts, a sandbox, live Router checks with a spend-capped key, the outside check and the runtime. Edit-category bans become outcome checks and flags; every decision is a commit; one item format for the whole dashboard. | See [EVAL.md](EVAL.md) and `eval/v6/`. |
| **v7** (this; [plan](plans/v7-flagship.md)) | The review is the product, the migration is the gate: a 15-line prompt makes the agent the product's CTO with nothing out of scope; recommendations carry level, who acts (code, Router setting, product call) and measured numbers; Router settings are the human's step; the fact sheet is re-measured with `facts --refresh`; prompt, fact and tool changes ship through a blind head-to-head release gate. | `eval/lean/LOG.md`, `eval/gate/`. |

### The findings that shaped v5 and v6

- **Wrapped is not moved.** Earlier scorers counted a wrapped client as migrated. It isn't, if its requests never reach
  Router. AI-Scientist's `seed=0` kept its OpenAI calls on OpenAI under every earlier scorer. Config-driven base URLs and
  ambiguous clients (several clients, one call) did the same. v5 reports route truth per call: **verified**, **unverified**
  or **stays**.
- **Destructive passes are invisible to tests.** On gpt-cli, the agent following Router's public docs prompt deleted the
  provider-key config and the Azure provider. Its 14 tests passed before and after. Only a scope check that knows what
  must survive catches this.
- **Process was the cost, not verification.** v4 required a claim per item, proof per claim and a gate loop. D3 dropped
  all three and kept the same judgment at about 3× the speed. The exception was large suites (325 s per run), which v5
  handles by leaving in-loop testing to the agent and running the full suite once, Router on, in the background.
- **Rules can't hand over understanding (v6).** On agent.email the model names lived in a config file and reached the call
  through a wrapper. The rules engine saw "model unknown" and stayed silent, and the report had no field for what the
  agent knew. v6 makes improvement the agent's work, with code checking facts, not finding opportunities.
- **Edit bans block good migrations (v6).** "Don't edit lockfiles" and "don't delete files" would have reverted a port from
  Anthropic server tools to Responses-style web search. v6 judges outcomes (tests, typecheck, valid requests, nothing
  lost without saying so) and flags the rest for the human.
- **Real bugs hide behind "tests pass".** The v4 pilot found that sec-insights deep-copies its LlamaIndex LLM. With Router
  on, our HTTP client could not be deep-copied, and the call crashed. Every version since v1 had shipped it. The runtime's
  clients are now `deepcopy`-safe, and a crash in the Router-on run is a failure, never a baseline.

- **Procedure in the prompt hurts; facts and a wide mandate help (v6, lean experiments).** Blind head-to-head runs on
  five repos (`eval/lean/LOG.md`): a 55-line CTO prompt beat the 150-line v6 prompt; a 10-line prompt beat that 5-0; one
  line allowing unbuilt, levelled proposals beat that 4-1 at lower cost; widening the mandate to the whole product with
  every Router surface ("nothing is out of scope") beat that 5-0. Removing the fact sheet, the scanner map, or the
  `policy` / `levers` tools each made results worse. The default prompt is now the 15-line open-scope one.

## Where natural language appears

Only in four places:

1. The agent's instructions: the goal, measured Router facts, hard limits, invariants and the tools. No ordered rules,
   no recipes.
2. The agent's `why` and `loses` text. Stored and shown to humans, never parsed for decisions.
3. Tool output, including the fix for each call that doesn't route.
4. Card text on the dashboard.

Everything that decides pass or fail is structured: outcome enums, `FILE:LINE` locations, router-sim verdicts, test counts.
