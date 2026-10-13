"""Python scanner: finds LLM SDK clients, API call sites, raw HTTP and env usage with libcst (a real parser)."""
import os
import re

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider, ParentNodeProvider

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", ".env", "site-packages", "build", "dist", "__pycache__", ".tox",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".idea", ".vscode", "vendor", "third_party", ".eggs"}
NONPROD_PARTS = {"tests", "test", "testing", "examples", "example", "docs", "doc", "notebooks", "notebook", "benchmarks",
                 "benchmark", "evals", "eval", "evaluation", "cookbooks", "cookbook", "samples", "sample", "demo", "demos",
                 "playground", "auto_eval", "fixtures", "__tests__", "e2e"}

# qualified constructor -> (sdk, surface label)
CLIENTS = {
    "openai.OpenAI": ("openai", "OpenAI"), "openai.AsyncOpenAI": ("openai", "AsyncOpenAI"),
    "openai.AzureOpenAI": ("azure-openai", "AzureOpenAI"), "openai.AsyncAzureOpenAI": ("azure-openai", "AsyncAzureOpenAI"),
    "anthropic.Anthropic": ("anthropic", "Anthropic"), "anthropic.AsyncAnthropic": ("anthropic", "AsyncAnthropic"),
    "anthropic.AnthropicBedrock": ("bedrock", "AnthropicBedrock"), "anthropic.AsyncAnthropicBedrock": ("bedrock", "AsyncAnthropicBedrock"),
    "anthropic.AnthropicVertex": ("other", "AnthropicVertex"), "anthropic.AsyncAnthropicVertex": ("other", "AsyncAnthropicVertex"),
    "langchain_openai.ChatOpenAI": ("langchain-openai", "ChatOpenAI"), "langchain_openai.chat_models.ChatOpenAI": ("langchain-openai", "ChatOpenAI"),
    "langchain_community.chat_models.ChatOpenAI": ("langchain-openai", "ChatOpenAI"),
    "langchain.chat_models.ChatOpenAI": ("langchain-openai", "ChatOpenAI"),
    "langchain_openai.AzureChatOpenAI": ("azure-openai", "AzureChatOpenAI"),
    "langchain_openai.OpenAIEmbeddings": ("langchain-openai", "OpenAIEmbeddings"),
    "langchain_anthropic.ChatAnthropic": ("langchain-anthropic", "ChatAnthropic"),
    "llama_index.llms.openai.OpenAI": ("llamaindex-openai", "llama_index.OpenAI"),
    "pydantic_ai.providers.openai.OpenAIProvider": ("pydantic-ai", "OpenAIProvider"),
    "groq.Groq": ("groq", "Groq"), "groq.AsyncGroq": ("groq", "AsyncGroq"),
    "mistralai.Mistral": ("mistral", "Mistral"), "mistralai.client.MistralClient": ("mistral", "MistralClient"),
    "google.genai.Client": ("google-genai", "genai.Client"), "cohere.Client": ("cohere", "cohere.Client"),
    "cohere.ClientV2": ("cohere", "cohere.ClientV2"), "ollama.Client": ("ollama", "ollama.Client"),
    "ollama.AsyncClient": ("ollama", "ollama.AsyncClient"), "together.Together": ("other", "Together"),
    "zhipuai.ZhipuAI": ("other", "ZhipuAI"),
}
SDK_MODULES = ("openai", "anthropic", "langchain_openai", "langchain_anthropic", "langchain_community", "langchain", "llama_index",
               "pydantic_ai", "litellm", "groq", "mistralai", "google.genai", "google.generativeai", "cohere", "ollama", "together",
               "zhipuai", "dashscope", "boto3")
