"""Python codemods (libcst): patch client constructions so their traffic goes through the vendored router_runtime.

Only client-construction expressions are edited; call sites are never touched (some test suites assert exact call kwargs).
Every edit is a no-op at runtime unless RAMP_ROUTER_API_KEY is set. Re-running apply is idempotent.
"""
import os

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

MARK = "router-migrate"
HELPERS = {
    "wrap_openai": ["wrap_openai"], "wrap_anthropic": ["wrap_anthropic"],
    "kwargs_langchain_openai": ["http_client", "async_http_client"], "kwargs_llamaindex": ["http_client", "async_http_client"],
    "kwargs_chat_anthropic": ["chat_anthropic_kwargs"], "install_openai_module": ["install_openai_module"],
    "install_litellm": ["install_litellm"],
}


def alias(name):
    return "_router_" + name


class _Patcher(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, targets):
        self.targets = targets  # {(line, col): patch kind}
        self.done = {}
        self.skipped = {}

    def leave_Call(self, original, updated):
        pos = self.get_metadata(PositionProvider, original).start
        kind = self.targets.get((pos.line, pos.column))
        if not kind:
            return updated
        key = (pos.line, pos.column)
        if kind in ("wrap_openai", "wrap_anthropic"):
            self.done[key] = kind
            return cst.Call(func=cst.Name(alias(kind)), args=[cst.Arg(updated)])
        names = {a.keyword.value for a in updated.args if a.keyword is not None}
        if kind in ("kwargs_langchain_openai", "kwargs_llamaindex"):
            async_kw = "http_async_client" if kind == "kwargs_langchain_openai" else "async_http_client"
            if "http_client" in names or async_kw in names or "openai_proxy" in names:
                self.skipped[key] = "client already sets http_client/%s/openai_proxy" % async_kw
                return updated
            new = list(updated.args)
            if new and new[-1].comma == cst.MaybeSentinel.DEFAULT:
                new[-1] = new[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
            new += [cst.Arg(keyword=cst.Name("http_client"), value=cst.parse_expression(alias("http_client") + "()"),
                            equal=cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""), whitespace_after=cst.SimpleWhitespace("")),
                            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
                    cst.Arg(keyword=cst.Name(async_kw), value=cst.parse_expression(alias("async_http_client") + "()"),
                            equal=cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""), whitespace_after=cst.SimpleWhitespace("")))]
            self.done[key] = kind
            return updated.with_changes(args=new)
        if kind == "kwargs_chat_anthropic":
            if names & {"base_url", "anthropic_api_url", "api_key", "anthropic_api_key"}:
                self.skipped[key] = "ChatAnthropic already sets base_url/api_key explicitly"
                return updated
            new = list(updated.args)
            if new and new[-1].comma == cst.MaybeSentinel.DEFAULT:
                new[-1] = new[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
            new.append(cst.Arg(star="**", value=cst.parse_expression(alias("chat_anthropic_kwargs") + "()")))
            self.done[key] = kind
            return updated.with_changes(args=new)
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
    imp = ", ".join("%s as %s" % (n, alias(n)) for n in names)
    if dots:
        return ("try:  # %s: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set\n"
                "    from %srouter_runtime import %s\n"
                "except ImportError:  # %s: module executed as a script\n"
                "    from router_runtime import %s\n") % (MARK, dots, imp, MARK, imp)
    return "from router_runtime import %s  # %s: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set\n" % (imp, MARK)


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


def patch_file(root, rel, targets, installs):
    """targets: {(line, col): kind}; installs: set of install kinds. Returns (changed, done, skipped, runtime_rel)."""
    path = os.path.join(root, rel)
    src = open(path, encoding="utf-8").read()
    if "_router_wrap_" in src or "_router_http_client" in src or "_router_install_" in src or "_router_chat_anthropic" in src:
        return False, {}, {k: "already patched" for k in targets}, None
    mod = cst.parse_module(src)
    p = _Patcher(targets)
    new_mod = MetadataWrapper(mod).visit(p)
    used = sorted({h for k in p.done.values() for h in HELPERS[k]} | {h for k in installs for h in HELPERS[k]})
    if not used:
        return False, p.done, p.skipped, None
    runtime_rel, dots = runtime_location(root, rel)
    block = import_block(dots, used)
    for k in sorted(installs):
        block += "%s()  # %s\n" % (alias(HELPERS[k][0]), MARK)
    new_mod = _insert_after_imports(new_mod, block)
    open(path, "w", encoding="utf-8").write(new_mod.code)
    return True, p.done, p.skipped, runtime_rel
