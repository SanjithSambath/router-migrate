import { createOpenAI } from "@ai-sdk/openai";
import { streamText } from "ai";
import { routerOptions } from "../../lib/routerRuntime";

const openai = createOpenAI({ ...routerOptions() });

export async function POST(req: Request) {
  const { messages } = await req.json();
  const result = streamText({
    model: openai("gpt-4o"),
    messages,
  });
  return result.toTextStreamResponse();
}
