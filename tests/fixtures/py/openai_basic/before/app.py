import os
from openai import OpenAI

client = OpenAI()


def ask(q):
    r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": q}], max_tokens=50)
    return r.choices[0].message.content


def embed(t):
    return client.embeddings.create(model="text-embedding-3-small", input=t).data[0].embedding
