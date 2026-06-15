import json
import os
import time

import httpx


BASE_URL = os.getenv("NOVEL_AI_BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        health = client.get("/healthz")
        health.raise_for_status()

        project = client.post(
            "/api/projects",
            json={
                "title": "Smoke Test Novel",
                "genre": "悬疑",
                "style": "冷峻",
                "global_prompt": "保持紧张悬疑风格，严格遵循大纲。",
                "total_chapters": 2,
                "auto_mode": True,
                "max_retries": 5,
            },
        )
        project.raise_for_status()
        project_id = project.json()["id"]

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

        models = client.get(f"/api/projects/{project_id}/models")
        models.raise_for_status()
        model_items = models.json()
        assert len(model_items) == 3
        assert any(
            item["role"] == "writer" and item.get("extra_config", {}).get("api_key_env_var") == "WRITER_API_KEY"
            for item in model_items
        )

        client.post(
            f"/api/projects/{project_id}/outlines/import",
            json=[
                {"chapter_number": 1, "outline_text": "主角收到委托并决定展开调查。"},
                {"chapter_number": 2, "outline_text": "主角遭遇阻碍并发现更大的阴谋。"},
            ],
        ).raise_for_status()

        client.post(f"/api/projects/{project_id}/start").raise_for_status()

        for _ in range(20):
            chapters = client.get(f"/api/projects/{project_id}/chapters")
            chapters.raise_for_status()
            data = chapters.json()
            if data and data[-1]["status"] == "passed":
                break
            time.sleep(1)

        export_resp = client.get(f"/api/projects/{project_id}/export")
        export_resp.raise_for_status()
        summaries = client.get(f"/api/projects/{project_id}/summaries")
        summaries.raise_for_status()
        summary_items = summaries.json()
        assert summary_items
        assert "emotional_tone" in summary_items[0]
        assert "time_location" in summary_items[0]

        characters = client.get(f"/api/projects/{project_id}/characters")
        characters.raise_for_status()

        world_settings = client.get(f"/api/projects/{project_id}/world-settings")
        world_settings.raise_for_status()

        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "model_count": len(model_items),
                    "summary_count": len(summary_items),
                    "character_count": len(characters.json()),
                    "world_setting_count": len(world_settings.json()),
                    "export_preview": export_resp.text[:120],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
