import { OpenAI } from 'openai';
import AnthropicClient from '@anthropic-ai/sdk';
import { readConfig } from '../config.js';

export class Engine {
  client: OpenAI;
  claude: AnthropicClient;
  constructor() {
    const config = readConfig();
    this.client = new OpenAI({ apiKey: config.key, baseURL: config.url });
    this.claude = new AnthropicClient({ apiKey: config.key });
  }
}
