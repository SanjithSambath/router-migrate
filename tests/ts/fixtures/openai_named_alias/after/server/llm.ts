'use server'
import { OpenAI as Client } from 'openai'
import * as oai from 'openai'
import { routerOptions } from '../lib/routerRuntime'

export class Llm {
  private client: Client
  private other = new oai.OpenAI({ apiKey: 'k', ...routerOptions() })

  constructor(key: string) {
    this.client = new Client({ apiKey: key, ...routerOptions() })
  }

  async run(prompt: string) {
    const res = await this.client.responses.create({ model: 'gpt-4.1', input: prompt })
    await this.other.embeddings.create({ model: 'text-embedding-3-small', input: prompt })
    return res.output_text
  }
}