# trailing method chains -> surface (with_raw_response / with_streaming_response stripped first)
SURFACES = [
    ("beta.threads.messages.create", "assistants"), ("beta.threads.messages.list", "assistants"), ("beta.threads.runs.create", "assistants"),
    ("beta.chat.completions.parse", "chat.completions.parse"), ("beta.chat.completions.stream", "chat.completions.stream"),
    ("chat.completions.create", "chat.completions"), ("chat.completions.parse", "chat.completions.parse"),
    ("chat.completions.stream", "chat.completions.stream"),
    ("responses.create", "responses"), ("responses.stream", "responses"), ("responses.parse", "responses"),
    ("responses.retrieve", "responses.retrieve"),
    ("beta.messages.create", "messages.beta"), ("beta.messages.stream", "messages.beta"), ("beta.messages.count_tokens", "messages.beta"),
    ("messages.create", "messages"), ("messages.stream", "messages.stream"), ("messages.count_tokens", "messages.count_tokens"),
    ("embeddings.create", "embeddings"),
    ("images.generate", "images"), ("images.edit", "images"), ("images.create_variation", "images"),
    ("audio.transcriptions.create", "audio"), ("audio.translations.create", "audio"), ("audio.speech.create", "audio"),
    ("files.create", "files"), ("files.list", "files"), ("files.content", "files"), ("files.retrieve", "files"), ("files.delete", "files"),
    ("models.list", "models"), ("models.retrieve", "models"),
    ("completions.create", "completions.legacy"),
    ("beta.assistants", "assistants"), ("beta.threads", "assistants"), ("vector_stores", "vector_stores"),
]
# other providers' generation/embedding methods (counted so coverage has an honest denominator)
OTHER_SURFACES = [
    ("models.generate_content_stream", "other.chat"), ("models.generate_content", "other.chat"), ("models.embed_content", "other.embeddings"),
    ("generate_content", "other.chat"), ("send_message", "other.chat"), ("send_message_stream", "other.chat"),
    ("chat.complete", "other.chat"), ("chat.stream", "other.chat"), ("chat.complete_async", "other.chat"),
    ("converse", "other.chat"), ("converse_stream", "other.chat"), ("invoke_model", "other.chat"),
    ("invoke_model_with_response_stream", "other.chat"),
]
OTHER_SDKS = {"google-genai", "mistral", "cohere", "ollama", "bedrock", "other", "groq"}
LEGACY_V0 = {"openai.ChatCompletion.create": "chat.completions", "openai.ChatCompletion.acreate": "chat.completions",
             "openai.Completion.create": "completions.legacy", "openai.Embedding.create": "embeddings"}
FRAMEWORK_CALLS = {
    "litellm.completion": ("litellm", "chat.completions"), "litellm.acompletion": ("litellm", "chat.completions"),
    "litellm.embedding": ("litellm", "embeddings"), "litellm.aembedding": ("litellm", "embeddings"),
    "litellm.responses": ("litellm", "responses"), "litellm.text_completion": ("litellm", "completions.legacy"),
}
ENV_NAMES = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_ORG_ID",
             "OPENAI_ORGANIZATION", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"}
RAW_HOSTS = ("api.openai.com", "api.anthropic.com")


def is_prod(rel):
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    if name.startswith(("test_", "eval_", "bench_")) or name.endswith(("_test.py", "_eval.py")) or name == "conftest.py" or "benchmark" in name:
        return False
    return not any(p.lower() in NONPROD_PARTS for p in parts[:-1])


