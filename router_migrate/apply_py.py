"""Python codemods (libcst): patch client constructions so their traffic goes through the vendored router_runtime.

Only client-construction expressions are edited; call sites are never touched (some test suites assert exact call kwargs).
Every edit is a no-op at runtime unless RAMP_ROUTER_API_KEY is set. Re-running apply is idempotent.
"""
import os

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

MARK = "router-migrate"
RUNTIME_HEADER = "# router_runtime.py (vendored by router-migrate"
HELPERS = {
    "wrap_openai": ["wrap_openai"], "wrap_anthropic": ["wrap_anthropic"], "wrap_langchain": ["wrap_langchain"],
    "kwargs_llamaindex": ["http_client", "async_http_client"],
    "install_openai_module": ["install_openai_module"], "install_litellm": ["install_litellm"],
}
# LangChain chat models are wrapped after construction: their SDK clients get the transport in place, so every request is still
# planned per request (unknown models, beta headers -> provider). Planner kinds from older plans map onto that.
KIND_ALIAS = {"kwargs_langchain_openai": "wrap_langchain", "kwargs_chat_anthropic": "wrap_langchain"}
# what each helper becomes when the runtime cannot be imported from a module: the original behavior, exactly
STUBS = {"wrap_openai": "lambda c, *a, **k: c", "wrap_anthropic": "lambda c, *a, **k: c", "wrap_langchain": "lambda c, *a, **k: c",
         "http_client": "lambda **k: k.get('_default')", "async_http_client": "lambda **k: k.get('_default')",
         "install_openai_module": "lambda: None", "install_litellm": "lambda: None", "chat_anthropic_kwargs": "lambda: {}"}


CTOR_NAMES = ("OpenAI", "AsyncOpenAI", "Anthropic", "AsyncAnthropic")


def alias(name):
    return "_router_" + name


def _is_router_call(node):
    return isinstance(node, cst.Call) and isinstance(node.func, cst.Name) and node.func.value.startswith("_router_")


class _AlreadyPatched(cst.CSTVisitor):
    """Positions of constructor calls that an earlier run already wrapped (`_router_wrap_x(Ctor(...))`)."""
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self):
        self.pos = set()

    def visit_Call(self, node):
        if _is_router_call(node) and node.args and isinstance(node.args[0].value, cst.Call):
            p = self.get_metadata(PositionProvider, node.args[0].value).start
            self.pos.add((p.line, p.column))
        elif any(a.keyword is not None and a.keyword.value == "http_client" and _is_router_call(a.value) for a in node.args):
            p = self.get_metadata(PositionProvider, node).start
            self.pos.add((p.line, p.column))


def _kw(name, expr):
    eq = cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""), whitespace_after=cst.SimpleWhitespace(""))
    return cst.Arg(keyword=cst.Name(name), value=cst.parse_expression(expr), equal=eq)


