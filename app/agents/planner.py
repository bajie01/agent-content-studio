import json
import re

from app.agents.kimi_client import KimiClient
from app.schemas import TaskPlan


class PlannerAgent:
    def __init__(self, kimi: KimiClient):
        self.kimi = kimi

    async def run(self, topic: str, platforms: list[str]) -> TaskPlan:
        if self.kimi.available:
            return await self._run_with_llm(topic, platforms)
        return self._run_fallback(topic, platforms)

    async def _run_with_llm(self, topic: str, platforms: list[str]) -> TaskPlan:
        system_prompt = (
            "你是内容策划专家。输出严格 JSON，字段: outline(string[]),"
            "keywords(string[]), visual_placeholders(string[]), "
            "platform_style(object: platform->写作风格)。"
            "不要输出额外解释。"
        )
        user_prompt = (
            f"主题: {topic}\n"
            f"目标平台: {platforms}\n"
            "要求: 深度大纲(5-8条), 搜索关键词(6-10个), "
            "视觉素材占位符(3-5个), 每个平台一句写作策略。"
        )
        raw = await self.kimi.complete(
            system_prompt,
            user_prompt,
            force_json_object=True,
        )
        cleaned = self._extract_json(raw)
        data = json.loads(cleaned)
        return TaskPlan.model_validate(data)

    def _run_fallback(self, topic: str, platforms: list[str]) -> TaskPlan:
        base_keywords = self._extract_keywords(topic)
        keywords = (base_keywords + [f"{topic} 案例", f"{topic} 数据", f"{topic} 趋势"])[:10]
        outline = [
            f"{topic} 的背景与核心问题",
            f"{topic} 的最新趋势与关键数据",
            f"{topic} 的典型案例拆解",
            f"{topic} 的实践方法与执行步骤",
            f"{topic} 的风险、误区与规避建议",
            f"{topic} 的总结与行动建议",
        ]
        visual_placeholders = [
            f"{topic} 概念示意图",
            f"{topic} 数据图表",
            f"{topic} 案例场景图",
        ]
        style_map = {p: self._platform_style(p) for p in platforms}
        return TaskPlan(
            outline=outline,
            keywords=keywords,
            visual_placeholders=visual_placeholders,
            platform_style=style_map,
        )

    @staticmethod
    def _platform_style(platform: str) -> str:
        mapping = {
            "zhihu": "理性、结构化、重论证，适当引用数据与来源。",
            "xiaohongshu": "口语化、强场景、可执行清单，段落短且有标题钩子。",
            "baijiahao": "信息密度高，新闻化表达，强调观点与结论。",
        }
        return mapping.get(platform.lower(), "中性风格，清晰结构化表达。")

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        tokens = re.split(r"[\s,，、;；]+", text.strip())
        return [t for t in tokens if t]

    @staticmethod
    def _extract_json(text: str) -> str:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("Planner did not return JSON.")
        return match.group(0)
