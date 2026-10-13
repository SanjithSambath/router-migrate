# Site schema (shared by the Python and TypeScript scanners)

A scan emits JSON: {"root": str, "files_scanned": int, "sites": [Site], "errors": [{file, error}]}

Site = {
  "id":       "<lang>:<relpath>:<line>:<col>:<kind>",
  "lang":     "py" | "ts",
  "file":     relative path (posix), "line": 1-based, "col": 0-based,
  "kind":     "client"   (an SDK client / framework model is constructed: OpenAI(...), new Anthropic(), ChatOpenAI(...), createOpenAI(...))
            | "call"     (an API call: client.chat.completions.create(...), messages.stream(...), embeddings.create(...), generateText(...))
            | "module_client" (module-level SDK usage: openai.chat.completions.create with no client object)
            | "raw_http" (a string literal or fetch/requests call targeting api.openai.com / api.anthropic.com)
            | "env"      (reads OPENAI_API_KEY / OPENAI_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL ...),
  "sdk":      "openai" | "anthropic" | "azure-openai" | "langchain-openai" | "langchain-anthropic" | "llamaindex-openai"
            | "pydantic-ai" | "litellm" | "vercel-ai" | "vercel-ai-openai" | "vercel-ai-anthropic" | "vercel-ai-gateway"
            | "google-genai" | "mistral" | "groq" | "cohere" | "ollama" | "bedrock" | "other",
  "surface":  for calls: "chat.completions" | "chat.completions.parse" | "chat.completions.stream" | "responses" | "messages"
            | "messages.stream" | "messages.count_tokens" | "messages.beta" | "embeddings" | "images" | "audio" | "files"
            | "models" | "completions.legacy" | "assistants" | "vector_stores" | "framework.chat" | "framework.embeddings" | "other";
            for clients: the class/factory name (e.g. "OpenAI", "AsyncAnthropic", "ChatOpenAI", "createOpenAI"),
  "stream":   bool | null, "async": bool | null,
  "model":    {"value": str | null, "static": bool} | null,
  "base_url": {"value": str | null, "static": bool} | null   (clients only; null = SDK default / env),
  "receiver": source text of the call receiver (calls only), e.g. "self.client",
  "prod":     bool (false for tests/, examples/, docs/, notebooks, evals, benchmarks, cookbooks, scripts named test_*),
  "snippet":  the source line, trimmed to 160 chars
}
