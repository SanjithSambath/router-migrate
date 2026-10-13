'use strict';
const OpenAI = require('openai');
const { OpenAI: Named } = require('openai');

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY
});
const second = new Named();

async function main() {
  const out = await client.chat.completions.create({ model: 'gpt-4o-mini', messages: [{ role: 'user', content: 'hi' }], stream: true });
  return [out, second];
}

module.exports = { main };
