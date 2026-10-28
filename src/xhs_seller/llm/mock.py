class MockLLM:
    """离线占位；不声明生成事实或连接外部模型。"""

    def generate(self, prompt: str) -> str:
        return "离线占位：P0 不生成经营内容。"
