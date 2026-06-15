from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.db.models import ChapterReview
from backend.services.context_service import ContextService


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return list(self._rows)


def test_estimate_tokens_is_positive():
    service = ContextService(session=None)  # type: ignore[arg-type]
    assert service.estimate_tokens("这是一个测试文本") > 0


def test_fit_to_budget_trims_optional_layers():
    service = ContextService(session=None)  # type: ignore[arg-type]
    layers = [
        ("required", "a" * 20000, True),
        ("optional", "b" * 120000, False),
    ]
    fitted = service._fit_to_budget(layers)
    assert len(fitted) == 2
    assert len(dict(fitted)["optional"]) < 120000


def test_fit_to_budget_prioritizes_target_limit():
    service = ContextService(session=None)  # type: ignore[arg-type]
    layers = [
        ("effective_system_prompt", "a" * 5000, True),
        ("current_outline", "b" * 3000, True),
        ("recent_full_text", "c" * 80000, False),
        ("distant_memory", "d" * 40000, False),
    ]

    fitted = service._fit_to_budget(layers, target_tokens=12000, hard_limit_tokens=16000)
    total_tokens = sum(service.estimate_tokens(text) for _, text in fitted)

    assert total_tokens <= 16000
    assert len(dict(fitted)["recent_full_text"]) < 80000


def test_aggregate_retry_reviews_collects_structured_feedback():
    service = ContextService(session=None)  # type: ignore[arg-type]
    failed_reviews = [
        (
            3,
            ChapterReview(
                overall_score=7.2,
                passed=False,
                outline_score=7.0,
                instruction_score=7.1,
                continuity_score=7.4,
                character_score=7.3,
                writing_score=7.5,
                blocking_issues=["开场冲突不足"],
                uncovered_outline_points=["主角没有决定潜入档案馆"],
                violated_instructions=["没有保持冷峻文风"],
                improvement_suggestions=["增强主角决断瞬间"],
                non_scoring_notes=[],
                raw_json={},
            ),
        ),
        (
            2,
            ChapterReview(
                overall_score=6.8,
                passed=False,
                outline_score=6.8,
                instruction_score=6.9,
                continuity_score=7.0,
                character_score=6.7,
                writing_score=6.8,
                blocking_issues=["开场冲突不足"],
                uncovered_outline_points=["缺少档案馆警卫压迫感"],
                violated_instructions=[],
                improvement_suggestions=["补强环境压迫感"],
                non_scoring_notes=[],
                raw_json={},
            ),
        ),
    ]

    aggregated = service._aggregate_retry_reviews(failed_reviews, "延续上一轮指出的问题")

    assert aggregated["recent_failed_attempts"] == 2
    assert aggregated["last_failed_attempt_no"] == 3
    assert aggregated["last_failed_score"] == pytest.approx(7.2)
    assert aggregated["recent_blocking_issues"] == ["开场冲突不足"]
    assert "主角没有决定潜入档案馆" in aggregated["recent_uncovered_outline_points"]
    assert "没有保持冷峻文风" in aggregated["recent_violated_instructions"]
    assert "增强主角决断瞬间" in aggregated["recent_improvement_suggestions"]
    assert "延续上一轮指出的问题" in aggregated["recent_improvement_suggestions"]
    assert any(item.startswith("优先修复：") for item in aggregated["failed_reasons"])


