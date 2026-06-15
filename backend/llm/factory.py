import json
from collections.abc import AsyncIterator

from backend.db.models import ProjectModelConfig
from backend.llm.base import BaseLLMClient, JSONValue
from backend.llm.openai_compatible import OpenAICompatibleClient


class MockLLMClient(BaseLLMClient):
    def __init__(self, *, role: str = "", model_name: str = "") -> None:
        self.role = role
        self.model_name = model_name

    async def generate(self, system_prompt: str, user_message: str) -> str:
        if "提示词策划助手" in system_prompt:
            return json.dumps(
                {
                    "system_prompt": "必须严格覆盖本章大纲，延续前文状态，直接输出正文。",
                    "must_cover": ["覆盖本章大纲关键事件"],
                    "must_avoid": ["不要脱离大纲", "不要输出解释"],
                    "style_focus": ["叙述清晰", "冲突推进明确"],
                    "reasoning_notes": ["mock prompt builder"],
                },
                ensure_ascii=False,
            )
        if self.model_name == "mock-writer-retry-aware":
            return self._generate_retry_aware_writer_content(user_message)
        return "第1章 测试章节\n\n这是一个用于本地联调的占位正文。\n人物按照大纲推进了情节，并埋下了下一章线索。"

    async def stream_generate(self, system_prompt: str, user_message: str) -> AsyncIterator[str]:
        yield await self.generate(system_prompt, user_message)

    async def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: dict[str, JSONValue] | None = None,
        schema_name: str = "response",
    ) -> JSONValue:
        if "系统提示词设计助手" in system_prompt:
            return {
                "global_prompt": "\n".join(
                    [
                        "你是一名长篇中文小说写作者。",
                        "必须严格按照章节大纲推进主线，覆盖每章关键事件，不得擅自删改主要情节。",
                        "保持人物性格、动机、关系和世界观规则前后一致。",
                        "全书叙事风格需稳定，正文直接输出，不写解释与元评论。",
                    ]
                ),
                "core_requirements": ["严格遵循章节大纲", "保证连续性", "保持人物一致性"],
                "style_guidance": ["叙事清晰", "节奏稳定"],
                "risk_points": ["不要跳过关键情节", "不要脱离题材与主线"],
                "reasoning_notes": ["mock global prompt builder"],
            }
        if "提示词策划助手" in system_prompt and self.model_name == "mock-prompt-builder-retry-aware":
            return self._build_retry_aware_prompt_payload(user_message)
        if "连续性检查助手" in system_prompt:
            return {
                "issues": [],
                "continuity_notes": ["延续上一章人物状态", "保持当前场景逻辑一致"],
                "suggested_focus": ["推进本章主冲突", "确保关键事件落地"],
            }
        if "小说摘要助手" in system_prompt:
            return {
                "summary_text": "本章围绕既定大纲推进，主要角色完成关键互动，并为下一章留下推进空间。",
                "key_events": ["角色完成本章核心目标", "冲突进入下一阶段"],
                "character_changes": {"主角": "明确了新的行动方向"},
                "world_changes": {},
                "unresolved_threads": ["下一章需要继续处理的冲突"],
                "emotional_tone": "紧张",
                "time_location": "章节结尾仍在当前主要场景",
                "relationship_changes": [
                    {
                        "characters": ["主角", "关键对象"],
                        "change": "关系试探性推进",
                        "tension": "仍有未说破的顾虑",
                    }
                ],
                "emotional_beats": ["靠近后又克制", "短暂误会加深张力"],
                "romance_threads": ["两人关系尚未明确"],
                "scene_hook": "关键对象留下暧昧但未说完的话",
                "next_chapter_anchor": "下一章需要继续处理关系推进与主线冲突",
            }
        if "人物状态追踪助手" in system_prompt:
            return {
                "updates": [
                    {
                        "name": "主角",
                        "role": "主角",
                        "change_type": "update",
                        "profile_json": {"latest_state": "准备继续推进主线"},
                        "confidence": 0.95,
                        "apply_mode": "auto_safe",
                    }
                ]
            }
        if "世界观设定追踪助手" in system_prompt:
            return {"updates": []}
        if self.model_name == "mock-critic-retry-aware":
            return self._build_retry_aware_review_payload(user_message)
        return json.loads(
            """
            {
              "overall_score": 8.5,
              "passed": true,
              "dimensions": {
                "outline_adherence": {"score": 9, "comment": "已覆盖大纲要点"},
                "instruction_adherence": {"score": 8, "comment": "基本遵循提示词"},
                "continuity_consistency": {"score": 8, "comment": "未发现明显冲突"},
                "character_consistency": {"score": 8, "comment": "人物表现稳定"},
                "writing_quality": {"score": 9, "comment": "文本流畅可读"}
              },
              "blocking_issues": [],
              "uncovered_outline_points": [],
              "violated_instructions": [],
              "improvement_suggestions": ["下一章延续当前冲突推进"],
              "non_scoring_notes": []
            }
            """
        )

    def supports_stream_text_fallback(self) -> bool:
        return True

    def _generate_retry_aware_writer_content(self, user_message: str) -> str:
        if "【retry_feedback】" in user_message and "怀表密钥" in user_message:
            return (
                "第1章 重试修正版\n\n"
                "钟楼下的风像钝刀一样刮过石阶。沈夜在墙缝里摸到一枚冰冷的怀表密钥，立刻意识到它正是失踪档案案的关键物证。"
                "他没有再犹豫，当场决定当夜潜入档案室，把管理员刻意藏起的那段旧记录翻出来。"
            )
        return (
            "第1章 初稿\n\n"
            "钟楼下雾气沉沉，沈夜反复审视委托人留下的旧纸条，却始终没有抓到真正能撬动案件的关键物证。"
            "他感觉危险逼近，却仍停在犹疑之中，没有把下一步行动真正落实。"
        )

    def _build_retry_aware_prompt_payload(self, user_message: str) -> dict[str, JSONValue]:
        if "最近失败尝试次数" in user_message or "失败原因" in user_message:
            system_prompt = (
                "必须严格覆盖本章大纲，明确写出沈夜在钟楼下发现怀表密钥，"
                "并让他当场决定当夜潜入档案室继续追查；保持冷峻压迫感，直接输出正文。"
            )
            reasoning_notes = ["mock retry-aware prompt builder", "包含重试修正点"]
        else:
            system_prompt = "必须严格覆盖本章大纲，保持钟楼调查氛围，直接输出正文。"
            reasoning_notes = ["mock retry-aware prompt builder", "首次生成"]
        return {
            "system_prompt": system_prompt,
            "must_cover": ["覆盖本章大纲关键事件"],
            "must_avoid": ["不要脱离大纲", "不要输出解释"],
            "style_focus": ["叙述清晰", "冲突推进明确"],
            "reasoning_notes": reasoning_notes,
        }

    def _build_retry_aware_review_payload(self, user_message: str) -> dict[str, JSONValue]:
        content = user_message.split("【待评审正文】", 1)[1] if "【待评审正文】" in user_message else user_message
        if "怀表密钥" in content and ("决定当夜潜入档案室" in content or "决定潜入档案室" in content):
            return {
                "overall_score": 8.8,
                "passed": True,
                "dimensions": {
                    "outline_adherence": {"score": 9, "comment": "关键事件已补齐"},
                    "instruction_adherence": {"score": 9, "comment": "已遵循重试修正要求"},
                    "continuity_consistency": {"score": 8, "comment": "与前文衔接正常"},
                    "character_consistency": {"score": 8, "comment": "主角决策更明确"},
                    "writing_quality": {"score": 8, "comment": "文本达到可发布下限"},
                },
                "blocking_issues": [],
                "uncovered_outline_points": [],
                "violated_instructions": [],
                "improvement_suggestions": ["下一章继续回收钟楼与档案室线索"],
                "non_scoring_notes": [],
            }
        return {
            "overall_score": 7.1,
            "passed": False,
            "dimensions": {
                "outline_adherence": {"score": 7, "comment": "缺少关键物证发现"},
                "instruction_adherence": {"score": 7, "comment": "没有落实明确行动决策"},
                "continuity_consistency": {"score": 8, "comment": "连续性暂无硬伤"},
                "character_consistency": {"score": 7, "comment": "主角推进动力不足"},
                "writing_quality": {"score": 8, "comment": "文字基本通顺"},
            },
            "blocking_issues": ["缺少能推动案件进入下一阶段的关键物证"],
            "uncovered_outline_points": ["没有写出沈夜在钟楼下发现怀表密钥"],
            "violated_instructions": ["没有让主角明确决定当夜潜入档案室"],
            "improvement_suggestions": ["补上怀表密钥的发现过程，并让主角立刻作出潜入决定"],
            "non_scoring_notes": ["钟楼压迫感可以再强一些"],
        }


def build_llm_client(
    model_config: ProjectModelConfig,
    api_key: str | None = None,
    api_key_name: str | None = None,
) -> BaseLLMClient:
    if model_config.provider == "mock":
        return MockLLMClient(role=str(model_config.role), model_name=model_config.model_name)
    if model_config.provider != "openai_compatible":
        raise ValueError(f"暂不支持的 provider: {model_config.provider}")
    if not api_key:
        key_name = api_key_name or f"{model_config.role.upper()}_API_KEY"
        raise ValueError(f"{model_config.role} 缺少 API Key，请配置环境变量 {key_name}")
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=model_config.base_url,
        model=model_config.model_name,
        temperature=float(model_config.temperature) if model_config.temperature is not None else None,
        max_tokens=int(model_config.max_tokens) if model_config.max_tokens is not None else None,
        extra_config=getattr(model_config, "extra_config", None),
    )
