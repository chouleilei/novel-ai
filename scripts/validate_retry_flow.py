import json
import os
import time

import httpx


BASE_URL = os.getenv("NOVEL_AI_BASE_URL", "http://127.0.0.1:8050")
POLL_INTERVAL_SECONDS = 2
MAX_WAIT_SECONDS = 180


def wait_for_retry_count(client: httpx.Client, project_id: str, chapter_number: int, minimum_retry_count: int) -> tuple[dict, dict]:
    deadline = time.time() + MAX_WAIT_SECONDS
    last_project: dict = {}
    last_chapter: dict = {}
    while time.time() < deadline:
        project_resp = client.get(f"/api/projects/{project_id}")
        project_resp.raise_for_status()
        last_project = project_resp.json()

        chapter_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}")
        chapter_resp.raise_for_status()
        last_chapter = chapter_resp.json()

        if int(last_chapter.get("retry_count") or 0) >= minimum_retry_count:
            return last_project, last_chapter
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_project, last_chapter


def wait_for_chapter_status(client: httpx.Client, project_id: str, chapter_number: int, statuses: set[str]) -> tuple[dict, dict]:
    deadline = time.time() + MAX_WAIT_SECONDS
    last_project: dict = {}
    last_chapter: dict = {}
    while time.time() < deadline:
        project_resp = client.get(f"/api/projects/{project_id}")
        project_resp.raise_for_status()
        last_project = project_resp.json()

        chapter_resp = client.get(f"/api/projects/{project_id}/chapters/{chapter_number}")
        chapter_resp.raise_for_status()
        last_chapter = chapter_resp.json()

        if last_chapter.get("status") in statuses:
            return last_project, last_chapter
        time.sleep(POLL_INTERVAL_SECONDS)
    return last_project, last_chapter


def main() -> None:
    outlines = [
        {
            "chapter_number": 1,
            "outline_text": "沈夜接下调查旧档案失踪案的委托，在钟楼下第一次发现怀表密钥，并决定当夜潜入档案室继续追查。",
        }
    ]

    with httpx.Client(base_url=BASE_URL, timeout=20) as client:
        health = client.get("/healthz")
        health.raise_for_status()

        project_resp = client.post(
            "/api/projects",
            json={
                "title": "Retry Validation Novel",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "保持冷峻压迫感，严格遵循章节大纲，关键行动必须明确落地。",
                "total_chapters": 1,
                "auto_mode": True,
                "max_retries": 3,
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
                    "model_name": "mock-writer-retry-aware",
                    "temperature": 0.8,
                    "max_tokens": 4000,
                    "extra_config": {"api_key_env_var": "WRITER_API_KEY"},
                },
                {
                    "role": "critic",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-critic-retry-aware",
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
                {
                    "role": "prompt_builder",
                    "provider": "mock",
                    "base_url": "http://mock.local",
                    "model_name": "mock-prompt-builder-retry-aware",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "extra_config": {"api_key_env_var": "PROMPT_BUILDER_API_KEY"},
                },
            ],
        ).raise_for_status()

        client.post(f"/api/projects/{project_id}/outlines/import", json=outlines).raise_for_status()
        client.post(f"/api/projects/{project_id}/start").raise_for_status()

        project_state, chapter_one = wait_for_retry_count(client, project_id, 1, 1)
        if int(chapter_one.get("retry_count") or 0) < 1:
            raise RuntimeError(json.dumps({"project_id": project_id, "phase": "after_first_failure", "project": project_state, "chapter_one": chapter_one}, ensure_ascii=False))

        final_project, final_chapter_one = wait_for_chapter_status(client, project_id, 1, {"passed", "failed"})
        if final_chapter_one.get("status") != "passed":
            raise RuntimeError(json.dumps({"project_id": project_id, "phase": "final", "project": final_project, "chapter_one": final_chapter_one}, ensure_ascii=False))

        prompts_resp = client.get(f"/api/projects/{project_id}/chapters/1/prompts")
        prompts_resp.raise_for_status()
        prompts = prompts_resp.json()

        attempts_resp = client.get(f"/api/projects/{project_id}/chapters/1/attempts")
        attempts_resp.raise_for_status()
        attempts = attempts_resp.json()

        reviews_resp = client.get(f"/api/projects/{project_id}/chapters/1/reviews")
        reviews_resp.raise_for_status()
        reviews = reviews_resp.json()

        events_resp = client.get(f"/api/projects/{project_id}/events")
        events_resp.raise_for_status()
        events = events_resp.json()

        if len(prompts) < 2:
            raise RuntimeError(json.dumps({"project_id": project_id, "reason": "prompt_versions_insufficient", "prompts": prompts}, ensure_ascii=False))
        if len(attempts) < 2:
            raise RuntimeError(json.dumps({"project_id": project_id, "reason": "attempts_insufficient", "attempts": attempts}, ensure_ascii=False))
        if len(reviews) < 2:
            raise RuntimeError(json.dumps({"project_id": project_id, "reason": "reviews_insufficient", "reviews": reviews}, ensure_ascii=False))

        retry_events = [item for item in events if item["event_type"] == "chapter_rewriting"]
        if not retry_events:
            raise RuntimeError(json.dumps({"project_id": project_id, "reason": "missing_retry_event"}, ensure_ascii=False))

        first_attempt, second_attempt = attempts[0], attempts[1]
        first_review, second_review = reviews[0], reviews[1]
        latest_prompt = prompts[0]
        previous_prompt = prompts[1]

        result = {
            "project_id": project_id,
            "project_status": final_project.get("status"),
            "chapter_status": final_chapter_one.get("status"),
            "retry_count": final_chapter_one.get("retry_count"),
            "prompt_versions": len(prompts),
            "attempt_count": len(attempts),
            "review_count": len(reviews),
            "first_attempt_status": first_attempt["status"],
            "second_attempt_status": second_attempt["status"],
            "first_review_passed": first_review["passed"],
            "second_review_passed": second_review["passed"],
            "latest_prompt_excerpt": latest_prompt["generated_system_prompt"][:160],
            "previous_prompt_excerpt": previous_prompt["generated_system_prompt"][:160],
            "second_attempt_excerpt": (second_attempt.get("content") or "")[:160],
            "retry_event_count": len(retry_events),
        }
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
