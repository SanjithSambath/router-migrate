import { generateText } from "ai";
import { createOpenAI } from "@ai-sdk/openai";
import { routerOptions } from "./lib/routerRuntime";

const openai = createOpenAI({ compatibility: "strict", ...routerOptions() });

export const answer = (prompt: string) => generateText({ model: openai("gpt-4o-mini"), prompt });
