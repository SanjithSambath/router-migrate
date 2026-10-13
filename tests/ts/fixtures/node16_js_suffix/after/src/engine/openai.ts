import { OpenAI } from 'openai';
import AnthropicClient from '@anthropic-ai/sdk';
import { readConfig } from '../config.js';
import { routerOptions } from '../lib/routerRuntime.js';

export class Engine {
  client: OpenAI;
  claude: AnthropicClient;
  constructor() {
    const config = readConfig();
    this.client = new OpenAI({ apiKey: config.key, baseURL: config.url, ...routerOptions() });
    this.claude = new AnthropicClient({ apiKey: config.key, ...routerOptions() });
  }

  async ask(q: string) {
    const r = await this.client.chat.completions.create({ model: 'gpt-4o-mini', messages: [{ role: 'user', content: q }] });
    return r.choices[0].message.content;
  }

  async askClaude(q: string) {
    const m = await this.claude.messages.create({ model: 'claude-haiku-4-5', max_tokens: 64, messages: [{ role: 'user', content: q }] });
    return m.content;
  }
}
