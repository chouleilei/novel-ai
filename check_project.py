import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import uuid

async def run():
    engine = create_async_engine('postgresql+asyncpg://postgres:postgres@localhost:5432/novel_ai')
    async with engine.connect() as conn:
        # Find project
        res = await conn.execute(text("SELECT id, title, status, auto_accept_critic_failed, max_retries FROM projects WHERE title = '曾少年'"))
        project = res.fetchone()
        if not project:
            print("Project not found")
            return
        
        project_id = project[0]
        print(f"Project: {project}")

        # Check latest chapter and its events
        res = await conn.execute(text(f"SELECT id, chapter_number, status, retry_count, last_error FROM chapters WHERE project_id = '{project_id}' ORDER BY chapter_number DESC LIMIT 1"))
        chapter = res.fetchone()
        print(f"Latest Chapter: {chapter}")
        
        if chapter:
            chapter_number = chapter[1]
            # Check latest events for this chapter
            res = await conn.execute(text(f"SELECT id, event_type, payload, chapter_number, created_at FROM project_events WHERE project_id = '{project_id}' AND chapter_number = {chapter_number} ORDER BY created_at DESC LIMIT 10"))
            events = res.fetchall()
            print("\nRecent Events for Chapter:")
            for e in events:
                print(e)
                
            # Check jobs
            res = await conn.execute(text(f"SELECT id, status, job_type, run_count, last_error, payload FROM generation_jobs WHERE project_id = '{project_id}' AND chapter_number = {chapter_number} ORDER BY created_at DESC"))
            jobs = res.fetchall()
            print("\nJobs for Chapter:")
            for j in jobs:
                print(j)

    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(run())
