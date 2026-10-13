import { createOpenAI } from '@ai-sdk/openai';
import { createAnthropic } from '@ai-sdk/anthropic';
import { embed, generateText } from 'ai';
import { routerOptions } from '../../lib/routerRuntime';

const oa = createOpenAI({ ...routerOptions() });
const anthropic = createAnthropic({ ...routerOptions() });

const custom = createOpenAI({ baseURL: 'https://example.com/v1' });

export async function run(text: string) {
  await embed({ model: oa.embedding('text-embedding-3-small'), value: text });
  await generateText({ model: custom('gpt-4o-mini'), prompt: text });
  return generateText({ model: anthropic('claude-sonnet-4-5'), prompt: text });
}
