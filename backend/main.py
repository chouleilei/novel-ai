from fastapi import FastAPI

from backend.api.events import router as events_router
from backend.api.generation import router as generation_router
from backend.api.projects import router as projects_router
from backend.api.prompts import router as prompts_router
from backend.api.resources import router as resources_router
from backend.api.system import router as system_router
from backend.logging_config import setup_logging

setup_logging()

app = FastAPI(title="Novel AI System")

app.include_router(projects_router)
app.include_router(system_router)
app.include_router(generation_router)
app.include_router(prompts_router)
app.include_router(events_router)
app.include_router(resources_router)


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}
