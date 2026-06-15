import json
import os
import time
from collections import Counter

import httpx


BASE_URL = os.getenv("NOVEL_AI_BASE_URL", "http://127.0.0.1:8050")
POLL_INTERVAL_SECONDS = 10
MAX_WAIT_SECONDS = 1800
OUTLINES = [
    {
        "chapter_number": 1,
        "outline_text": "沈夜接下调查旧档案失踪案的委托，在钟楼下第一次发现怀表密钥，并确认它与失踪档案有关。",
        "tags": {},
    },
    {
        "chapter_number": 2,
        "outline_text": "沈夜潜入旧档案室，发现管理员刻意隐瞒封锁机制，并记下第二把密钥流向特审办的线索。",
        "tags": {},
    },
    {
        "chapter_number": 3,
        "outline_text": "林疏在雨夜现身，与沈夜交换条件，明确抛出关于β-12和旧警备系统的交易，并逼沈夜在信任与怀疑之间作出选择。",
        "tags": {},
    },
]


def wait_for_terminal_state(client: httpx.Client, project_id: str) -> tuple[dict, list[dict]]:
    deadline = time.time() + MAX_WAIT_SECONDS
    last_project: dict | None = None
    last_chapters: list[dict] = []
    while time.time() < deadline:
        project = client.get(f"/api/projects/{project_id}")
        project.raise_for_status()
        last_project = project.json()

        chapters = client.get(f"/api/projects/{project_id}/chapters")
        chapters.raise_for_status()
        last_chapters = chapters.json()

        if last_project["status"] in {"completed", "paused", "failed"}:
            return last_project, last_chapters
        time.sleep(POLL_INTERVAL_SECONDS)

    assert last_project is not None
    return last_project, last_chapters


def fetch_per_chapter(client: httpx.Client, project_id: str, chapter_number: int) -> dict:
    detail = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}")
    detail.raise_for_status()

    prompts = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}/prompts")
    prompts.raise_for_status()

    attempts = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}/attempts")
    attempts.raise_for_status()

    reviews = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}/reviews")
    reviews.raise_for_status()

    return {
        "detail": detail.json(),
        "prompt_versions": len(prompts.json()),
        "attempts": [
            {
                "attempt_no": item["attempt_no"],
                "status": item["status"],
                "content_len": len(item.get("content") or ""),
                "tail": (item.get("content") or "")[-160:],
            }
            for item in attempts.json()
        ],
        "reviews": reviews.json(),
    }


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=180) as client:
        health = client.get("/healthz")
        health.raise_for_status()

        project_resp = client.post(
            "/api/projects",
            json={
                "title": "Real Default 3 Chapter Validation",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "保持冷峻悬疑风格，严格遵循章节大纲，注意线索回收、连续性与章节收束。",
                "total_chapters": 3,
                "auto_mode": True,
                "max_retries": 5,
            },
        )
        project_resp.raise_for_status()
        project_id = project_resp.json()["id"]

        models_resp = client.get(f"/api/projects/{project_id}/models")
        models_resp.raise_for_status()
        models = models_resp.json()

        connectivity_results: dict[str, dict] = {}
        for role in ["writer", "critic", "memory", "prompt_builder"]:
            payload = next(item for item in models if item["role"] == role)
            test_resp = client.post(f"/api/projects/{project_id}/models/test", json=payload)
            test_resp.raise_for_status()
            connectivity_results[role] = test_resp.json()
            if not connectivity_results[role]["success"]:
                raise RuntimeError(json.dumps({"project_id": project_id, "role": role, "result": connectivity_results[role]}, ensure_ascii=False))

        import_resp = client.post(f"/api/projects/{project_id}/outlines/import", json=OUTLINES)
        import_resp.raise_for_status()

        start_resp = client.post(f"/api/projects/{project_id}/start")
        start_resp.raise_for_status()

        project, chapters = wait_for_terminal_state(client, project_id)

        events_resp = client.get(f"/api/projects/{project_id}/events")
        events_resp.raise_for_status()
        events = events_resp.json()
        event_counts = Counter(item["event_type"] for item in events)

        summaries_resp = client.get(f"/api/projects/{project_id}/summaries")
        summaries_resp.raise_for_status()
        summaries = summaries_resp.json()

        export_resp = client.get(f"/api/projects/{project_id}/export?format=txt")
        export_resp.raise_for_status()
        export_text = export_resp.text

        result = {
            "project": project,
            "chapters": chapters,
            "connectivity": connectivity_results,
            "event_counts": dict(event_counts),
            "event_tail": events[:12],
            "summary_count": len(summaries),
            "export_preview": export_text[:500],
            "per_chapter": {
                str(number): fetch_per_chapter(client, project_id, number)
                for number in range(1, 4)
            },
        }

        if project["status"] != "completed":
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        if not chapters or not all(item["status"] == "passed" for item in chapters):
            raise RuntimeError(json.dumps(result, ensure_ascii=False))

        required_events = [
            "prompt_generated",
            "writer_non_stream_started",
            "writer_non_stream_succeeded",
            "chapter_scored",
            "memory_updated",
            "chapter_passed",
            "pipeline_complete",
        ]
        missing_events = [event for event in required_events if event_counts.get(event, 0) == 0]
        if missing_events:
            raise RuntimeError(json.dumps({"project_id": project_id, "missing_events": missing_events, "event_counts": dict(event_counts)}, ensure_ascii=False))

        if len(summaries) < 3:
            raise RuntimeError(json.dumps({"project_id": project_id, "summary_count": len(summaries)}, ensure_ascii=False))

        if "第1章" not in export_text or "第3章" not in export_text:
            raise RuntimeError(json.dumps({"project_id": project_id, "export_preview": export_text[:200]}, ensure_ascii=False))

        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
