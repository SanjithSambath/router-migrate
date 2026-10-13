import { ChatOpenAI, OpenAIEmbeddings } from "@langchain/openai";
import { ChatAnthropic } from "@langchain/anthropic";

const shared = { timeout: 5000 };

export const plain = new ChatOpenAI({ model: "gpt-4o-mini", temperature: 0 });

export const configured = new ChatOpenAI({
  model: "gpt-4o-mini",
  configuration: {
    baseURL: "https://api.openai.com/v1",
  },
});

export const indirect = new ChatOpenAI({ model: "gpt-4o", configuration: shared });

export const bare = new ChatOpenAI();

export const embeddings = new OpenAIEmbeddings();

export const claude = new ChatAnthropic({ model: "claude-sonnet-4-5" });

export async function answer(q: string) {
  const structured = plain.withStructuredOutput({ type: "object", properties: {} });
  await structured.invoke(q);
  for await (const chunk of await configured.stream(q)) void chunk;
  return claude.invoke(q);
}
