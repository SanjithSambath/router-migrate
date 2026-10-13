# Dashboard tabs check on the eval data

Each `eval/reports/<repo>/REPORT.html` was opened in headless Chromium (Playwright; network blocked), the **Router setup** and **Unsupported** tabs were clicked, and every item of that repo's bundle (`setup[].title`, `unsupported[].file`) was looked up in the rendered text.

| repo | Router setup items shown | Unsupported sites shown |
|---|---|---|
| TaskWeaver | 5 / 5 | 2 / 2 |
| ai-chatbot | 5 / 5 | 0 / 0 |
| aicommits | 5 / 5 | 0 / 0 |
| anthropic-quickstarts | 5 / 5 | 2 / 2 |
| chatbot-ui | 5 / 5 | 4 / 4 |
| gpt-researcher | 5 / 5 | 6 / 6 |
| langchain-nextjs-template | 5 / 5 | 3 / 3 |
| llm-anthropic | 5 / 5 | 1 / 1 |
| llm | 5 / 5 | 4 / 4 |
| mem0 | 5 / 5 | 20 / 20 |
| openai-responses-starter-app | 5 / 5 | 6 / 6 |
| opencommit | 5 / 5 | 2 / 2 |
| screenshot-to-code | 5 / 5 | 0 / 0 |
| shell_gpt | 5 / 5 | 0 / 0 |
| swarm | 5 / 5 | 0 / 0 |

Result: all items rendered (15 repos).
