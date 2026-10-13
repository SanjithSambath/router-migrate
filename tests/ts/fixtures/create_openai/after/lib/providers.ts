import { createOpenAI } from "@ai-sdk/openai";
import { createAnthropic } from "@ai-sdk/anthropic";
import { generateText } from "ai";
import { routerOptions } from "./routerRuntime";

export const myOpenAI = createOpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  compatibility: "strict",
  ...routerOptions(),
});

export const claude = createAnthropic({ ...routerOptions() });

export async function title(prompt: string) {
  const { text } = await generateText({ model: myOpenAI("gpt-4o-mini"), prompt });
  return text;
}