@pytest.mark.asyncio
async def test_build_writer_context_includes_relationship_layers_and_cross_chapter_review_insights():
    project = SimpleNamespace(global_prompt="全局要求")
    outline = SimpleNamespace(outline_text="沈夜在钟楼与林疏对峙")
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)
    service._get_relevant_characters = AsyncMock(return_value="沈夜: 冷静")
    service._get_relevant_world_settings = AsyncMock(return_value="location/钟楼: 夜雾弥漫")
    service._get_recent_context = AsyncMock(
        return_value={
            "previous_chapter_tail": "上一章末尾",
            "recent_chapter_cards": "第1章\n摘要：旧线索浮现",
            "relationship_state_memory": "近期关系推进：\n第1章：沈夜 ↔ 林疏：关系推进：短暂联手",
        }
    )
    service._get_distant_memory = AsyncMock(return_value="远期记忆")
    service.build_retry_guidance = AsyncMock(
        return_value={
            "writer_feedback": "阻塞问题：\n- 补强对峙张力",
            "critic_review_history": "",
            "prompt_retry_info": {},
        }
    )
    service._build_cross_chapter_review_insights = AsyncMock(return_value="- 保持压迫感（来自第2章已通过评审的提醒）")

    result = await service.build_writer_context(
        project_id="project-id",
        chapter_number=3,
        effective_system_prompt="系统提示词",
        retry_feedback="",
        precheck={"continuity_notes": ["承接上一章结尾"]},
    )

    assert "【cross_chapter_review_insights】" in result["user_message"]
    assert "【recent_chapter_cards】" in result["user_message"]
    assert "【relationship_state_memory】" in result["user_message"]
    assert "保持压迫感" in result["user_message"]
    assert result["layers"]["cross_chapter_review_insights"].startswith("- 保持压迫感")
    assert "短暂联手" in result["layers"]["relationship_state_memory"]


@pytest.mark.asyncio
async def test_build_rush_writer_context_includes_last_ten_passed_chapters_by_default():
    project = SimpleNamespace(title="雪夜档案", genre="悬疑", global_prompt="保持冷峻叙事")
    outline = SimpleNamespace(outline_text="沈夜潜入档案馆")
    previous_chapters = [
        SimpleNamespace(chapter_number=1, final_content="第一章正文"),
        SimpleNamespace(chapter_number=2, final_content="第二章正文"),
    ]
    session = SimpleNamespace(
        get=AsyncMock(return_value=project),
        execute=AsyncMock(return_value=FakeResult(list(reversed(previous_chapters)))),
    )
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)

    result = await service.build_rush_writer_context(
        project_id="project-id",
        chapter_number=3,
        previous_chapter_count=10,
    )

    assert "【书名】\n雪夜档案" in result["user_message"]
    assert "【题材】\n悬疑" in result["user_message"]
    assert "【全局提示词】\n保持冷峻叙事" in result["user_message"]
    assert "第1章正文\n第一章正文" in result["user_message"]
    assert "第2章正文\n第二章正文" in result["user_message"]
    assert "你现在要创作第 3 章，本章的主要情节为：沈夜潜入档案馆" in result["user_message"]
    assert result["layers"]["rush_previous_chapter_contents"].startswith("第1章正文")


@pytest.mark.asyncio
async def test_build_rush_writer_context_allows_zero_previous_chapters():
    project = SimpleNamespace(title="雪夜档案", genre="悬疑", global_prompt="保持冷峻叙事")
    outline = SimpleNamespace(outline_text="沈夜潜入档案馆")
    session = SimpleNamespace(get=AsyncMock(return_value=project), execute=AsyncMock())
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)

    result = await service.build_rush_writer_context(
        project_id="project-id",
        chapter_number=3,
        previous_chapter_count=0,
    )

    assert "rush_previous_chapter_contents" not in result["layers"]
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_critic_context_includes_world_settings_previous_tail_and_review_history():
    project = SimpleNamespace(global_prompt="总要求")
    outline = SimpleNamespace(outline_text="主角在钟楼追查密钥")
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)
    service._get_relevant_characters = AsyncMock(return_value="沈夜: 调查员")
    service._get_relevant_world_settings = AsyncMock(return_value="rule/密钥: 只能在钟声后激活")
    service._render_recent_summaries = AsyncMock(return_value="第2章\n主角拿到地图")
    service._get_previous_chapter_tail = AsyncMock(return_value="第2章末尾\n钟声落下前，沈夜停步。")
    service.build_retry_guidance = AsyncMock(
        return_value={
            "writer_feedback": "",
            "critic_review_history": "同章重试复盘：\n- 上一轮缺少钟楼规则约束",
            "prompt_retry_info": {},
        }
    )
    service._build_cross_chapter_review_insights = AsyncMock(return_value="- 保持线索回收节奏")
    service._get_distant_memory = AsyncMock(return_value="远期伏笔：第一章提到失踪档案")

    context = await service.build_critic_context(
        project_id="project-id",
        chapter_number=5,
        effective_system_prompt="系统提示词",
        content="正文内容",
    )

    assert "【相关世界设定】" in context
    assert "只能在钟声后激活" in context
    assert "【上一章末尾】" in context
    assert "沈夜停步" in context
    assert "【历史评审要点】" in context
    assert "保持线索回收节奏" in context
    assert "【远期记忆摘要】" in context




