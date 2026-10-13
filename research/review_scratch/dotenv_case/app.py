"""Repro: the runtime's import-time os.environ.setdefault("OPENAI_API_KEY", ...) shadows a key that load_dotenv() would load.

Typical app layout: imports first (a patched module pulls in router_runtime), then load_dotenv().
RAMP_ROUTER_API_KEY comes from the deployment environment; OPENAI_API_KEY lives in .env.
Run with an interpreter that has python-dotenv (eval/work/envs/gpt-researcher/bin/python), from this directory.
"""
import importlib.util
import os
import sys

os.environ.pop("OPENAI_API_KEY", None)
os.environ["RAMP_ROUTER_API_KEY"] = "rm-review-fake-router-key"  # set by the platform, not by .env
spec = importlib.util.spec_from_file_location("router_runtime", sys.argv[1])
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)  # what `from .router_runtime import wrap_openai` does at the top of a patched module

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
v = os.environ.get("OPENAI_API_KEY")
print("OPENAI_API_KEY after load_dotenv():", "placeholder 'unused-router-serves-this'" if v == "unused-router-serves-this" else ("value from .env" if v == "sk-from-dotenv-file" else repr(v)))
print("=> every request the runtime leaves on the provider (embeddings, unmapped models, n>1, previous_response_id ...) is sent with the placeholder key -> 401")
