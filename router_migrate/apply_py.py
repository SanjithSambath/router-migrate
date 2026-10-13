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

    def leave_Call(self, original, updated):
        pos = self.get_metadata(PositionProvider, original).start
        key = (pos.line, pos.column)
        if key not in self.targets:
            return updated
        kind, opts = self.targets[key]
        kind = KIND_ALIAS.get(kind, kind)
        if key in self.already:
            self.done[key] = kind  # patched by an earlier run: counts as patched, nothing to do
            return updated
        if kind in ("wrap_openai", "wrap_anthropic", "wrap_langchain"):
            args = [cst.Arg(updated)]
            if (opts or {}).get("responses") is False and kind != "wrap_anthropic":
                args = [cst.Arg(updated, comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))), _kw("responses", "False")]
            self.done[key] = kind
            self.added.add(kind)
            return cst.Call(func=cst.Name(alias(kind)), args=args)
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
    if not used and not p.added and not todo_installs:
        return False, p.done, p.skipped, (runtime_rel if p.done or installs else None)
    block = import_block(dots, used) if used else ""
    for k in sorted(todo_installs):
        block += "%s()  # %s\n" % (alias(HELPERS[k][0]), MARK)
    if block:
        new_mod = _insert_after_imports(new_mod, block)
    open(path, "w", encoding="utf-8").write(new_mod.code)
    return True, p.done, p.skipped, runtime_rel
