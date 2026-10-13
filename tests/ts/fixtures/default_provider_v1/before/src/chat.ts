import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";

export const answer = (prompt: string) => generateText({ model: openai("gpt-4o-mini"), prompt });