class _Patcher(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, targets, already):
        self.targets = targets  # {(line, col): (patch kind, opts)}
        self.already = already
        self.done = {}
        self.skipped = {}
        self.added = set()  # helper kinds this run actually inserted
        self.native = False  # a native patch was made (the file then needs `import os`)

    @staticmethod
    def _wrap_kwargs(kind, opts):
        """Keyword args a wrap call carries. v1: only responses=False; v2 unlocks add unlock_hosts / drop_betas."""
        opts = opts or {}
        kws = []
        if opts.get("responses") is False and kind != "wrap_anthropic":
            kws.append(("responses", "False"))
        if opts.get("unlock_hosts") and kind == "wrap_openai":
            kws.append(("unlock_hosts", repr(sorted(opts["unlock_hosts"]))))
        if opts.get("drop_betas") and kind == "wrap_anthropic":
            kws.append(("drop_betas", repr(sorted(opts["drop_betas"]))))
        if opts.get("operator_key_only"):
            kws.append(("operator_key_only", "True"))
        return kws

    def _key(self, node):
        """Target key of a call: exact (line, col), or (line, None) for a client constructor on that line (v2 unlocks address
        constructors in already-patched files, where the column moved)."""
        pos = self.get_metadata(PositionProvider, node).start
        if (pos.line, pos.column) in self.targets:
            return (pos.line, pos.column)
        f = node.func
        name = f.value if isinstance(f, cst.Name) else f.attr.value if isinstance(f, cst.Attribute) else None
        if (pos.line, None) in self.targets and name in CTOR_NAMES:
            return (pos.line, None)
        return (pos.line, pos.column)

    def leave_Call(self, original, updated):
        key = self._key(original)
        if _is_router_call(original) and original.args and isinstance(original.args[0].value, cst.Call):
            ikey = self._key(original.args[0].value)
            if ikey in self.targets:  # an earlier run wrapped this constructor: add any v2 unlock option it lacks
                kind, opts = self.targets[ikey]
                kind = KIND_ALIAS.get(kind, kind)
                have = {a.keyword.value for a in updated.args if a.keyword is not None}
                extra = [kv for kv in self._wrap_kwargs(kind, opts) if kv[0] not in have and kv[0] != "responses"]
                if extra:
                    new = list(updated.args)
                    new[-1] = new[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                    for i, (k, v) in enumerate(extra):
                        arg = _kw(k, v)
                        if i < len(extra) - 1:
                            arg = arg.with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                        new.append(arg)
                    self.added.add(kind)
                    return updated.with_changes(args=new)
            return updated
        if key not in self.targets:
            return updated
        kind, opts = self.targets[key]
        kind = KIND_ALIAS.get(kind, kind)
        pos = self.get_metadata(PositionProvider, original).start
        if key in self.already or (pos.line, pos.column) in self.already:
            # patched by an earlier run: counts as patched (an unlock adds its option to that wrap, above), nothing to do
            self.done[key] = kind
            return updated
        if kind in ("wrap_openai", "wrap_anthropic", "wrap_langchain"):
            kws = self._wrap_kwargs(kind, opts)
            args = [cst.Arg(updated, comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")) if kws else cst.MaybeSentinel.DEFAULT)]
            for i, (k, v) in enumerate(kws):
                arg = _kw(k, v)
                if i < len(kws) - 1:
                    arg = arg.with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                args.append(arg)
            self.done[key] = kind
            self.added.add(kind)
            return cst.Call(func=cst.Name(alias(kind)), args=args)
        if kind in ("native_openai", "native_anthropic"):
            old = {a.keyword.value: a for a in updated.args if a.keyword is not None}
            if any(a.star for a in updated.args):
                self.skipped[key] = "constructor takes *args/**kwargs, which may carry base_url/api_key"
                return updated
            if "base_url" in old and "RAMP_ROUTER" in _code(old["base_url"].value):
                self.done[key] = kind  # already native
                return updated
            orig_base = _code(old["base_url"].value) if "base_url" in old else "None"
            orig_key = _code(old["api_key"].value) if "api_key" in old else "None"
            vals = {"base_url": native_base_expr(kind == "native_anthropic", orig_base),
                    "api_key": 'os.environ.get("RAMP_ROUTER_API_KEY") or %s' % orig_key}
            # existing kwargs are rewritten in place (so unwrap restores the file exactly); missing ones are appended
            new = [a.with_changes(value=cst.parse_expression(vals[a.keyword.value])) if a.keyword is not None and a.keyword.value in vals else a
                   for a in updated.args]
            missing = [k for k in ("base_url", "api_key") if k not in old]
            if missing and new and new[-1].comma == cst.MaybeSentinel.DEFAULT:
                new[-1] = new[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
            for i, k in enumerate(missing):
                arg = _kw(k, vals[k])
                if i < len(missing) - 1:
                    arg = arg.with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                new.append(arg)
            self.done[key] = kind
            self.native = True
            return updated.with_changes(args=new)
        if kind == "kwargs_llamaindex":
            names = {a.keyword.value for a in updated.args if a.keyword is not None}
            if "http_client" in names or "async_http_client" in names:
                self.skipped[key] = "client already sets http_client/async_http_client"
                return updated
            if any(a.star == "**" for a in updated.args):
                self.skipped[key] = "constructor takes **kwargs, which may already carry http_client"
                return updated
            new = list(updated.args)
            if new and new[-1].comma == cst.MaybeSentinel.DEFAULT:
                new[-1] = new[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
            new += [_kw("http_client", alias("http_client") + "()").with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
                    _kw("async_http_client", alias("async_http_client") + "()")]
            self.done[key] = kind
            self.added.add(kind)
            return updated.with_changes(args=new)
        self.skipped[key] = "unknown patch kind %s" % kind
        return updated


def _code(node):
    return cst.Module(body=[]).code_for_node(node)


NATIVE_ACTIVE = 'os.environ.get("RAMP_ROUTER_API_KEY") and not os.environ.get("ROUTER_MIGRATE_DISABLE")'


def native_base_expr(anthropic, orig):
    """base_url for a native client: Router's endpoint when RAMP_ROUTER_API_KEY is set (OpenAI SDKs take .../v1, the
    Anthropic SDK the bare host), else the original value. Evaluated when the client is constructed."""
    base = 'os.environ.get("RAMP_ROUTER_BASE_URL", "https://api.router.com").rstrip("/").removesuffix("/v1")'
    return "(%s%s if %s else %s)" % (base, "" if anthropic else ' + "/v1"', NATIVE_ACTIVE, orig)


class _Unwrapper(cst.CSTTransformer):
    """The inverse of every edit _Patcher makes, for constructions starting within `lines`: _router_wrap_x(Ctor(...), ...)
    -> Ctor(...); http_client=_router_http_client() kwargs removed; native base_url/api_key restored to their originals."""
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, lines):
        self.lines, self.done = lines, []

    def _at(self, node):
        return self.get_metadata(PositionProvider, node).start.line in self.lines

    def leave_Call(self, original, updated):
        if _is_router_call(original) and original.args and isinstance(original.args[0].value, cst.Call) and \
                (self._at(original) or self._at(original.args[0].value)) and original.func.value.startswith("_router_wrap_"):
            self.done.append(original.func.value)
            return updated.args[0].value
        if not self._at(original):
            return updated
        args, changed = [], False
        for a in updated.args:
            if a.keyword is not None and a.keyword.value in ("http_client", "async_http_client") and _is_router_call(a.value):
                changed = True
                continue
            if a.keyword is not None and a.keyword.value == "base_url" and "RAMP_ROUTER" in _code(a.value):
                orig = _code(a.value).rsplit(" else ", 1)[-1].rstrip(")").strip()
                changed = True
                if orig != "None":
                    args.append(a.with_changes(value=cst.parse_expression(orig)))
                continue
            if a.keyword is not None and a.keyword.value == "api_key" and "RAMP_ROUTER_API_KEY" in _code(a.value):
                orig = _code(a.value).split(" or ", 1)[-1].strip()
                changed = True
                if orig != "None":
                    args.append(a.with_changes(value=cst.parse_expression(orig)))
                continue
            args.append(a)
        if not changed:
            return updated
        if args:
            args[-1] = args[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        self.done.append("kwargs")
        return updated.with_changes(args=args)


class _DropRouterStatements(cst.CSTTransformer):
    """Remove the runtime import block and install-hook lines once nothing in the module uses the runtime any more."""

    def __init__(self, drop_hooks):
        self.drop_hooks = drop_hooks

    def leave_Try(self, original, updated):
        src = cst.Module(body=[original]).code
        if "router_runtime import" in src and MARK in src:
            return cst.RemovalSentinel.REMOVE
        return updated

    def leave_SimpleStatementLine(self, original, updated):
        if self.drop_hooks and len(original.body) == 1 and isinstance(original.body[0], cst.Expr) and _is_router_call(original.body[0].value) \
                and original.body[0].value.func.value.startswith("_router_install_"):
            return cst.RemovalSentinel.REMOVE
        return updated


def unwrap_file(root, rel, line, slack=2, hooks=False):
    """Undo router-migrate's edit at a construction starting near `line` (or the file's install hooks when hooks=True).
    Returns the list of edits undone ([] = nothing there). The import block goes when nothing uses the runtime any more."""
    path = os.path.join(root, rel)
    src = open(path, encoding="utf-8").read()
    mod = cst.parse_module(src)
    u = _Unwrapper(set(range(line - slack, line + slack + 1)))
    new = MetadataWrapper(mod).visit(u)
    if hooks:
        new = new.visit(_DropRouterStatements(drop_hooks=True))
        if new.code != mod.code:
            u.done.append("install hook")
    code = new.code
    rest = code
    for marker in ("router_runtime import",):
        rest = "\n".join(l for l in rest.splitlines() if marker not in l)
    import re as _re
    if not _re.search(r"_router_(wrap_|http_client|async_http_client|install_)\w*\(", "\n".join(
            l for l in code.splitlines() if " as _router_" not in l and "= lambda" not in l)):
        code = cst.parse_module(code).visit(_DropRouterStatements(drop_hooks=False)).code
    if "kwargs" in u.done and "import os  # %s" % MARK in code and not _re.search(r"\bos\.", "\n".join(
            l for l in code.splitlines() if not l.startswith("import os"))):
        code = "\n".join(l for l in code.split("\n") if not l.startswith("import os  # %s" % MARK))
    if code != src:
        open(path, "w", encoding="utf-8").write(code)
    return u.done


def runtime_location(root, rel_file):
    """(runtime path relative to root, import statement lines) for a file: package root if the file is in a package."""
    d = os.path.dirname(os.path.join(root, rel_file))
    pkg_dirs = []
    while os.path.exists(os.path.join(d, "__init__.py")) and os.path.abspath(d) != os.path.abspath(root):
        pkg_dirs.append(d)
        d = os.path.dirname(d)
    if pkg_dirs:
        top = pkg_dirs[-1]
        depth = len(pkg_dirs)  # number of package levels from the file's dir up to (and including) the top package
        return os.path.relpath(os.path.join(top, "router_runtime.py"), root), "." * depth
    return os.path.relpath(os.path.join(os.path.dirname(os.path.join(root, rel_file)), "router_runtime.py"), root), ""


def import_block(dots, names):
    """try package-relative -> plain module -> stubs. The last tier makes the import unable to fail (namespace packages, scripts
    inside packages): the module then behaves exactly as before the migration, and its calls stay on their provider."""
    dots = dots or "."

    def imp(ind):
        if len(names) == 1:
            return "%s as %s" % (names[0], alias(names[0]))
        return "(\n" + "".join("%s    %s as %s,\n" % (ind, n, alias(n)) for n in names) + ind + ")"
    stubs = "".join("        %s = %s  # noqa: E731\n" % (alias(n), STUBS[n]) for n in names)
    return ("try:  # %s: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set\n"
            "    from %srouter_runtime import %s\n"
            "except ImportError:  # %s: module executed as a script\n"
            "    try:\n"
            "        from router_runtime import %s\n"
            "    except ImportError:  # %s: runtime not importable from here; this module keeps its original behavior\n"
            "%s") % (MARK, dots, imp("    "), MARK, imp("        "), MARK, stubs)


def _insert_after_imports(module, stmts_src):
    new = cst.parse_module(stmts_src).body
    body = list(module.body)
    idx = 0
    for i, st in enumerate(body):
        if isinstance(st, cst.SimpleStatementLine) and all(isinstance(x, (cst.Import, cst.ImportFrom)) for x in st.body):
            idx = i + 1
        elif i == 0 and isinstance(st, cst.SimpleStatementLine) and isinstance(st.body[0], cst.Expr) and isinstance(st.body[0].value, cst.SimpleString):
            idx = 1  # module docstring
        elif isinstance(st, cst.If) or isinstance(st, cst.Try):
            # `try: import x / except ImportError` blocks count as imports
            src = cst.Module(body=[st]).code
            if "import" in src.split("\n", 1)[0] or src.lstrip().startswith(("try:", "if TYPE_CHECKING")):
                if all(l.strip().startswith(("import ", "from ", "try:", "except", "pass", "if ", "else:", "#")) or not l.strip() for l in src.splitlines()):
                    idx = i + 1
                    continue
            break
        else:
            break
    return module.with_changes(body=body[:idx] + list(new) + body[idx:])


def dotted_name(n):
    return n.value if isinstance(n, cst.Name) else (dotted_name(n.value) + "." + n.attr.value if isinstance(n, cst.Attribute) else "")


def _already_imported(src, name):
    return ("as %s" % alias(name)) in src or ("%s = " % alias(name)) in src


def runtime_conflict(root, runtime_rel):
    """A router_runtime.py that router-migrate did not write must never be overwritten."""
    p = os.path.join(root, runtime_rel)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8", errors="replace") as f:
        return not f.readline().startswith(RUNTIME_HEADER)


def patch_file(root, rel, targets, installs):
    """targets: {(line, col): kind or (kind, opts)}; installs: set of install kinds.
    Returns (changed, done, skipped, runtime_rel). Idempotent per constructor: a rerun counts earlier wraps as done and only
    patches what is new."""
    targets = {k: (v if isinstance(v, tuple) else (v, None)) for k, v in targets.items()}
    path = os.path.join(root, rel)
    src = open(path, encoding="utf-8").read()
    runtime_rel, dots = runtime_location(root, rel)
    if runtime_conflict(root, runtime_rel):
        return False, {}, {k: "%s already exists and is not router-migrate's" % runtime_rel for k in targets}, None
    mod = cst.parse_module(src)
    wrapper = MetadataWrapper(mod)
    seen = _AlreadyPatched()
    wrapper.visit(seen)
    p = _Patcher(targets, seen.pos)
    new_mod = wrapper.visit(p)
    todo_installs = {k for k in installs if "%s()" % alias(HELPERS[k][0]) not in src}
    used = sorted({h for k in p.added for h in HELPERS[k]} | {h for k in todo_installs for h in HELPERS[k]})
    used = [h for h in used if not _already_imported(src, h)]
    if p.native:  # native clients need no runtime: only `import os` for the env reads
        if not any(isinstance(st, cst.SimpleStatementLine) and any(isinstance(x, cst.Import) and any(dotted_name(n.name) == "os" for n in x.names)
                                                                      for x in st.body) for st in new_mod.body):
            new_mod = _insert_after_imports(new_mod, "import os  # %s: native Router endpoint\n" % MARK)
        if not used and not todo_installs:
            open(path, "w", encoding="utf-8").write(new_mod.code)
            return True, p.done, p.skipped, None
    if not used and not p.added and not todo_installs:
        return False, p.done, p.skipped, (runtime_rel if p.done or installs else None)
    block = import_block(dots, used) if used else ""
    for k in sorted(todo_installs):
        block += "%s()  # %s\n" % (alias(HELPERS[k][0]), MARK)
    if block:
        new_mod = _insert_after_imports(new_mod, block)
    open(path, "w", encoding="utf-8").write(new_mod.code)
    return True, p.done, p.skipped, runtime_rel
