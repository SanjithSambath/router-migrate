'use strict';
const { routerOptions } = require('./lib/routerRuntime.js');
const OpenAI = require('openai');
const { OpenAI: Named } = require('openai');

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  ...routerOptions()
});
const second = new Named({ ...routerOptions() });

async function main() {
  const out = await client.chat.completions.create({ model: 'gpt-4o-mini', messages: [{ role: 'user', content: 'hi' }], stream: true });
  return [out, second];
}

module.exports = { main };
