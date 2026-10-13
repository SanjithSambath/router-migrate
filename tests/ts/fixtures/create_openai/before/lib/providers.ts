import { createOpenAI } from "@ai-sdk/openai";
import { createAnthropic } from "@ai-sdk/anthropic";
import { generateText } from "ai";

export const myOpenAI = createOpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  compatibility: "strict",
});

export const claude = createAnthropic();

export async function title(prompt: string) {
  const { text } = await generateText({ model: myOpenAI("gpt-4o-mini"), prompt });
  return text;
}
