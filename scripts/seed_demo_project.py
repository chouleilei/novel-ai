import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import AsyncSessionLocal
from backend.services.project_service import ProjectService


MOCK_CONFIGS = [
    {
        "role": "writer",
        "provider": "mock",
        "base_url": "http://mock.local",
        "model_name": "mock-writer",
        "temperature": 0.8,
        "max_tokens": 4000,
    },
    {
        "role": "critic",
        "provider": "mock",
        "base_url": "http://mock.local",
        "model_name": "mock-critic",
        "temperature": 0.2,
        "max_tokens": 2000,
    },
    {
        "role": "memory",
        "provider": "mock",
        "base_url": "http://mock.local",
        "model_name": "mock-memory",
        "temperature": 0.1,
        "max_tokens": 2000,
    },
]

DEMO_OUTLINES = [
    {"chapter_number": 1, "outline_text": "主角在雨夜接到神秘委托，决定前往废弃码头调查。"},
    {"chapter_number": 2, "outline_text": "主角在码头遭遇第一次正面冲突，并发现更大的阴谋线索。"},
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        service = ProjectService(session)
        project = await service.create_project(
            title="Demo Novel",
            genre="悬疑",
            style="紧张克制",
            global_prompt="全文保持中文长篇小说风格，强调悬疑推进和人物行动逻辑。",
            total_chapters=2,
            auto_mode=True,
            max_retries=5,
        )
        await service.save_model_configs(project.id, MOCK_CONFIGS)
        await service.import_outlines(project.id, DEMO_OUTLINES)
        await session.commit()
        print(project.id)


if __name__ == "__main__":
    asyncio.run(seed())

