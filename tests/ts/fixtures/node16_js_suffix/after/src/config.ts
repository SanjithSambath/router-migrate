export function readConfig() {
  return { key: process.env.OPENAI_API_KEY ?? '', url: process.env.OPENAI_BASE_URL };
}
