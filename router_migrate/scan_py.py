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
    ("responses.retrieve", "responses.retrieve"), ("responses.input_items.list", "responses.input_items"),
    ("responses.cancel", "responses.cancel"), ("responses.delete", "responses.delete"),
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
        self.fn_nodes = []          # enclosing FunctionDef nodes (parallel to func_stack)
        self.fn_params = []         # parameter names of each enclosing function
        self.class_stack = []
        self.mod_values = {}        # module/class level NAME = <expression node> (to resolve tools=TOOLS etc.)
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
    def visit_ClassDef(self, node):
        self.class_stack.append(node.name.value)

    def leave_ClassDef(self, node):
        self.class_stack.pop()

    def visit_FunctionDef(self, node):
        self.func_stack.append((node.name.value, node.asynchronous is not None))
        self.fn_nodes.append(node)
        ps = node.params
        names = {p.name.value for p in list(ps.posonly_params) + list(ps.params) + list(ps.kwonly_params)}
        for st in (ps.star_arg, ps.star_kwarg):
            if isinstance(st, cst.Param):
                names.add(st.name.value)
        self.fn_params.append(names - {"self", "cls"})
        self.locals.append({})

    def leave_FunctionDef(self, node):
        self.func_stack.pop()
        self.fn_nodes.pop()
        self.fn_params.pop()
        self.locals.pop()

    def _scope(self):
        """Enclosing function, qualified by class and def line: two `client` locals in different functions are different variables."""
        if not self.fn_nodes:
            return "<module>"
        line = self._pos(self.fn_nodes[-1])[0]
        return "%s%s:%d" % ((self.class_stack[-1] + ".") if self.class_stack else "", self.func_stack[-1][0], line)

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
        if len(node.targets) == 1:
            tgt = self.module.code_for_node(node.targets[0].target).strip()
            for call in _value_calls(node.value):  # x = OpenAI(), x = client or OpenAI(), x = OpenAI() if a else Azure()
                self.assign_targets[id(call)] = tgt
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name) and isinstance(node.value, cst.SimpleString):
            self.consts[node.targets[0].target.value] = node.value.evaluated_value
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name) and not self.fn_nodes:
            self.mod_values[node.targets[0].target.value] = node.value

    def visit_AnnAssign(self, node):
        if node.value is not None:
            for call in _value_calls(node.value):
                self.assign_targets[id(call)] = self.module.code_for_node(node.target).strip()
        if isinstance(node.target, cst.Name) and node.value is not None:
            if self.fn_nodes:
                self.locals[-1][node.target.value] = node.value
            else:
                self.mod_values[node.target.value] = node.value
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
                "prod": is_prod(self.rel), "snippet": snippet, "function": self.func_stack[-1][0] if self.func_stack else "<module>",
                "scope": self._scope(), "class": self.class_stack[-1] if self.class_stack else None}
        site.update(extra)
        if kind == "module_client":  # module-level usage routes only through the runtime's install hook in this file
            src = "\n".join(self.lines)
            site["installed"] = "install_litellm()" in src or "install_openai_module()" in src
        self.sites.append(site)

    def _router_wrapped(self, node):
        """True when this constructor already goes through the vendored runtime: `_router_wrap_x(Ctor(...))`,
        `router_runtime.wrap_x(Ctor(...))`, or `Ctor(http_client=_router_http_client())`."""
        par = self.get_metadata(ParentNodeProvider, node, None)
        if isinstance(par, cst.Arg):
            gp = self.get_metadata(ParentNodeProvider, par, None)
            if isinstance(gp, cst.Call):
                f = gp.func
                name = f.value if isinstance(f, cst.Name) else f.attr.value if isinstance(f, cst.Attribute) else ""
                if name.startswith("_router_wrap_") or name in ("wrap_openai", "wrap_anthropic", "wrap_langchain"):
                    return True
        for a in node.args:
            if a.keyword is not None and a.keyword.value in ("http_client", "async_http_client") and isinstance(a.value, cst.Call):
                f = a.value.func
                name = f.value if isinstance(f, cst.Name) else f.attr.value if isinstance(f, cst.Attribute) else ""
                if name.startswith("_router_") or name in ("http_client", "async_http_client"):
                    return True
        return False

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
                       model=self._strval(self._kw(node, "model") or self._kw(node, "model_name")),
                       api_key=self._api_key_info(self._kw(node, "api_key") or self._kw(node, "openai_api_key") or self._kw(node, "anthropic_api_key")),
                       wrapped=self._router_wrapped(node))
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
                lit = self._static_kwargs(node) if called else {}
                splat = lit.pop("__splat_keys__", [])
                has = lambda k: kw(k) is not None or k in splat
                self._site(node, kind, sdk, surface, model=self._strval(kw("model")), stream=bool(stream), async_=is_async,
                           receiver=receiver, has_prev_response=has("previous_response_id"),
                           has_store=has("store"), store_true=lit.get("store") is True, has_conversation=has("conversation"),
                           has_background=lit.get("background") is True, has_beta=self._has_beta(node, receiver, splat) if called else False,
                           beta_split=self._beta_split(node, receiver) if surface.startswith("messages") and "beta" not in surface else False,
                           uses_provider_files=self._uses_provider_files(node) if called else False,
                           static_kwargs=lit, receiver_from=self._receiver_from(receiver), bound_reference=not called)
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


    # ---- per-call static facts (H1 beta, H2 provider files, H5 replay kwargs, M6 per-tenant keys, L3 receiver origin)
    def _resolve(self, node, depth=0):
        """A Name bound in this function (or at module level) -> its assigned expression; anything else unchanged."""
        if isinstance(node, cst.Name) and depth < 3:
            inner = self.locals[-1].get(node.value) if self.fn_nodes else None
            if inner is None:
                inner = self.mod_values.get(node.value)
            if inner is not None and inner is not node:
                return self._resolve(inner, depth + 1)
        return node

    def _expr_text(self, node, depth=0):
        """Source of an expression plus the sources of the local/module names it mentions (2 levels), for keyword sniffing."""
        if node is None:
            return ""
        parts = [self.module.code_for_node(node)]
        if depth < 2:
            seen = set()
            for nm in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", parts[0]):
                if nm in seen:
                    continue
                seen.add(nm)
                inner = (self.locals[-1].get(nm) if self.fn_nodes else None) or self.mod_values.get(nm)
                if inner is not None:
                    parts.append(self._expr_text(inner, depth + 1))
        return "\n".join(parts)

    def _static_kwargs(self, call):
        """JSON-able literal kwargs of a call (non-literal values skipped), incl. literal entries of a **local_dict splat.
        '__splat_keys__' lists every key seen in splatted dict literals (literal or not)."""
        out, splat_keys = {}, []
        for a in call.args:
            if a.keyword is not None:
                k = a.keyword.value
                if k in ("messages", "input", "model", "stream", "prompt"):
                    continue
                ok, v = _literal(self._resolve(a.value))
                if ok and len(_json_len(v)) <= 4000:
                    out[k] = v
            elif a.star == "**":
                d = self._resolve(a.value)
                if isinstance(d, cst.Dict):
                    for el in d.elements:
                        if isinstance(el, cst.DictElement) and isinstance(el.key, cst.SimpleString):
                            k = el.key.evaluated_value
                            splat_keys.append(k)
                            if k in ("messages", "input", "model", "stream", "prompt") or k in out:
                                continue
                            ok, v = _literal(self._resolve(el.value))
                            if ok and len(_json_len(v)) <= 4000:
                                out[k] = v
                elif isinstance(d, cst.Call) and dotted(d.func) == "dict":
                    splat_keys += [x.keyword.value for x in d.args if x.keyword is not None]
        if splat_keys:
            out["__splat_keys__"] = splat_keys
        return out

    def _has_beta(self, call, receiver, splat):
        """Anthropic beta: betas= kwarg, or extra_headers carrying an anthropic-beta header (literal or built in this function)."""
        if self._kw(call, "betas") is not None or "betas" in splat:
            return True
        eh = self._kw(call, "extra_headers")
        txt = self._expr_text(eh) if eh is not None else ""
        if "extra_headers" in splat:
            for a in call.args:
                if a.star == "**":
                    txt += "\n" + self._expr_text(a.value)
        if "anthropic-beta" in txt.lower():
            return True
        if (eh is not None or "extra_headers" in splat) and self.fn_nodes:
            # headers assembled in this function (merged dicts, helpers): a literal anthropic-beta anywhere in it counts
            return "anthropic-beta" in self.module.code_for_node(self.fn_nodes[-1]).lower()
        return False

    def _beta_split(self, call, receiver):
        """`if betas: client.beta.messages.create(...) else: client.messages.create(...)`: which branch a client takes is decided at
        runtime, and the planner cannot tell that the plain branch is reachable for a first-party Anthropic client."""
        node = call
        for _ in range(12):
            node = self.get_metadata(ParentNodeProvider, node, None)
            if node is None or isinstance(node, (cst.FunctionDef, cst.Module)):
                return False
            if isinstance(node, cst.If):
                return (receiver + ".beta.messages.") in self.module.code_for_node(node)
        return False

    def _uses_provider_files(self, call):
        """file_search over vector_store_ids or code_interpreter over file ids: state that lives on the provider's account."""
        tools = self._kw(call, "tools")
        txt = self._expr_text(tools) if tools is not None else ""
        for a in call.args:
            if a.star == "**":
                txt += "\n" + self._expr_text(a.value)
        return bool(("file_search" in txt and "vector_store_ids" in txt) or ("code_interpreter" in txt and "file_ids" in txt))

    def _receiver_from(self, receiver):
        """`client = self.get_client(key)` then `client.chat...`: the factory the receiver came from ('get_client')."""
        if not receiver:
            return None
        if receiver.endswith("()"):
            return receiver[:-2].split(".")[-1]
        if "." in receiver or not self.fn_nodes:
            return None
        v = self.locals[-1].get(receiver)
        if isinstance(v, cst.Await):
            v = v.expression
        if isinstance(v, cst.Call):
            name = dotted(v.func)
            return name.split(".")[-1] if name else None
        return None

    def _api_key_info(self, node):
        """Classify a client's api_key expression. per_tenant only when clearly per-request: a parameter of the enclosing (non-constructor)
        function, an attribute/item of a parameter or of a request/user/profile/tenant object, or a *_for_user()-style lookup."""
        if node is None:
            return None
        text = self.module.code_for_node(node)[:200]
        resolved = self._resolve(node)
        params = self.fn_params[-1] if self.fn_params else set()
        ctor = bool(self.func_stack) and self.func_stack[-1][0] in ("__init__", "__new__", "__post_init__")
        why = None
        for op in _operands(resolved):
            if isinstance(op, (cst.SimpleString, cst.ConcatenatedString, cst.FormattedString)):
                continue
            src = self.module.code_for_node(op)
            if re.search(r"\b(os\.environ|getenv|environ\b|env\[|settings[.\[]|config[.\[]|conf[.\[]|cfg[.\[]|CONFIG|SETTINGS)", src):
                continue
            base = op
            while isinstance(base, (cst.Attribute, cst.Subscript)):
                base = base.value
            if isinstance(base, cst.Call):  # lookup(...) / lookup(...)["key"] / lookup(...).api_key
                fn = dotted(base.func).split(".")[-1].lower()
                if _TENANT_CALL.search(fn):
                    why = "per-tenant lookup %s()" % fn
                    break
                continue
            root = _root_name(op)
            if root is None or root in ("self", "cls"):
                continue
            if root.lower() in _TENANT_NAMES:
                why = "per-request object '%s'" % root
                break
            if root in params and (not ctor or isinstance(op, (cst.Attribute, cst.Subscript))):
                why = "function parameter '%s'" % root
                break
        return {"value": text, "static": isinstance(resolved, cst.SimpleString), "per_tenant": why is not None, "why": why}

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


