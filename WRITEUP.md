# router-migrate: write-up

**For:** Kedar Thakkar, Router team.

## The problem

Router's value grows with the share of a customer's LLM traffic that goes through it. Getting that traffic there is a code
migration, and most of it is judgment, not syntax. Where does this `base_url` point in production? Is this the operator's
key or a user's? Is `seed=0` a contract or a habit? Does anyone wait on this call? Rules guess these and overfit. Agents
answer them well, but they also make confident wrong moves, and they can make tests pass by deleting what the tests cover.

So the job is to move as much traffic as possible **and** keep every existing behavior, with an honest account of each.

## The result, in your two metrics

On 10 open-source repos with 64 movable production LLM calls (one run per approach). "Moved" means the call sends a valid
Router request, not just that it was wrapped.

<!-- numbers: WS4 -->
| | Share of traffic moved | Behavior kept |
|---|---|---|
| Router's public docs prompt | 13 / 64 | 1 repo broken; on a held-out repo it deleted the Azure provider and key config while tests passed |
| v4 (migrate by default, per-item proof) | 21 / 64 | 0 broken, 0 wrong moves; 180 min, $17.79 |
| D3 (the core of v5) | 17 / 64 verified (scorer under-counts; being fixed) | 0 broken, 0 wrong moves; 71 min, $9.87 |
| **v5** | _pending_ | _pending_ |

## The design, in five bullets

- **Code owns facts and arithmetic.** Does Router serve this request? Does this call route as the code stands? What does
  Flex save here? A route check runs every call through the real runtime against a local Router simulator.
- **The agent owns judgment, and the method.** It runs in your own Claude Code session (`/router-migrate`), reads the
  code, edits however it likes, and self-tests offline. It takes near-lossless trade-offs by default (drop `seed`, dated
  snapshot to current id, operator key only) and records what each one loses.
- **Humans own trade-offs.** Flex on user-facing calls, fallbacks, a different model and vendor ports are cards on a
  dashboard. Every agent trade-off there has an Undo.
- **One outside check the agent can't influence.** Parse, scope (no test, CI, lockfile or runtime edits, no deletions,
  provider keys kept), tests with Router on, no invalid Router requests, and route truth per call: verified, unverified
  or stays.
- **The runtime decides per request and always falls back.** Router when it can serve the request exactly, the provider
  otherwise. Inert without `RAMP_ROUTER_API_KEY`. `ROUTER_MIGRATE_DISABLE=1` turns it off.

## What we learned

- **Wrapped is not moved.** AI-Scientist's `seed=0` kept its OpenAI calls off Router under every earlier scorer.
  Config-driven base URLs did the same. We now count requests, not edits.
- **Tools remove wrong moves; prompts don't.** Every arm with fact-computing tools made 0 wrong moves. A detailed prompt
  with all our rules still made wrong moves.
- **Process was the cost.** v4 required a claim, a proof and a gate pass per item. Dropping those (D3) kept the same
  judgment at about 2.5× the speed and 55% of the cost. Large test suites were the one real cost; v5 runs affected tests
  first and the full suite once, in the background.
- **Tests don't catch destructive edits.** gpt-cli's tests passed after its Azure provider was deleted. A scope check does.
- **It found a real bug.** sec-insights deep-copies its LlamaIndex LLM, which crashed with Router on. Every version since
  v1 had shipped it. Fixed in the runtime.

## Limits

- One run per approach. Variance is large (one repo moved 4 calls in one run and 0 in another).
- The dev set was used to tune v3 to v5. Held-out results are partial (10 of 30 runs).
- Ground-truth inventories were written by an agent, blind, against a rubric.
- Everything ran offline against router-sim, which enforces Router's measured contract but is not Router.
- Clients built dynamically can be migrated but only verified when a sandboxed run reaches them.

## Next steps

1. Finish the held-out set and run k=3, so differences between approaches are measurable.
2. Verify dynamic clients: run client factories under the repo's example config against router-sim, so more
   "unverified" moves become "verified".
3. Router-side changes that would move more traffic with less code: a Chat Completions surface (or an official
   adapter), server-side Responses state, Flex eligibility in `/v1/models`, and aliases for dated model ids.

Docs: [README](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Methodology](docs/METHODOLOGY.md) ·
[Eval](docs/EVAL.md) · [Levers](docs/LEVERS.md)
