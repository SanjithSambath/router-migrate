from openai import OpenAI, AzureOpenAI

groq = OpenAI(base_url="https://api.groq.com/openai/v1", api_key="x")
azure = AzureOpenAI(azure_endpoint="https://x.openai.azure.com", api_version="2024-02-01")
cfg = OpenAI(base_url=settings.base_url)


def a():
    return groq.chat.completions.create(model="llama3-70b", messages=[])


def b():
    return azure.chat.completions.create(model="gpt-4o", messages=[])


def c():
    return cfg.chat.completions.create(model="gpt-4o", messages=[])
