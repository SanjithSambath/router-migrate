import openai
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from .router_runtime import install_openai_module as _router_install_openai_module
except ImportError:  # router-migrate: module executed as a script
    try:
        from router_runtime import install_openai_module as _router_install_openai_module
    except ImportError:  # router-migrate: runtime not importable from here; this module keeps its original behavior
        _router_install_openai_module = lambda: None  # noqa: E731
_router_install_openai_module()  # router-migrate


def go():
    return openai.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
