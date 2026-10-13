#!/usr/bin/env node
import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY ?? "sk-test" });

export async function complete(prompt) {
  const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: prompt }] });
  return r.choices[0].message.content;
}

console.log(typeof client.chat.completions.create);