_TENANT_NAMES = {"profile", "user", "current_user", "request", "req", "tenant", "account", "customer", "org", "organization", "workspace",
                 "team", "member", "owner", "user_settings", "user_profile", "tenant_config"}
_TENANT_CALL = re.compile(r"(_for_|_by_|_of_)(user|tenant|org|organization|account|profile|customer|workspace|team)|"
                          r"^(get_)?(user|tenant|profile|org|customer|account|workspace)_\w*(key|secret|cred|setting)")


def _value_calls(v):
    """Calls an assignment's value can evaluate to: the value itself, operands of `or`/`and`, both arms of `x if c else y`."""
    if isinstance(v, cst.Call):
        f = v.func
        name = f.value if isinstance(f, cst.Name) else f.attr.value if isinstance(f, cst.Attribute) else ""
        if (name.startswith("_router_wrap_") or name in ("wrap_openai", "wrap_anthropic", "wrap_langchain")) and v.args:
            return [v] + _value_calls(v.args[0].value)  # x = _router_wrap_openai(OpenAI()): the target belongs to the constructor too
        return [v]
    if isinstance(v, cst.BooleanOperation):
        return _value_calls(v.left) + _value_calls(v.right)
    if isinstance(v, cst.IfExp):
        return _value_calls(v.body) + _value_calls(v.orelse)
    if isinstance(v, cst.Await):
        return _value_calls(v.expression)
    return []


