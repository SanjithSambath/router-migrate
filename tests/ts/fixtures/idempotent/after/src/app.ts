import OpenAI from "openai";
import { routerOptions, routerFetch } from "./lib/routerRuntime";

// patched by an earlier run: left alone
export const a = new OpenAI({ apiKey: "k", ...routerOptions() });

// added since the last run
export const b = new OpenAI({ ...routerOptions() });

const myFetch = (u: any, i?: any) => fetch(u, i);
export const c = new OpenAI({ fetch: routerFetch(myFetch) });

export async function run(q: string) {
  const x = await a.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: q }] });
  const y = await b.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: q }] });
  const z = await c.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: q }] });
  return [x, y, z];
}
