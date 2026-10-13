import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export async function ask(q: string) {
  const msg = await anthropic.messages.create({
    model: "claude-sonnet-4-5",
    max_tokens: 1024,
    messages: [{ role: "user", content: q }],
  });
  return msg.content;
}

// lazily loaded peer dependency (mem0 style)
export class LazyClaude {
  private client?: Anthropic;
  constructor(private readonly clientArgs: { apiKey: string; baseURL?: string }) {}

  private async ensure() {
    const sdk = await import("@anthropic-ai/sdk");
    this.client = new sdk.default(this.clientArgs);
  }

  async stream(q: string) {
    await this.ensure();
    return this.client!.messages.stream({ model: "claude-haiku-4-5", max_tokens: 64, messages: [{ role: "user", content: q }] });
  }
}
