import os

from anthropic import Anthropic
from openai import OpenAI

shared = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def settings_for_user(uid):
    return {}


def anthropic_route(profile, messages):
    client = Anthropic(api_key=profile.anthropic_api_key or "")
    return client.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=messages)


def openai_route(uid, messages):
    client = OpenAI(api_key=settings_for_user(uid)["key"])
    return client.chat.completions.create(model="gpt-4o-mini", messages=messages)


class Service:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key)  # constructor injection: configuration, not per-request

    def ask(self, messages):
        return self.client.chat.completions.create(model="gpt-4o-mini", messages=messages)


def shared_route(messages):
    return shared.chat.completions.create(model="gpt-4o-mini", messages=messages)
