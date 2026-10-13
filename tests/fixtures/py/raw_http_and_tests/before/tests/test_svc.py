from openai import OpenAI

def test_x():
    c = OpenAI(api_key="test")
    c.chat.completions.create(model="gpt-4o", messages=[])