def test_aggregate_retry_reviews_backfills_from_raw_json_when_persisted_lists_are_empty():
    service = ContextService(session=None)  # type: ignore[arg-type]
    failed_reviews = [
        (
            5,
            ChapterReview(
                overall_score=7.4,
                passed=False,
                outline_score=7.0,
                instruction_score=7.0,
                continuity_score=8.0,
                character_score=8.0,
                writing_score=8.0,
                blocking_issues=[],
                uncovered_outline_points=[],
                violated_instructions=[],
                improvement_suggestions=[],
                non_scoring_notes=[],
                raw_json={
                    "scores": {
                        "outline_adherence": {"score": 7, "reason": "关键证据不足"},
                        "instruction_adherence": {"score": 7, "reason": "关键要求表达不够直接"},
                    },
                    "blocking_issues": [
                        {
                            "issue": "核心结论证据不足",
                            "details": "缺少档案抽页、索引缺失和数据库修改的交叉印证。",
                        }
                    ],
                    "minor_issues": [
                        {
                            "issue": "实时呼叫的实时性可再强化",
                            "details": "建议明确表现为外部实时频道意外接通。",
                        }
                    ],
                    "failed_reasons": ["outline_adherence < 8", "instruction_adherence < 8"],
                    "summary": "优先补强硬证据与章末实时悬念。",
                },
            ),
        )
    ]

    aggregated = service._aggregate_retry_reviews(failed_reviews, "")

    assert aggregated["recent_blocking_issues"] == [
        "核心结论证据不足：缺少档案抽页、索引缺失和数据库修改的交叉印证。"
    ]
    assert aggregated["recent_violated_instructions"] == [
        "关键要求表达不够直接",
        "实时呼叫的实时性可再强化：建议明确表现为外部实时频道意外接通。",
    ]
    assert "outline_adherence < 8" in aggregated["recent_improvement_suggestions"]
    assert "优先补强硬证据与章末实时悬念。" in aggregated["recent_improvement_suggestions"]


@pytest.mark.asyncio
async def test_get_distant_memory_prefers_cache_and_relevant_summary_snippets():
    project = SimpleNamespace(distant_memory_cache="压缩远期记忆")
    summaries = [
        SimpleNamespace(
            chapter_number=1,
            summary_text="第一章摘要",
            key_events=["沈夜拿到密钥"],
            unresolved_threads=["失踪档案去向不明"],
            character_changes={"沈夜": "开始怀疑上司"},
            world_changes={"钟楼": "夜间会封锁"},
        ),
        SimpleNamespace(
            chapter_number=2,
            summary_text="第二章摘要",
            key_events=["林疏现身"],
            unresolved_threads=[],
            character_changes={},
            world_changes={},
        ),
    ]
    session = SimpleNamespace(get=AsyncMock(return_value=project), execute=AsyncMock(return_value=FakeResult(summaries)))
    service = ContextService(session)  # type: ignore[arg-type]

    result = await service._get_distant_memory("project-id", 5, "本章大纲提到沈夜继续追查失踪档案")

    assert "压缩远期记忆" in result
    assert "相关远期记忆提要" in result
    assert "失踪档案去向不明" in result


def test_render_compact_distant_summary_accepts_list_based_change_shapes():
    service = ContextService(session=None)  # type: ignore[arg-type]
    summary = SimpleNamespace(
        chapter_number=9,
        summary_text="第九章摘要",
        key_events=["黄亦玫做出决定"],
        unresolved_threads=["庄国栋态度未明"],
        character_changes=[{"name": "黄亦玫"}, {"character_name": "庄国栋"}, "苏苏"],
        world_changes=[{"world_name": "北京"}, {"title": "建筑院"}],
    )

    rendered = service._render_compact_distant_summary(summary)

    assert "人物变化：黄亦玫、庄国栋、苏苏" in rendered
    assert "设定变化：北京、建筑院" in rendered


