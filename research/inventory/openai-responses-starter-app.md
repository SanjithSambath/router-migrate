## openai/openai-responses-starter-app @ 0fae283f12

- Lang: TypeScript (Next.js ^15.2.3 App Router, React 18, zustand)
- Pkg manager: npm; lockfile `package-lock.json` (lockfileVersion 3). `openai` ^4.87.3 -> locked 4.89.0.
- Python: n/a (python_requires null, py3.9 verdict n/a)
- Node: no pin (no .nvmrc, no `engines`, no `packageManager`, no CI workflows). Next 15 implies Node >=18.18; @types/node ^20.
- Commands: `npm run lint` (next lint), `npm run build` (next build, type-checks), `npx tsc --noEmit` (strict tsconfig). No test script.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| app/api/turn_response/route.ts:15 | openai-node | `new OpenAI()` (per request) | none (implicit env OPENAI_BASE_URL) | env OPENAI_API_KEY (implicit) |
| app/api/vector_stores/create_store/route.ts:3 | openai-node | `new OpenAI()` (module) | none | env OPENAI_API_KEY (implicit) |
| app/api/vector_stores/upload_file/route.ts:2 | openai-node | `new OpenAI()` (module) | none | env OPENAI_API_KEY (implicit) |
| app/api/vector_stores/add_file/route.ts:3 | openai-node | `new OpenAI()` (module) | none | env OPENAI_API_KEY (implicit) |
| app/api/vector_stores/list_files/route.ts:3 | openai-node | `new OpenAI()` (module) | none | env OPENAI_API_KEY (implicit) |
| app/api/vector_stores/retrieve_store/route.ts:3 | openai-node | `new OpenAI()` (module) | none | env OPENAI_API_KEY (implicit) |
| app/api/container_files/content/route.ts:15 | raw-http-openai | `fetch` | hardcoded `https://api.openai.com/v1` | `process.env.OPENAI_API_KEY` |

### Prod call sites (7)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| app/api/turn_response/route.ts:17 | openai-node | responses.create | yes | yes | hardcoded `'gpt-5.2'` (config/constants.ts:1 `MODEL`) |
| app/api/vector_stores/create_store/route.ts:8 | openai-node | vector_stores.create | no | yes | n/a |
| app/api/vector_stores/upload_file/route.ts:13 | openai-node | files.create (purpose `assistants`) | no | yes | n/a |
| app/api/vector_stores/add_file/route.ts:8 | openai-node | vector_stores.files.create | no | yes | n/a |
| app/api/vector_stores/list_files/route.ts:10 | openai-node | vector_stores.files.list | no | yes | n/a |
| app/api/vector_stores/retrieve_store/route.ts:9 | openai-node | vector_stores.retrieve | no | yes | n/a |
| app/api/container_files/content/route.ts:15 | raw-http-openai | GET /v1/containers/{cid}/files/{fid}/content or /v1/container-files/{fid}/content | no | yes | n/a |

By surface: responses.create x1 (the only LLM call), vector_stores.* x4, files.* x1, raw container-files GET x1.

### Non-prod call sites
None. No tests, examples or scripts. Other `fetch` calls (`app/api/functions/get_weather`, `get_joke`) hit non-LLM public APIs.

### Models seen
`gpt-5.2` (only model, single constant)

### Tests
- None. No test files, no test framework, no test script, no CI. test_cmd = null; mocking = none.
- Verification you can run without tests: `npm run lint`, `npx tsc --noEmit`, `npm run build`.

### Unusual / migration-relevant
- **responses.create (stream:true)** at turn_response/route.ts:17 uses `instructions`, `parallel_tool_calls:false`, and tools from `lib/tools/tools.ts:10 getTools()`:
  - `web_search` with an optional `user_location`
  - `file_search` with `vector_store_ids` (from a vector store the app created itself)
  - `code_interpreter` with `container:{type:'auto'}`
  - strict `function` tools (`get_weather`, `get_joke`)
  - user-configured remote `mcp` (server_url, `require_approval:'never'` optional, `allowed_tools`)
  - **OpenAI Google connectors** (`lib/tools/connectors.ts:4-21`: `type:'mcp'`, `connector_id: connector_googlecalendar / connector_gmail`, `authorization` = Google OAuth access token from openid-client)

  A router has to pass these hosted tools through unchanged.
- **SSE passthrough:** every Responses stream event is re-serialized to the browser (`data: {event,data}`) and handled in `lib/assistant.ts:159-560`. That handler relies on these event types and item types:
  - Events: `response.output_text.delta` / `annotation.added`, `output_item.added/done`, `function_call_arguments.delta/done`, `mcp_call_arguments.delta/done`, `web_search_call.completed`, `file_search_call.completed`, `code_interpreter_call_code.delta/done`, `code_interpreter_call.completed`, `response.completed`
  - Items inside `response.completed`: `mcp_list_tools` and `mcp_approval_request`
- **State:** there is no `previous_response_id` and no explicit `store`. The client keeps `conversationItems`, which holds the raw output items with server ids plus `function_call_output` and `mcp_approval_response` (components/assistant.tsx:39). It resends all of them as `input` every turn, so it depends on server-side item handling under the default store behavior.
- **Tool loop runs client-side:** function calls are executed in the browser via `/api/functions/*`, then the client re-POSTs to `/api/turn_response` (lib/assistant.ts:312-346).
- **Hardcoded OpenAI host** in the container-files download: it ignores any base-URL override and sends OPENAI_API_KEY straight to api.openai.com.
- **SDK version drift:** the tool types used (web_search, mcp connectors, code_interpreter container) are newer than the openai 4.89.0 typings. The tools array is loosely typed, so whether it typechecks is unverified.
- **Env mismatch:** `.env.example` sets `OAUTH_REDIRECT_URI`, but the code reads `GOOGLE_REDIRECT_URI` (lib/connectors-auth.ts:47; default `http://localhost:3000/api/google/callback`). No real keys are present.
