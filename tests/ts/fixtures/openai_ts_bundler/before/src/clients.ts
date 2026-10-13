import OpenAI from "openai";

export const MODEL = "gpt-4o-mini";

// no arguments
export const plain = new OpenAI();

// object literal, multi-line, trailing comma
export const configured = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  baseURL: "https://api.openai.com/v1", // explicit default
});

// object literal, single line, no trailing comma
export const inline = new OpenAI({ apiKey: "sk-test", maxRetries: 1 });

// existing fetch
export const withFetch = new OpenAI({
  apiKey: "sk-test",
  fetch: (url: any, init?: any) => globalThis.fetch(url, init),
});

// expression argument
const options: ConstructorParameters<typeof OpenAI>[0] = { apiKey: "sk-test" };
export const fromOptions = new OpenAI(options);

// complex expression argument (evaluated once)
function makeOptions() {
  return { apiKey: "sk-test", timeout: 1000 };
}
export const fromCall = new OpenAI(makeOptions());

// spread in an object literal
export const spread = new OpenAI({ ...options, organization: "org" });

export async function ask(q: string) {
  const r = await configured.chat.completions.create({
    model: MODEL,
    messages: [{ role: "user", content: q }],
  });
  return r.choices[0].message.content;
}
