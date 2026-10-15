# router-migrate: write-up

**For:** Kedar Thakkar, Router team.

## The problem

Router's value grows with the share of a customer's LLM traffic that goes through it, and with how well that traffic
uses Router once it's there. Getting it there is a code migration, and most of it is judgment, not syntax: where does
this `base_url` point in production, is this the operator's key or a user's, does anyone wait on this call? Rules guess
and overfit. Agents answer well but make confident wrong moves, and can make tests pass by deleting what they cover.
And moving the traffic is only half the value: the bigger win is what the product should become with every model, tier
and strategy Router offers.

## What router-migrate does

1. **Migrates, as the gate.** The agent moves every production call it can; an outside check it can't influence proves
   the app behaves the same (tests and typecheck against the baseline, requests Router accepts, which calls really reach
   Router).
2. **Reviews, as the product.** The agent reads the product as its CTO, measures on real Router with a spend-capped key,
   and ranks what to change: quick wins, contained changes and reworks, each saying who carries it out (code, a Router
   setting, or the human's call).
3. **Applies what you approve.** A human decides on a dashboard; an agent session builds or undoes it; the check runs
   again.

## The result

| Question | Evidence |
|---|---|
| Traffic moved, behaviour kept | 10 open-source repos, 64 movable calls: 0 broken, 0 wrong moves, 0 destructive edits; held-out 23 of 45 moved. Router's docs prompt: 1 repo broken, 54 destructive edits. |
| Quality of the review | The 15-line open-scope prompt beat the ~150-line v6 prompt 5-0 on five held-out repos, blind, under two rubrics. |
| Depth | From about 1.7 improvements per repo (mostly model and cache tweaks) to 9-15 ranked recommendations per repo, including removing an inner agent, collapsing a two-level agent so answers stream sooner, and shadow models before a model change. |
| The loop | An approved, never-built rework was built by an apply session and passed the outside check. |

## What we learned

- **Wrapped is not moved.** We count requests Router accepts, not edits.
- **Tools beat prompts for facts.** Every arm that could ask "does Router accept this?" made zero wrong moves.
- **Cut procedure, keep facts, widen the mandate.** Every cut to procedure helped; every cut to the fact sheet, the map of
  calls, or the tools the agent uses hurt; giving it the whole product with nothing out of scope produced the CTO-level
  review.
- **Measure, don't estimate.** The recommendations that land carry numbers from real Router (cost per reply, cache hits,
  time to first words), with the sample size shown.
- **Write for the reader.** Correct recommendations written as jargon don't get acted on; the report format now carries a
  plain-language writing guide.

The full history, hypothesis by hypothesis: [docs/EVOLUTION.md](docs/EVOLUTION.md).

## Limits

- One run per arm and repo, judged by a Claude reviewer. The direction held across rounds and on held-out repos.
- Measured numbers come from small samples; answer quality after a model change isn't measured (filed as the human's
  call, with shadow models proposed).
- Router-side changes that would move more traffic with less code: a Chat Completions surface, server-side Responses
  state, Flex eligibility in `/v1/models`, aliases for dated model ids.

Docs: [README](README.md) · [Evolution](docs/EVOLUTION.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Eval](docs/EVAL.md) · [Router facts](router_migrate/data/ROUTER.md)