def _operands(v):
    if isinstance(v, cst.BooleanOperation):
        return _operands(v.left) + _operands(v.right)
    if isinstance(v, cst.IfExp):
        return _operands(v.body) + _operands(v.orelse)
    return [v]


def _root_name(v):
    while isinstance(v, (cst.Attribute, cst.Subscript)):
        v = v.value
    return v.value if isinstance(v, cst.Name) else None


def _json_len(v):
    import json
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return "x" * 10000


def _literal(node):
    """(True, python value) for JSON-able literals: strings, numbers, True/False/None, lists/tuples/dicts of literals."""
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            v = node.evaluated_value
            return (True, v) if isinstance(v, str) else (False, None)
        except Exception:
            return False, None
    if isinstance(node, cst.Integer):
        return True, int(node.evaluated_value)
    if isinstance(node, cst.Float):
        return True, float(node.evaluated_value)
    if isinstance(node, cst.Name) and node.value in ("True", "False", "None"):
        return True, {"True": True, "False": False, "None": None}[node.value]
    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Minus):
        ok, v = _literal(node.expression)
        return (True, -v) if ok and isinstance(v, (int, float)) and not isinstance(v, bool) else (False, None)
    if isinstance(node, (cst.List, cst.Tuple)):
        out = []
        for el in node.elements:
            if not isinstance(el, cst.Element):
                return False, None
            ok, v = _literal(el.value)
            if not ok:
                return False, None
            out.append(v)
        return True, out
    if isinstance(node, cst.Dict):
        out = {}
        for el in node.elements:
            if not isinstance(el, cst.DictElement):
                return False, None
            ok, k = _literal(el.key)
            if not ok or not isinstance(k, str):
                return False, None
            ok, v = _literal(el.value)
            if not ok:
                return False, None
            out[k] = v
        return True, out
    return False, None


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
    """Attach each call to the client construction it uses. Same file only: a call on `self.client` is never linked to a client in
    another file just because the attribute names match (that linked ZhipuAI/DeepSeek calls to OpenAI clients). Calls that stay
    unresolved fall back to the planner's repo-wide rules (plan._classify), which apply the vendor-in-path check.

    Within a file:
      - attribute receivers (self.client) match clients assigned to the same attribute, same class preferred;
      - plain local names (client) match only clients assigned to that name in the same function (or at module level),
        nearest preceding assignment first;
      - a receiver produced by a factory (client = self.get_client(); self.get_client().x) matches clients built in that function.
    """
    by_file = {}
    for c in sites:
        if c["kind"] == "client":
            by_file.setdefault(c["file"], []).append(c)
    for s in sites:
        if s["kind"] != "call" or not s.get("receiver"):
            continue
        cand = _same_file_candidates(s, by_file.get(s["file"], []))
        if len(cand) > 1:
            cand = _narrow(s, cand)
        if len(cand) == 1:
            s["client_id"] = cand[0]["id"]
            s["client_link"] = "same-file"
            s["sdk"] = _call_sdk(cand[0]["sdk"], s["sdk"])
        elif cand:
            s["client_candidates"] = [c["id"] for c in cand]
            s["client_link"] = "ambiguous"
            sdks = sorted({c["sdk"] for c in cand})
            if len(sdks) == 1:
                s["sdk"] = _call_sdk(sdks[0], s["sdk"])
        else:
            s["client_link"] = "unresolved"


