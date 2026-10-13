import litellm
from openai import OpenAI

client = OpenAI()
completion = client.chat.completions.create


def run(**kw):
    return completion(**kw)


def via_litellm(messages):
    return litellm.completion(model="gpt-4o-mini", messages=messages)