@pytest.mark.asyncio
async def test_get_distant_memory_handles_list_based_change_shapes_without_crashing():
    project = SimpleNamespace(distant_memory_cache="")
    summaries = [
        SimpleNamespace(
            chapter_number=1,
            summary_text="第一章摘要",
            key_events=["黄亦玫进入建筑院"],
            unresolved_threads=["庄国栋仍未表态"],
            character_changes=["黄亦玫", {"character_name": "庄国栋"}],
            world_changes=[{"name": "建筑院"}],
        ),
        SimpleNamespace(
            chapter_number=2,
            summary_text="第二章摘要",
            key_events=["苏苏提醒黄亦玫谨慎"],
            unresolved_threads=[],
            character_changes=[],
            world_changes=[],
        ),
    ]
    session = SimpleNamespace(get=AsyncMock(return_value=project), execute=AsyncMock(return_value=FakeResult(summaries)))
    service = ContextService(session)  # type: ignore[arg-type]

    result = await service._get_distant_memory("project-id", 5, "本章里黄亦玫继续处理庄国栋与建筑院的关系")

    assert "相关远期记忆提要" in result
    assert "人物变化：黄亦玫、庄国栋" in result
    assert "设定变化：建筑院" in result


def test_render_recent_chapter_cards_and_relationship_memory_support_romance_fields():
    service = ContextService(session=None)  # type: ignore[arg-type]
    summaries = [
        SimpleNamespace(
            chapter_number=7,
            summary_text="两人在雨夜后都没有挑明心意。",
            emotional_tone="克制又摇晃",
            time_location="雨夜/钟楼外",
            unresolved_threads=["感情线索：沈夜误以为林疏准备离开", "场景钩子：林疏收到匿名短信", "下一章锚点：第二天的庆功宴", "匿名短信来源未明"],
            character_changes={
                "沈夜": {
                    "emotional_shift": "压下试探改为沉默观察",
                    "emotional_beats": ["想挽留却没开口", "听到林疏脚步离开后失落"],
                    "external_pressures": ["专案组盯得更紧"],
                    "relationships": [
                        {
                            "with": ["林疏"],
                            "change": "雨夜共处后默契上升",
                            "status": "暧昧未明",
                            "tension": "都在等对方先开口",
                            "emotional_shift": "靠近后又各自后退",
                            "external_pressure": ["庆功宴前必须统一口径"],
                        }
                    ],
                }
            },
            world_changes={},
            key_events=["雨夜共处"],
        )
    ]

    cards = service._render_recent_chapter_cards(summaries)
    relationship_memory = service._build_relationship_state_memory(summaries)

    assert "场景钩子：林疏收到匿名短信" in cards
    assert "下一章锚点：第二天的庆功宴" in cards
    assert "感情线索：沈夜误以为林疏准备离开" in cards
    assert "沈夜 ↔ 林疏" in relationship_memory
    assert "暧昧未明" in relationship_memory
    assert "情绪节拍：想挽留却没开口；听到林疏脚步离开后失落" in relationship_memory


def test_fit_to_budget_prefers_relationship_layers_when_writer_budget_is_tight():
    service = ContextService(session=None)  # type: ignore[arg-type]
    layers = [
        ("effective_system_prompt", "a" * 3200, True),
        ("current_outline", "b" * 2600, True),
        ("relevant_world_settings", "w" * 12000, False),
        ("global_prompt_digest", "g" * 6000, False),
        ("distant_memory", "d" * 20000, False),
        ("recent_chapter_cards", "c" * 16000, False),
        ("relationship_state_memory", "r" * 12000, False),
        ("previous_chapter_tail", "p" * 10000, False),
    ]

    fitted = dict(
        service._fit_to_budget(
            layers,
            target_tokens=14000,
            hard_limit_tokens=18000,
        )
    )
    total_tokens = sum(service.estimate_tokens(text) for text in fitted.values())

    assert total_tokens <= 18000
    assert service.estimate_tokens(fitted["relationship_state_memory"]) >= 2500
    assert service.estimate_tokens(fitted["previous_chapter_tail"]) >= 2500
    assert len(fitted["relevant_world_settings"]) < len("w" * 12000)
    assert len(fitted["global_prompt_digest"]) < len("g" * 6000)