def _same_file_candidates(s, same):
    recv = s["receiver"]
    norm = _SELF.sub("", recv)
    if recv.endswith("()"):
        cand = [c for c in same if c["line"] == s["line"]]  # OpenAI().chat.completions.create(...)
        if cand:
            return cand
    elif "." in recv or _SELF.match(recv):
        # attribute receiver: self.client / self.llm.client / app.state.client
        cand = [c for c in same if c.get("target") and _SELF.sub("", c["target"]) == norm]
        same_class = [c for c in cand if c.get("class") == s.get("class")]
        return same_class or cand
    else:
        # plain name: a local of this function, else a module-level name
        named = [c for c in same if c.get("target") == recv]
        local = [c for c in named if c.get("scope") == s.get("scope")]
        if local:
            before = [c for c in local if c["line"] <= s["line"]]
            return before or local
        mod = [c for c in named if c.get("scope") == "<module>"]
        if mod:
            return mod
    src = s.get("receiver_from")
    if src:
        made = [c for c in same if c.get("function") == src]
        if made:
            return made
    return []


def _narrow(s, cand):
    """Several same-file candidates. Same SDK family: pick the one matching the call's sync/async-ness, else the nearest preceding
    assignment. Different SDKs (OpenAI vs AzureOpenAI branches) stay ambiguous: the planner reports both."""
    if len({_call_family(c["sdk"]) for c in cand}) > 1:
        return cand
    want_async = bool(s.get("async"))
    by_async = [c for c in cand if bool(str(c.get("surface", "")).startswith("Async")) == want_async]
    if len(by_async) == 1:
        return by_async
    pool = by_async or cand
    if len({c.get("scope") for c in pool}) == 1:
        before = [c for c in pool if c["line"] <= s["line"]]
        if before:
            return [max(before, key=lambda c: c["line"])]
    return cand


