import asyncio
from collections import OrderedDict
from typing import AsyncGenerator

from app.agents.kimi_client import KimiClient
from app.agents.models import Material
from app.schemas import TaskPlan


class WriterAgent:
    def __init__(self, kimi: KimiClient):
        self.kimi = kimi

    async def stream(
        self,
        topic: str,
        platforms: list[str],
        plan: TaskPlan,
        research: dict[str, list[Material]],
        *,
        enable_llm_stream: bool = False,
    ) -> AsyncGenerator[str, None]:
        if self.kimi.available and enable_llm_stream:
            system_prompt, user_prompt = self._build_prompts(topic, platforms, plan, research)
            async for delta in self.kimi.complete_stream(system_prompt, user_prompt):
                yield delta
            return

        if self.kimi.available:
            text = await self._write_with_llm(topic, platforms, plan, research)
        else:
            text = self._write_fallback(topic, platforms, plan, research)

        for chunk in self._chunk_text(text, 60):
            await asyncio.sleep(0.03)
            yield chunk

    async def _write_with_llm(
        self,
        topic: str,
        platforms: list[str],
        plan: TaskPlan,
        research: dict[str, list[Material]],
    ) -> str:
        system_prompt, user_prompt = self._build_prompts(topic, platforms, plan, research)
        return await self.kimi.complete(system_prompt, user_prompt)

    def _build_prompts(
        self,
        topic: str,
        platforms: list[str],
        plan: TaskPlan,
        research: dict[str, list[Material]],
    ) -> tuple[str, str]:
        platform = platforms[0].lower() if platforms else "zhihu"
        template = self._platform_template(platform)
        system_prompt = (
            "你是资深内容作者。严格按给定大纲写作，使用 markdown。"
            "输出需包含：标题、导语、分节正文、总结、可操作建议。"
            f"当前平台为 {platform}，必须严格执行该平台模板约束。"
            "当引用外部素材时，请在句末追加引用标记，格式为 [S:source_id]。"
            "引用标记必须来自已提供素材列表，不要编造 source_id。"
        )
        source_pool = self._source_pool(research)
        research_lines: list[str] = []
        for keyword, items in research.items():
            if not items:
                continue
            research_lines.append(f"- 关键词: {keyword}")
            for item in items:
                research_lines.append(
                    f"  - [S:{item.id}] [{item.provider}] {item.title} | {item.url} | quality={item.quality_score:.1f}"
                )
        source_index_lines = [
            f"- S:{item.id} | {item.title} | {item.url}" for item in source_pool[:30]
        ]
        user_prompt = (
            f"主题: {topic}\n"
            f"目标平台: {platform}\n"
            f"平台模板: {template}\n"
            f"大纲: {plan.outline}\n"
            f"写作风格约束: {plan.platform_style}\n"
            "检索摘要:\n"
            + ("\n".join(research_lines) if research_lines else "- 暂无外部检索结果")
            + "\n\n可引用来源索引:\n"
            + ("\n".join(source_index_lines) if source_index_lines else "- 无")
        )
        return system_prompt, user_prompt

    def _write_fallback(
        self,
        topic: str,
        platforms: list[str],
        plan: TaskPlan,
        research: dict[str, list[Material]],
    ) -> str:
        platform = platforms[0].lower() if platforms else "zhihu"
        template = self._platform_template(platform)
        lines = [
            f"# {topic}",
            "",
            f"> 平台：{platform} | 写作模板：{template['tone']} | 目标长度：{template['length_hint']}",
            "",
            "## 导语",
            f"围绕“{topic}”，本文按策划-研究-写作流程生成，并按 {platform} 风格组织表达。",
            "",
        ]
        for idx, section in enumerate(plan.outline, start=1):
            lines.append(f"## {idx}. {section}")
            lines.append(
                f"这一部分围绕“{section}”展开，采用 {template['paragraph_style']}，结合检索素材与通用方法论进行说明。"
            )
            lines.append("")
        lines.append("## 平台执行清单")
        lines.append(f"- 语气：{template['tone']}")
        lines.append(f"- 标题策略：{template['title_style']}")
        lines.append(f"- 结构策略：{template['structure']}")
        lines.append(f"- 结尾动作：{template['ending_action']}")
        lines.append("")
        lines.append("## 参考素材")
        added = 0
        for items in research.values():
            for item in items:
                lines.append(f"- [S:{item.id}] [{item.title}]({item.url})")
                added += 1
                if added >= 10:
                    break
            if added >= 10:
                break
        if added == 0:
            lines.append("- 当前环境下未获取到外部检索结果，可接入 Search API 增强。")
        return "\n".join(lines)

    @staticmethod
    def _platform_template(platform: str) -> dict[str, str]:
        templates = {
            "zhihu": {
                "length_hint": "1800-2600字",
                "tone": "理性克制、论证清晰",
                "title_style": "问题式或观点式标题，避免标题党",
                "structure": "先结论后论证，分层小标题",
                "paragraph_style": "段落较长，强调因果和数据",
                "ending_action": "给出方法论总结与延伸阅读建议",
            },
            "xiaohongshu": {
                "length_hint": "800-1400字",
                "tone": "口语化、陪伴感、强场景",
                "title_style": "场景+收益导向标题，开头3行给钩子",
                "structure": "短段落+清单化，步骤编号明确",
                "paragraph_style": "单段1-3句，强调可执行动作",
                "ending_action": "给出可复制清单和互动提问",
            },
            "baijiahao": {
                "length_hint": "1200-2000字",
                "tone": "信息密度高、新闻化表达",
                "title_style": "事实导向标题，突出时间与结论",
                "structure": "导语-事实-分析-结论",
                "paragraph_style": "中短段，先事实后观点",
                "ending_action": "总结趋势判断与风险提示",
            },
        }
        return templates.get(
            platform,
            {
                "length_hint": "1200-1800字",
                "tone": "中性专业",
                "title_style": "清晰陈述主题",
                "structure": "导语-正文-总结",
                "paragraph_style": "结构化表达",
                "ending_action": "给出简短结论",
            },
        )

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        return [text[i : i + size] for i in range(0, len(text), size)]

    @staticmethod
    def _source_pool(research: dict[str, list[Material]]) -> list[Material]:
        pool: OrderedDict[str, Material] = OrderedDict()
        for items in research.values():
            for item in items:
                if item.id not in pool:
                    pool[item.id] = item
        return list(pool.values())
