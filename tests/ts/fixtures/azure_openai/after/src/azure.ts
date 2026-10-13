import { AzureOpenAI } from "openai";

const client = new AzureOpenAI({
  endpoint: process.env.AZURE_OPENAI_ENDPOINT,
  apiVersion: "2024-10-21",
});

export async function chat(content: string) {
  return client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content }] });
}
