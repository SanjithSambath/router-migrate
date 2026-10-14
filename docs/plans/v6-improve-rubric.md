# v6 Improve rubric (frozen before any v6 end-to-end run)

Written on 2026-10-14, before the first v6 run on any repository. Scores are assigned per repository by reading the
agent's MIGRATION.json, its improvement branches, validation.json and the dashboard. No expected answers are written down
for any repository; the reviewer judges each item on its own merits against the repository's code.

## Per improvement item (0-2 each)

| Criterion | 0 | 1 | 2 |
|---|---|---|---|
| **Correct** | The change doesn't do what it says, or breaks something the check didn't catch | Does what it says with a gap (an edge case, a missed call site) | Does exactly what it says |
| **Worth it** | No real gain, or the loss outweighs it | Small or uncertain gain | A clear gain a senior engineer would want considered |
| **Honest** | A claim (price, behaviour, evidence) is false | Claims are true but vague | Every claim is true and concrete; losses are stated; "what was checked" matches what ran |
| **Scoped** | Changes behaviour nobody asked to change, or bundles unrelated changes | Mostly focused | One decision per branch; behaviour changes are labelled as such |

## Per repository

| Criterion | 0 | 1 | 2 |
|---|---|---|---|
| **Coverage** | Missed an opportunity an expert would raise first (judged from the code after the run, before reading the agent's list) | Found the main one, missed others of similar value | Found what an expert would raise |
| **Reasoning when nothing is proposed** | Empty with no reason | A generic reason | A specific reason grounded in this code |
| **Evidence use** | No evidence beyond assertions | Simulator/tests only where live evidence was available | Live evidence (Router, side-by-side samples) where a key was available and it mattered |

## Migration (reported alongside, same as earlier evals)

Traffic moved (verified / unverified / stays), behaviour preserved (tests, typecheck, no capability lost without being
flagged), wrong moves, destructive edits, agent minutes and dollars.

## Apply and undo (end-to-end runs)

For each requested apply/undo: done as requested (2), done with a reasonable deviation explained (1), wrong or unexplained
(0); and whether the check after the session passed.

## Rules

- k = 1 per repository; stated as a limitation.
- The reviewer writes the "expert opportunities" for Coverage after the run but before reading the agent's items.
- Scores and a one-line justification per item go in `eval/v6/IMPROVE_SCORES.md`.
