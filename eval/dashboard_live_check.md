# Live dashboard check (`router-migrate up --no-browser`)

Run on a fresh clone of langchain-nextjs-template at the pinned commit, against the real stdlib server (router_migrate/dashboard/server.py) on 127.0.0.1:62606. Requests were made with http.client; the session token is not recorded.

| check | got | expected | result |
|---|---|---|---|
| GET / with token | `200` | `200` | PASS |
| page has all 5 tabs | `True` | `True` | PASS |
| GET / without token | `403` | `403` | PASS |
| GET / wrong token | `403` | `403` | PASS |
| GET /api/bundle without header | `403` | `403` | PASS |
| GET /api/bundle token only in query | `403` | `403` | PASS |
| foreign Host (DNS rebinding) | `403` | `403` | PASS |
| foreign Origin | `403` | `403` | PASS |
| oversize body (70 KB) | `413` | `413` | PASS |
| GET /api/bundle with header | `200` | `200` | PASS |
| bundle: Router setup checklist items >= 4 | `True` | `True` | PASS |
| bundle: unsupported sites >= 1 | `True` | `True` | PASS |
| POST /api/decide approve (same origin) | `200` | `200` | PASS |
| POST /api/decide dismiss without reason | `400` | `400` | PASS |
| POST /api/decide dismiss with reason | `200` | `200` | PASS |
| POST /api/apply | `200` | `200` | PASS |
| apply echoes the CLI command | `True` | `True` | PASS |
| decisions.json written by the UI | `[('approve', 'ui'), ('dismiss', 'ui')]` | `[('approve', 'ui'), ('dismiss', 'ui')]` | PASS |
| router.policy.json has the approved metadata | `lc` | `lc` | PASS |
| compiled routerPolicy.ts beside the runtime | `True` | `True` | PASS |
| bundle shows policy hash == compiled hash | `True` | `True` | PASS |

Live bundle: 17 cards, 5 Router setup checklist items, 3 unsupported sites (rendered by the Router setup and Unsupported tabs; screenshots in eval/screenshots/).
