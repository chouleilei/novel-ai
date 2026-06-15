import json
import os
import time
from collections import Counter

import httpx


BASE_URL = os.getenv("NOVEL_AI_BASE_URL", "http://127.0.0.1:8050")
POLL_INTERVAL_SECONDS = 2
MAX_WAIT_SECONDS = 180


def wait_for_project_terminal_state(client: httpx.Client, project_id: str) -> list[dict]:
    deadline = time.time() + MAX_WAIT_SECONDS
    last_chapters: list[dict] = []
    while time.time() < deadline:
        chapters = client.get(f"/api/projects/{project_id}/chapters")
        chapters.raise_for_status()
        last_chapters = chapters.json()
        if last_chapters and all(item["status"] == "passed" for item in last_chapters):
            return last_chapters
        if any(item["status"] == "failed" for item in last_chapters):
            return last_chapters
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_chapters


def main() -> None:
    outlines = [
        {"chapter_number": 1, "outline_text": "沈夜接下调查旧档案失踪案的委托，在钟楼下第一次发现密钥与失踪档案有关。"},
        {"chapter_number": 2, "outline_text": "沈夜潜入旧档案室，发现管理员刻意隐瞒封锁机制，并记下第二把密钥的线索。"},
        {"chapter_number": 3, "outline_text": "林疏在雨夜现身，与沈夜交换条件，逼他在信任与怀疑之间作出选择。"},
        {"chapter_number": 4, "outline_text": "沈夜根据前文线索进入钟楼内部，确认失踪档案与旧警备系统存在关联。"},
        {"chapter_number": 5, "outline_text": "沈夜在钟楼顶层公开揭出管理员的谎言，并保留一条仍未彻底解开的后续伏笔。"},
    ]

    with httpx.Client(base_url=BASE_URL, timeout=20) as client:
        health = client.get("/healthz")
        health.raise_for_status()

        project_resp = client.post(
            "/api/projects",
            json={
                "title": "Five Chapter Validation Novel",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "保持冷峻悬疑风格，严格遵循章节大纲，注意线索回收与连续性。",
                "total_chapters": 5,
                "auto_mode": True,
                "max_retries": 5,
            },
        )
        project_resp.raise_for_status()
        project_id = project_resp.json()["id"]

        client.put(
            f"/api/projects/{project_id}/models",
            json=[
                {
                    "role": "writer",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-writer",
                    "temperature": 0.8,
                    "max_tokens": 4000,
                    "extra_config": {"api_key_env_var": "WRITER_API_KEY"},
                },
                {
                    "role": "critic",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-critic",
                    "temperature": 0.2,
                    "max_tokens": 2000,
                    "extra_config": {"api_key_env_var": "CRITIC_API_KEY"},
                },
                {
                    "role": "memory",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-memory",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "extra_config": {"api_key_env_var": "MEMORY_API_KEY"},
                },
            ],
        ).raise_for_status()

        client.post(f"/api/projects/{project_id}/outlines/import", json=outlines).raise_for_status()
        client.post(f"/api/projects/{project_id}/start").raise_for_status()

        chapters = wait_for_project_terminal_state(client, project_id)
        if not chapters or not all(item["status"] == "passed" for item in chapters):
            raise RuntimeError(json.dumps({"project_id": project_id, "chapters": chapters}, ensure_ascii=False))

        events_resp = client.get(f"/api/projects/{project_id}/events")
        events_resp.raise_for_status()
        events = events_resp.json()
        event_counter = Counter(item["event_type"] for item in events)

        prompt_versions: dict[int, list[dict]] = {}
        attempts_by_chapter: dict[int, list[dict]] = {}
        reviews_by_chapter: dict[int, list[dict]] = {}
        chapter_details: dict[int, dict] = {}

        for chapter_no in range(1, 6):
            prompt_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_no}/prompts")
            prompt_resp.raise_for_status()
            prompt_versions[chapter_no] = prompt_resp.json()

            attempts_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_no}/attempts")
            attempts_resp.raise_for_status()
            attempts_by_chapter[chapter_no] = attempts_resp.json()

            reviews_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_no}/reviews")
            reviews_resp.raise_for_status()
            reviews_by_chapter[chapter_no] = reviews_resp.json()

            chapter_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_no}")
            chapter_resp.raise_for_status()
            chapter_details[chapter_no] = chapter_resp.json()

        summaries_resp = client.get(f"/api/projects/{project_id}/summaries")
        summaries_resp.raise_for_status()
        summaries = summaries_resp.json()

        export_resp = client.get(f"/api/projects/{project_id}/export?format=txt")
        export_resp.raise_for_status()
        export_text = export_resp.text

        required_events = [
            "prompt_generated",
            "chapter_scored",
            "memory_updated",
            "chapter_passed",
            "pipeline_complete",
        ]
        missing_events = [event for event in required_events if event_counter.get(event, 0) == 0]
        if missing_events:
            raise RuntimeError(json.dumps({"project_id": project_id, "missing_events": missing_events}, ensure_ascii=False))

        if len(summaries) < 5:
            raise RuntimeError(json.dumps({"project_id": project_id, "summary_count": len(summaries)}, ensure_ascii=False))

        if "第1章" not in export_text or "第5章" not in export_text:
            raise RuntimeError(json.dumps({"project_id": project_id, "export_preview": export_text[:200]}, ensure_ascii=False))

        result = {
            "project_id": project_id,
            "event_counts": dict(event_counter),
            "prompt_versions": {key: len(value) for key, value in prompt_versions.items()},
            "attempt_counts": {key: len(value) for key, value in attempts_by_chapter.items()},
            "review_counts": {key: len(value) for key, value in reviews_by_chapter.items()},
            "chapter_scores": {key: chapter_details[key]["final_score"] for key in chapter_details},
            "chapter_improvement_notes": {key: chapter_details[key]["improvement_notes"] for key in chapter_details},
            "summary_count": len(summaries),
            "export_preview": export_text[:200],
        }
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
