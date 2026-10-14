import OpenAI from "openai";
import { routerOptions } from "./lib/routerRuntime.js";
const client = new OpenAI({ ...routerOptions() });
export async function ask(q: string) {
  return client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: q }] });
}
