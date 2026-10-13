class ZhipuAIService:
    zhipuai = None

    def __init__(self, config):
        import zhipuai

        ZhipuAIService.zhipuai = zhipuai
        self.client = ZhipuAIService.zhipuai.ZhipuAI(base_url=config.api_base, api_key=config.api_key)

    def chat(self, messages):
        return self.client.chat.completions.create(model=self.config.model, messages=messages)
