import asyncio
import contextlib
import logging
from typing import Any

import httpx

from backend.config import get_settings
from backend.db.base import AsyncSessionLocal
from backend.services.generation_service import GenerationService
from backend.services.event_service import EventService
from backend.services.project_events import ProjectEventType, job_failed_payload
from backend.logging_config import setup_logging
from backend.worker.job_queue import JobQueue, DEFAULT_JOB_ERROR_MESSAGE
from backend.worker.pipeline import DEFERRED_RETRY_JOB_KEY, Pipeline

setup_logging()
logger = logging.getLogger(__name__)


def summarize_worker_error(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    if isinstance(exc, httpx.ReadTimeout):
        return "模型响应读取超时，请稍后重试。"
    if isinstance(exc, asyncio.TimeoutError):
        return "任务执行超时，请稍后重试。"
    error_type = exc.__class__.__name__.strip()
    if error_type and error_type != "Exception":
        return f"{error_type}（未提供详细错误信息）"
    return DEFAULT_JOB_ERROR_MESSAGE


def pop_deferred_retry_job(job) -> tuple[int, dict[str, object]] | None:
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else None
    if payload is None:
        return None

    deferred_retry = payload.pop(DEFERRED_RETRY_JOB_KEY, None)
    if not isinstance(deferred_retry, dict):
        return None

    chapter_number = deferred_retry.get("chapter_number")
    retry_payload = deferred_retry.get("payload", {})
    if not isinstance(chapter_number, int):
        return None
    if not isinstance(retry_payload, dict):
        retry_payload = {}
    return chapter_number, retry_payload


async def renew_job_lease(stop_event: asyncio.Event, job_id, project_id) -> None:
    settings = get_settings()
    interval = max(1.0, settings.worker_lease_seconds / 3)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            async with AsyncSessionLocal() as lease_session:
                renewed = await JobQueue(lease_session).renew_lease(job_id, project_id)
                await lease_session.commit()
            if not renewed:
                logger.warning("job lease renewal skipped because lease was lost", extra={"job_id": str(job_id)})
                break


async def monitor_lease_task(lease_task: asyncio.Task[Any], pipeline_task: asyncio.Task[Any]) -> None:
    await lease_task
    if not pipeline_task.done():
        pipeline_task.cancel()
        raise RuntimeError("lease keepalive stopped before pipeline finished")


async def stop_lease_task(stop_event: asyncio.Event, lease_task: asyncio.Task[Any] | None) -> None:
    stop_event.set()
    if lease_task is None:
        return
    try:
        await lease_task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("lease keepalive failed")


async def run_worker() -> None:
    settings = get_settings()
    while True:
        async with AsyncSessionLocal() as session:
            queue = JobQueue(session)
            job = await queue.claim_next_job()
            if job is None:
                await session.commit()
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue
            job_id = job.id
            project_id = job.project_id
            chapter_number = job.chapter_number
            job_type = job.job_type
            await session.commit()

            pipeline = Pipeline(session)
            lease_stop = asyncio.Event()
            lease_task = asyncio.create_task(renew_job_lease(lease_stop, job_id, project_id))
            pipeline_task: asyncio.Task[Any] | None = None
            lease_monitor_task: asyncio.Task[Any] | None = None
            try:
                claimed_job = await session.get(type(job), job_id)
                if claimed_job is None:
                    raise ValueError("任务不存在，可能已被删除")
                if job_type == "generate_chapter":
                    pipeline_task = asyncio.create_task(pipeline.process_generate_chapter(claimed_job))
                    lease_monitor_task = asyncio.create_task(monitor_lease_task(lease_task, pipeline_task))
                    done, pending = await asyncio.wait(
                        {pipeline_task, lease_monitor_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for pending_task in pending:
                        pending_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pending_task
                    await done.pop()
                else:
                    raise ValueError(f"暂未实现的任务类型: {job_type}")
                await stop_lease_task(lease_stop, lease_task)
                finished_job = await session.get(type(job), job_id)
                if finished_job is not None:
                    await queue.mark_done(finished_job)
                    deferred_retry = pop_deferred_retry_job(finished_job)
                    if deferred_retry is not None:
                        chapter_number, retry_payload = deferred_retry
                        await GenerationService(session)._enqueue_generate_job(
                            finished_job.project_id,
                            chapter_number,
                            payload=retry_payload,
                        )
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await stop_lease_task(lease_stop, lease_task)
                logger.exception("worker job failed")
                error_summary = summarize_worker_error(exc)
                await session.rollback()
                async with AsyncSessionLocal() as failure_session:
                    await EventService(failure_session).append(
                        project_id,
                        ProjectEventType.JOB_FAILED,
                        job_failed_payload(job_id=job_id, error=error_summary),
                        chapter_number=chapter_number,
                    )
                    failed_job = await failure_session.get(type(job), job_id)
                    if failed_job is not None:
                        await JobQueue(failure_session).mark_failed(failed_job, error_summary)
                    await failure_session.commit()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
