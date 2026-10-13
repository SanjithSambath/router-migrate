from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
claude = ChatAnthropic(model="claude-haiku-4-5")
already = ChatOpenAI(model="gpt-4o", http_client=None)
emb = OpenAIEmbeddings()