def dotted(node):
    """a.b.c for Name/Attribute chains (calls in the chain are rendered as name())."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        head = dotted(node.value)
        return (head + "." if head else "") + node.attr.value
    if isinstance(node, cst.Call):
        head = dotted(node.func)
        return (head + "()") if head else ""
    if isinstance(node, cst.Subscript):
        return dotted(node.value) + "[]"
    return ""


def _strip_raw(chain):
    return chain.replace(".with_raw_response", "").replace(".with_streaming_response", "")


class _Visitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider, ParentNodeProvider)

    def __init__(self, rel, source_lines, module):
        self.rel, self.lines, self.module = rel, source_lines, module
        self.imports = {}           # local name -> qualified
        self.sites = []
        self.assign_targets = {}    # id(Call node) -> target text
        self.consts = {}            # module/class level NAME = "literal"
        self.sdk_imported = False
        self.mods = set()
        self.aliases = {}           # name -> SDK namespace chain, e.g. messages_client -> client.messages
        self.locals = [{}]          # per-function: local name -> assigned expression node (to resolve base_url=base_url)
        self.in_import = 0           # top-level modules imported (for provider-specific method names)
        self.func_stack = []
        self.await_depth = 0

    # ---- imports
    def leave_Import(self, node):
        self.in_import -= 1

    def leave_ImportFrom(self, node):
        self.in_import -= 1

    def visit_Import(self, node):
        self.in_import += 1
        for a in node.names:
            name = dotted(a.name)
            local = a.asname.name.value if a.asname else name.split(".")[0]
            self.imports[local] = name if a.asname else name.split(".")[0]
            self.mods.add(name)
            if name.startswith(SDK_MODULES):
                self.sdk_imported = True

    def visit_ImportFrom(self, node):
        self.in_import += 1
        if node.module is None or isinstance(node.names, cst.ImportStar):
            return
        mod = dotted(node.module)
        self.mods.add(mod)
        if mod.startswith(SDK_MODULES):
            self.sdk_imported = True
        for a in node.names:
            n = a.name.value if isinstance(a.name, cst.Name) else dotted(a.name)
            local = a.asname.name.value if a.asname else n
            self.imports[local] = mod + "." + n

    def qualify(self, chain):
        head, _, rest = chain.partition(".")
        q = self.imports.get(head.rstrip("()"))
        if q is None:
            return chain
        return q + ("." + rest if rest else "")

    # ---- context
    def visit_FunctionDef(self, node):
        self.func_stack.append((node.name.value, node.asynchronous is not None))
        self.locals.append({})

    def leave_FunctionDef(self, node):
        self.func_stack.pop()
        self.locals.pop()

    def visit_Await(self, node):
        self.await_depth += 1

    def leave_Await(self, node):
        self.await_depth -= 1

    def visit_Attribute(self, node):
        if not self.sdk_imported or self.in_import:
            return
        parent = self.get_metadata(ParentNodeProvider, node, None)
        if isinstance(parent, cst.Attribute) or (isinstance(parent, cst.Call) and parent.func is node):
            return  # part of a longer chain, or a direct call (handled in visit_Call)
        chain = dotted(node)
        if chain.count(".") >= 2:
            self._match_surface(node, chain, False, called=False)

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name):
            self.locals[-1][node.targets[0].target.value] = node.value
        if isinstance(node.value, cst.Attribute) and len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name):
            chain = dotted(node.value)
            if chain.endswith((".messages", ".beta.messages", ".chat.completions", ".chat", ".responses", ".embeddings", ".completions")):
                self.aliases[node.targets[0].target.value] = self._expand(chain)
        if isinstance(node.value, cst.Call) and len(node.targets) == 1:
            self.assign_targets[id(node.value)] = self.module.code_for_node(node.targets[0].target).strip()
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name) and isinstance(node.value, cst.SimpleString):
            self.consts[node.targets[0].target.value] = node.value.evaluated_value

    def visit_AnnAssign(self, node):
        if isinstance(node.value, cst.Call):
            self.assign_targets[id(node.value)] = self.module.code_for_node(node.target).strip()
        if isinstance(node.target, cst.Name) and isinstance(node.value, cst.SimpleString):
            self.consts[node.target.value] = node.value.evaluated_value

    # ---- helpers
    def _pos(self, node):
        p = self.get_metadata(PositionProvider, node)
        return p.start.line, p.start.column

    def _kw(self, call, name):
        for a in call.args:
            if a.keyword is not None and a.keyword.value == name:
                return a.value
        return None

    def _strval(self, node):
        if node is None:
            return None
        if isinstance(node, cst.SimpleString):
            try:
                return {"value": node.evaluated_value, "static": True}
            except Exception:
                return {"value": None, "static": False}
        if isinstance(node, cst.Name) and node.value in self.consts:
            return {"value": self.consts[node.value], "static": True}
        if isinstance(node, cst.Name) and node.value in self.locals[-1] and self.locals[-1][node.value] is not node:
            inner = self.locals[-1][node.value]
            if isinstance(inner, cst.SimpleString):
                return self._strval(inner)
            return {"value": self.module.code_for_node(inner)[:300], "static": False}
        return {"value": self.module.code_for_node(node)[:300], "static": False}

    def _site(self, node, kind, sdk, surface, **extra):
        line, col = self._pos(node)
        snippet = self.lines[line - 1].strip()[:160] if 0 < line <= len(self.lines) else ""
        site = {"id": "py:%s:%d:%d:%s" % (self.rel, line, col, kind), "lang": "py", "file": self.rel, "line": line, "col": col,
                "kind": kind, "sdk": sdk, "surface": surface, "stream": extra.pop("stream", None), "async": extra.pop("async_", None),
                "model": extra.pop("model", None), "base_url": extra.pop("base_url", None), "receiver": extra.pop("receiver", None),
                "prod": is_prod(self.rel), "snippet": snippet, "function": self.func_stack[-1][0] if self.func_stack else "<module>"}
        site.update(extra)
        self.sites.append(site)

    # ---- calls
    def visit_Call(self, node):
        chain = dotted(node.func)
        if not chain:
            return
        q = self.qualify(chain)
        is_async = self.await_depth > 0 or (self.func_stack[-1][1] if self.func_stack else False)
        # client constructors
        if q in CLIENTS:
            sdk, label = CLIENTS[q]
            bu = None
            for k in ("base_url", "openai_api_base", "api_base", "azure_endpoint"):
                v = self._kw(node, k)
                if v is not None:
                    bu = self._strval(v)
                    break
            self._site(node, "client", sdk, label, base_url=bu, target=self.assign_targets.get(id(node)), async_=label.startswith("Async"),
                       model=self._strval(self._kw(node, "model") or self._kw(node, "model_name")))
            return
        if q in LEGACY_V0:
            self._site(node, "module_client", "openai-v0", LEGACY_V0[q], model=self._strval(self._kw(node, "model") or self._kw(node, "engine")),
                       stream=_truthy(self._kw(node, "stream")), async_=is_async, receiver="openai")
            return
        if q in FRAMEWORK_CALLS:
            sdk, surface = FRAMEWORK_CALLS[q]
            self._site(node, "module_client", sdk, surface, model=self._strval(self._kw(node, "model")), stream=_truthy(self._kw(node, "stream")),
                       async_=is_async, receiver=chain.split(".")[0])
            return
        if q.startswith("pydantic_ai.Agent") and node.args and isinstance(node.args[0].value, cst.SimpleString):
            mv = self._strval(node.args[0].value)
            self._site(node, "call", "pydantic-ai", "framework.chat", model=mv, receiver="Agent")
            return
        if not self.sdk_imported:
            return
        self._match_surface(node, chain, is_async, called=True)

    def _expand(self, chain):
        head, dot, rest = chain.partition(".")
        return self.aliases[head] + dot + rest if head in self.aliases else chain

    def _match_surface(self, node, chain, is_async, called):
        chain = self._expand(chain)
        stripped = _strip_raw(chain)
        q = self.qualify(stripped)
        if q != stripped and not q.startswith(("openai", "anthropic")):
            return  # a module path (e.g. mem0.vector_stores.x), not an SDK client chain
        for ns, surface in (("beta.threads", "assistants"), ("beta.assistants", "assistants"), ("vector_stores", "vector_stores")):
            if (".%s." % ns) in stripped or stripped.startswith(ns + "."):
                receiver = stripped.split("." + ns)[0] if ("." + ns) in stripped else ""
                self._site(node, "call", "openai", surface, model=None, stream=False, async_=is_async, receiver=receiver, bound_reference=not called)
                return
        for suffix, surface in SURFACES:
            if stripped.endswith("." + suffix) or stripped == suffix or ("." + suffix + ".") in stripped and suffix in ("beta.assistants", "beta.threads", "vector_stores"):
                receiver = stripped[: -len(suffix) - 1] if stripped.endswith("." + suffix) else stripped.split("." + suffix)[0]
                if surface == "completions.legacy" and receiver.endswith(".chat"):
                    continue
                recv_q = self.qualify(receiver) if receiver else ""
                kind = "module_client" if recv_q in ("openai", "anthropic") else "call"
                sdk = "anthropic" if surface.startswith("messages") or recv_q.startswith("anthropic") else "openai"
                kw = (lambda k: self._kw(node, k)) if called else (lambda k: None)
                stream = (_truthy(kw("stream")) if called else None) or surface in ("messages.stream", "chat.completions.stream") or suffix.endswith(".stream")
                self._site(node, kind, sdk, surface, model=self._strval(kw("model")), stream=bool(stream), async_=is_async,
                           receiver=receiver, has_prev_response=kw("previous_response_id") is not None,
                           has_store=kw("store") is not None, bound_reference=not called)
                return
        other_ok = any(m.startswith(("google", "vertexai", "mistralai", "boto3", "botocore", "cohere", "ollama", "zhipuai", "dashscope"))
                       for m in self.mods)
        for suffix, surface in OTHER_SURFACES:
            if other_ok and stripped.endswith("." + suffix):
                receiver = stripped[: -len(suffix) - 1]
                if not receiver or receiver in ("self", "cls"):
                    return
                self._site(node, "call", "other", surface, model=self._strval(self._kw(node, "model") or self._kw(node, "modelId")) if called else None,
                           stream="stream" in suffix, async_=is_async, receiver=receiver, bound_reference=not called)
                return

    # ---- raw http + env
    def visit_SimpleString(self, node):
        try:
            v = node.evaluated_value
        except Exception:
            return
        if isinstance(v, str) and any(h in v for h in RAW_HOSTS) and len(v) < 300:
            surface = "chat.completions" if "chat/completions" in v else "messages" if "/messages" in v else "other"
            self._site(node, "raw_http", "anthropic" if "anthropic" in v else "openai", surface, receiver=None, url=v)
        if isinstance(v, str) and v in ENV_NAMES:
            self._site(node, "env", "anthropic" if v.startswith("ANTHROPIC") else "openai", v)


def _truthy(node):
    if node is None:
        return None
    if isinstance(node, cst.Name):
        return True if node.value == "True" else False if node.value == "False" else None
    return None


def scan_file(path, rel):
    src = open(path, encoding="utf-8", errors="replace").read()
    if not any(k in src for k in ("openai", "anthropic", "litellm", "langchain", "llama_index", "pydantic_ai", "groq", "mistral",
                                  "genai", "cohere", "ollama", "OPENAI_", "ANTHROPIC_", "zhipuai", "together")):
        return []
    mod = cst.parse_module(src)
    v = _Visitor(rel, src.splitlines(), mod)
    MetadataWrapper(mod, unsafe_skip_copy=True).visit(v)
    if not v.sdk_imported:
        v.sites = [s for s in v.sites if s["kind"] in ("raw_http", "env") or s["sdk"] in ("litellm", "pydantic-ai", "openai-v0")]
    return v.sites


def iter_py_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") and not x.endswith(".egg-info"))
        for f in sorted(files):
            if f.endswith(".py"):
                p = os.path.join(d, f)
                if os.path.getsize(p) <= 1_000_000:
                    yield p


def scan(root):
    root = os.path.abspath(root)
    sites, errors, n = [], [], 0
    for p in iter_py_files(root):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        n += 1
        try:
            sites.extend(scan_file(p, rel))
        except Exception as e:  # syntax the parser rejects (e.g. Python 2 files): reported, never silently dropped
            errors.append({"file": rel, "error": "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else "")})
    _link_clients(sites)
    return {"root": root, "files_scanned": n, "sites": sites, "errors": errors}


_SELF = re.compile(r"^(self|cls)\.")


def _call_sdk(client_sdk, guessed):
    if client_sdk in ("langchain-openai", "llamaindex-openai"):
        return guessed
    return client_sdk


def _link_clients(sites):
    """Attach each call to the client construction it most likely uses (same file first, then repo-wide by attribute name)."""
    clients = [s for s in sites if s["kind"] == "client"]
    by_file = {}
    for c in clients:
        by_file.setdefault(c["file"], []).append(c)
    for s in sites:
        if s["kind"] != "call" or not s.get("receiver"):
            continue
        recv = s["receiver"]
        same = by_file.get(s["file"], [])
        cand = [c for c in same if c.get("target") and (c["target"] == recv or _SELF.sub("", c["target"]) == _SELF.sub("", recv))]
        if not cand and recv.endswith("()"):
            cand = [c for c in same if c["line"] == s["line"]]  # OpenAI().chat.completions.create(...)
        if not cand:
            tail = _SELF.sub("", recv).split(".")[-1]
            cand = [c for c in clients if c.get("target") and _SELF.sub("", c["target"]).split(".")[-1] == tail]
            if len(cand) > 1:
                cand = [c for c in cand if c["sdk"] in (s["sdk"], "azure-openai", "langchain-openai")] or cand
        if len(cand) == 1:
            s["client_id"] = cand[0]["id"]
            s["client_link"] = "same-file" if cand[0]["file"] == s["file"] else "by-name"
            s["sdk"] = _call_sdk(cand[0]["sdk"], s["sdk"])
        elif cand:
            s["client_candidates"] = [c["id"] for c in cand]
            s["client_link"] = "ambiguous"
            sdks = sorted({c["sdk"] for c in cand})
            if len(sdks) == 1:
                s["sdk"] = _call_sdk(sdks[0], s["sdk"])
        else:
            s["client_link"] = "unresolved"