def _call_family(sdk):
    return {"langchain-openai": "openai", "llamaindex-openai": "openai", "azure-openai": "azure", "langchain-anthropic": "anthropic"}.get(sdk, sdk)


MODEL_LITERAL = re.compile(r"""["'`]((?:claude-[a-z0-9][\w.\-]*)|(?:chatgpt-[\w.\-]+)|(?:gpt-[0-9][\w.\-]*)|(?:o[1345](?:-(?:mini|pro|preview)[\w.\-]*)?))["'`]""")
NON_CHAT_MODEL = re.compile(r"(tts|transcribe|image|realtime|audio|whisper|embedding|dall-e)", re.I)
LITERAL_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def collect_model_literals(root, max_files=20000):
    """Model-name string literals in production files (Python and TS/JS), by family: {"anthropic": {...}, "openai": {...}}.
    Used by the planner when a call's model is chosen at runtime: if the repo names models of that family and none of them
    is in Router's catalog, 'model chosen at runtime => migrated' is not a claim the planner can make (H1)."""
    out = {"anthropic": set(), "openai": set()}
    n = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") and not x.endswith(".egg-info"))
        for f in files:
            if not f.endswith(LITERAL_EXTS) or f.endswith((".d.ts", ".min.js")):
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if not is_prod(rel) or os.path.basename(rel).endswith((".test.ts", ".spec.ts", ".test.js", ".spec.js", ".test.tsx")):
                continue
            if f in ("router_runtime.py", "routerRuntime.ts", "router_policy.py", "routerPolicy.ts"):
                continue  # router-migrate's own vendored files embed the whole catalog; they are not the repo's model names
            n += 1
            if n > max_files:
                return {k: sorted(v) for k, v in out.items()}
            try:
                if os.path.getsize(p) > 1_000_000:
                    continue
                src = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in MODEL_LITERAL.finditer(src):
                name = m.group(1)
                if NON_CHAT_MODEL.search(name):
                    continue
                out["anthropic" if name.startswith("claude-") else "openai"].add(name)
    return {k: sorted(v) for k, v in out.items()}