@pytest.mark.asyncio
async def test_build_writer_context_passes_budget_overrides_into_fit_to_budget():
    project = SimpleNamespace(global_prompt="全局要求")
    outline = SimpleNamespace(outline_text="黄亦玫准备赴约")
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)
    service._get_relevant_characters = AsyncMock(return_value="黄亦玫: 建筑师")
    service._get_relevant_world_settings = AsyncMock(return_value="location/北京: 初夏")
    service._get_recent_context = AsyncMock(
        return_value={
            "previous_chapter_tail": "第4章末尾\n她最终还是没有回头。",
            "recent_chapter_cards": "第4章\n摘要：她与庄国栋短暂重逢。\n情绪：克制",
            "relationship_state_memory": "",
        }
    )
    service._get_distant_memory = AsyncMock(return_value="")
    service.build_retry_guidance = AsyncMock(return_value={"writer_feedback": "", "critic_review_history": "", "prompt_retry_info": {}})
    service._build_cross_chapter_review_insights = AsyncMock(return_value="")

    observed: dict[str, object] = {}
    original_fit_to_budget = service._fit_to_budget

    def capture_fit_to_budget(layers, target_tokens=None, hard_limit_tokens=None):
        observed["target_tokens"] = target_tokens
        observed["hard_limit_tokens"] = hard_limit_tokens
        return original_fit_to_budget(layers, target_tokens=target_tokens, hard_limit_tokens=hard_limit_tokens)

    service._fit_to_budget = capture_fit_to_budget  # type: ignore[method-assign]

    await service.build_writer_context(
        project_id="project-id",
        chapter_number=5,
        effective_system_prompt="系统提示词",
        budget_overrides={
            "target_tokens": 12000,
            "hard_limit_tokens": 18000,
        },
    )

    assert observed == {
        "target_tokens": 12000,
        "hard_limit_tokens": 18000,
    }


@pytest.mark.asyncio
async def test_build_critic_context_passes_budget_overrides_into_fit_to_budget():
    project = SimpleNamespace(global_prompt="总要求")
    outline = SimpleNamespace(outline_text="主角在钟楼追查密钥")
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    service = ContextService(session)  # type: ignore[arg-type]
    service._get_outline = AsyncMock(return_value=outline)
    service._get_relevant_characters = AsyncMock(return_value="沈夜: 调查员")
    service._get_relevant_world_settings = AsyncMock(return_value="rule/密钥: 只能在钟声后激活")
    service._render_recent_summaries = AsyncMock(return_value="第2章\n主角拿到地图")
    service._get_previous_chapter_tail = AsyncMock(return_value="第2章末尾\n钟声落下前，沈夜停步。")
    service.build_retry_guidance = AsyncMock(
        return_value={
            "writer_feedback": "",
            "critic_review_history": "同章重试复盘：\n- 上一轮缺少钟楼规则约束",
            "prompt_retry_info": {},
        }
    )
    service._build_cross_chapter_review_insights = AsyncMock(return_value="- 保持线索回收节奏")
    service._get_distant_memory = AsyncMock(return_value="远期伏笔：第一章提到失踪档案")

    observed: dict[str, object] = {}
    original_fit_to_budget = service._fit_to_budget

    def capture_fit_to_budget(layers, target_tokens=None, hard_limit_tokens=None):
        observed["target_tokens"] = target_tokens
        observed["hard_limit_tokens"] = hard_limit_tokens
        return original_fit_to_budget(layers, target_tokens=target_tokens, hard_limit_tokens=hard_limit_tokens)

    service._fit_to_budget = capture_fit_to_budget  # type: ignore[method-assign]

    await service.build_critic_context(
        project_id="project-id",
        chapter_number=5,
        effective_system_prompt="系统提示词",
        content="正文内容",
        budget_overrides={
            "target_tokens": 9000,
            "hard_limit_tokens": 13000,
        },
    )

    assert observed == {
        "target_tokens": 9000,
        "hard_limit_tokens": 13000,
    }
