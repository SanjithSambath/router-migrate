#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Doc."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from openai.types import Model
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from .router_runtime import wrap_openai as _router_wrap_openai
except ImportError:  # router-migrate: module executed as a script
    from router_runtime import wrap_openai as _router_wrap_openai
try:
    from openai import OpenAI
except ImportError:  # optional dep
    OpenAI = None
__all__ = ['client']
client = _router_wrap_openai(OpenAI()) if OpenAI else None

def f():
    return client.chat.completions.create(model='gpt-4o', messages=[])
