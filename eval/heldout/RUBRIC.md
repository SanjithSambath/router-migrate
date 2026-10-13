# Held-out inventory rubric

Your job: inventory every LLM API call site in a repository and record what *should* happen to it in a migration onto Ramp's Router.
Work from the repository's source only.
- Do **not** read anything under `~/code/router-migrate/router_migrate/`, `eval/logs/` or `eval/heldout/logs/`, and never look at a
  `.router-migrate/` directory: this inventory must be independent of the tool being evaluated.
- You may read the Router model catalog (`~/code/router-migrate/router_migrate/data/catalog.json`, ids under `models`) and the measured
  dated-id equivalences (`router_migrate/data/dated_equivalents.json`). Those are facts about Router, not about the tool.

## What Router is (measured facts)
Router serves exactly three endpoints:
- `GET /v1/models`;
- `POST /v1/responses` (OpenAI Responses API);
- `POST /v1/messages` and `/v1/messages/count_tokens` (Anthropic Messages API).

There is no chat-completions, embeddings, images, audio, files, assistants, vector-store, moderation or legacy-completions endpoint.

How Router treats requests:
- It serves only the model ids in the catalog (plus the listed dated equivalences); any other model id is 404.
- It keeps no server-side state: `store: true` returns 400; `previous_response_id`, `conversation` and `responses.retrieve` don't work.
- It uses one Router API key per deployment, with no per-user keys.

## What counts as one site (counting unit)
- **Direct SDK call:** one site per expression. This covers `client.chat.completions.create/parse/stream`,
  `client.responses.create/stream/parse/retrieve`, `client.messages.create/stream/count_tokens`, `client.beta.messages.*`,
  `client.embeddings.create`, `client.images.*`, `client.audio.*`, `client.files.*`, `client.beta.assistants|threads.*`,
  `client.vector_stores.*`, `client.models.list` and `client.completions.create`.
  - A bound reference counts too (`create = client.chat.completions.create`).
- **LangChain / LlamaIndex:** one site per *chat-model or embeddings construction* (`ChatOpenAI(...)`, `ChatAnthropic(...)`,
  `OpenAIEmbeddings(...)`, llama_index `OpenAI(...)`). Do not count `.invoke()` / `.stream()` calls separately.
- **Vercel AI SDK:** one site per `generateText` / `streamText` / `generateObject` / `streamObject` / `embed` / `embedMany` call.
  Provider constructions (`createOpenAI(...)`, the default `openai` provider) are *not* sites when calls exist.
- **LiteLLM:** one site per `completion` / `acompletion` / `embedding` call.
- **Other vendor SDKs:** one site per generation call, e.g. `google.genai`, `mistralai`, `cohere`, `groq`, `ollama`, `boto3`
  bedrock-runtime `invoke_model` / `converse`.
- **Raw HTTP:** one site per request (`requests`/`httpx`/`fetch`/`axios`) to `api.openai.com` or `api.anthropic.com` generation
  endpoints. Also count raw HTTP to other LLM vendors' endpoints.
- **prod:** set `prod: false` for tests, examples, demos, notebooks, eval/benchmark harnesses, docs, scripts not part of the shipped
  app, and vendored third-party code. Inventory them anyway.

## Expected outcome per production site (apply in this order)
1. **kept**: the surface has no Router endpoint: embeddings, images, audio, files, assistants/threads, vector stores, moderation,
   legacy completions, realtime.
2. **kept**: the request goes to another vendor's endpoint. That covers a `base_url`/`baseURL` to a non-OpenAI/Anthropic host
   (Groq, DeepSeek, OpenRouter, Together, Ollama, vLLM, LM Studio, a local server...), Azure OpenAI, Bedrock and Vertex, and
   also a base URL chosen from a user-defined object at runtime. Moving these would change vendor and model.
3. **kept**: stateful Responses use on that client (`previous_response_id`, `conversation`, `store=True`, `retrieve`/`input_items`),
   or `file_search` with vector store ids / `code_interpreter` with file ids.
4. **flagged**: the API key is per user or tenant, i.e. it comes from a request, user, profile or session object, or a per-call
   function parameter fed from one. Env vars or app config do not count.
5. **flagged**: the call sends Anthropic beta features (`betas=`, `anthropic-beta` header, `client.beta.messages`).
6. **flagged**: the model is a literal (or a constant) that is not a Router catalog id or listed dated equivalence. Also flagged:
   the model is chosen at runtime and *none* of the repo's model-name literals for that provider family are in the catalog.
7. **handoff**: another gateway (Vercel AI Gateway, Cloudflare AI gateway...), raw HTTP to api.openai.com / api.anthropic.com
   generation endpoints, or another vendor's SDK where a model it uses is in Router's catalog (e.g. a `google.genai` call with a
   Gemini model id that is in the catalog).
8. **migrated**: everything else on OpenAI chat-completions/responses or Anthropic messages (directly or via LangChain, LlamaIndex,
   the AI SDK or LiteLLM) that uses the vendor's default endpoint. That includes an endpoint taken from env/config that defaults to
   the vendor, with the operator's key, and a model that is a catalog id or chosen at runtime.

## Output
Write JSON to `~/code/router-migrate/eval/heldout/inventory/<name>.json`:
```json
{"repo": "owner/name", "commit": "<sha>", "sites": [
  {"file": "path/from/repo/root.py", "line": 42, "prod": true, "sdk": "openai|anthropic|langchain-openai|langchain-anthropic|vercel-ai|llamaindex|litellm|google-genai|raw-http|other:<name>",
   "surface": "chat|responses|messages|embeddings|images|audio|files|assistants|vector_stores|models|completions|other",
   "endpoint": "default|env-config|other:<host or vendor>|azure|bedrock|vertex|gateway:<name>|runtime-user-defined|unknown",
   "key_source": "env|config|per-user|literal|none|unknown", "model": {"value": "gpt-4o-mini or null", "static": true},
   "beta": false, "stateful_responses": false, "expected": "migrated|kept|flagged|handoff|nonprod", "why": "<short reason, citing the rule number>"}
]}
```
Use the line of the call expression or construction, i.e. where `create(`, `ChatOpenAI(`, `generateText(` or `fetch(` starts.
Use `expected: "nonprod"` for non-production sites.
