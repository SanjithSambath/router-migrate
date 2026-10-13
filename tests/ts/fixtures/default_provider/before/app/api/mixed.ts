import { createOpenAI, openai as oa } from '@ai-sdk/openai';
import { anthropic } from '@ai-sdk/anthropic';
import { embed, generateText } from 'ai';

const custom = createOpenAI({ baseURL: 'https://example.com/v1' });

export async function run(text: string) {
  await embed({ model: oa.embedding('text-embedding-3-small'), value: text });
  await generateText({ model: custom('gpt-4o-mini'), prompt: text });
  return generateText({ model: anthropic('claude-sonnet-4-5'), prompt: text });
}
