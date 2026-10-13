import openai


def go():
    return openai.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
