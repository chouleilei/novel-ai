# AGENTS.md

## Purpose
This is the root guide for coding agents working in this repository.
Read it together with `CLAUDE.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md`, and `scripts/CLAUDE.md`.
If instructions conflict, prefer the more local file for the module you are editing.

## Repository snapshot
This repo is a monorepo for a long-form novel generation system.
- `backend/`: FastAPI + async SQLAlchemy + PostgreSQL backend.
- `frontend/`: Vue 3 + Vite + Pinia + TypeScript frontend.
- `scripts/`: local init, smoke, and validation scripts.
- `docker-compose.yml`: full local stack for db + api + frontend + worker.

Treat the product as a database-backed generation state machine, not simple CRUD:
API writes project/chapter state -> worker claims `GenerationJob` -> pipeline runs -> events land in `ProjectEvent` -> frontend consumes HTTP + SSE.

## Rule files detected
Guidance files present: `CLAUDE.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md`, `scripts/CLAUDE.md`.
No `.cursor/rules/` entries, no `.cursorrules`, and no `.github/copilot-instructions.md` were found.

## Working principles
Before editing, identify which layer owns the behavior.
Do not push backend state-machine logic into API routers.
Do not change frontend realtime behavior without checking backend event emitters.
Do not add persistent fields before checking `backend/db/models/` and migrations.

When touching these cross-layer areas, inspect both sides:
- Prompt generation: `backend/services/prompt_service.py`, `backend/worker/pipeline.py`, `frontend/src/views/PromptEditor.vue`
- Realtime writing: `backend/api/events.py`, `backend/worker/pipeline.py`, `frontend/src/views/LiveWriting.vue`
- Resources/memory: `backend/services/memory_service.py`, `backend/api/resources.py`, `frontend/src/components/ProjectResourcesPanel.vue`
- Model configuration: `backend/config.py`, `backend/api/projects.py`, `backend/services/project_service.py`, `backend/services/runtime_service.py`, `frontend/src/utils/modelConfig.ts`

## Setup and development commands

### Backend setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env
```
Runtime-only deps:
```bash
pip install -r backend/requirements.txt
```

### Backend dev and tests
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
python -m backend.worker.runner
alembic upgrade head
python -m scripts.init_db
python -m pytest -q
python -m pytest -q backend/tests/test_prompt_service.py
python -m pytest -q backend/tests/test_prompt_service.py -k approved
```
`pytest.ini` sets `pythonpath = .`, so run pytest from repo root.

### Frontend
```bash
cd frontend && npm install
cd frontend && NOVEL_AI_VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
cd frontend && npm run build
cd frontend && npm run preview
```
There is no dedicated frontend lint or test script.
`npm run build` is the main frontend validation command and runs `vue-tsc -b` before Vite build.

### Docker Compose
```bash
cp .env.example .env
docker compose up -d --build
docker compose up -d api worker
docker compose up -d
```
Use `docker compose up -d api worker` after changing model endpoints, model names, or API keys in `.env`.
Use `docker compose up -d` after changing host port mappings.
Do not rely on `docker compose restart` to reload new `.env` values.

### Validation scripts
```bash
NOVEL_AI_BASE_URL=http://127.0.0.1:8000 python -m scripts.smoke_test
NOVEL_AI_BASE_URL=http://127.0.0.1:8050 python -m scripts.validate_five_chapter_flow
NOVEL_AI_BASE_URL=http://127.0.0.1:8050 python -m scripts.validate_retry_flow
NOVEL_AI_BASE_URL=http://127.0.0.1:8050 python -m scripts.validate_real_default_three_chapter_flow
```

## Lint, formatting, and static checks
No repo-level Ruff, Flake8, ESLint, or Prettier config files were detected.
Do not invent nonexistent lint commands.
Practical validation in this repo is backend `pytest`, frontend `cd frontend && npm run build`, and relevant `scripts/*.py` validation scripts.

## Backend style guide
Use absolute imports from `backend.*`.
Group imports as stdlib, third-party, then local imports with blank lines between groups.
Keep routers thin: validate input, map service errors to `HTTPException`, commit, and serialize responses.
Business rules, queueing, retries, and state transitions belong in services or worker code.
Prefer endpoint-local Pydantic `BaseModel` schemas for request/response payloads, and use `Field(...)` constraints instead of hand-written range validation where possible.
Follow existing async SQLAlchemy 2.x patterns: `select(...)`, `update(...)`, `or_(...)`, `await session.execute(stmt)`, and `result.scalar_one_or_none()` / `list(result.scalars())`.
ORM models use `Mapped[...]` with `mapped_column(...)`.
Enums use `class X(str, enum.Enum)` with UPPER_SNAKE members and lower-case persisted values.
Use `snake_case` for modules, functions, and variables; use `PascalCase` for classes, Pydantic models, and service classes.
Keep service filenames singular and descriptive, e.g. `generation_service.py`, `prompt_service.py`.
When serializing API responses, explicitly convert UUIDs to `str(...)`, datetimes with `serialize_datetime(...)`, and Numeric/Decimal values to `float(...)` where nearby code already does so.
Common pattern: services raise `ValueError`, routers convert that to `HTTPException(status_code=400, detail=str(exc))`; use `404` for missing project/chapter/prompt resources.
Broad `except Exception` should stay limited to boundary paths around LLM calls and fallback logic.
Before changing persisted state, inspect `backend/db/models/` first.

## Frontend style guide
Use Vue SFCs with `<script setup lang="ts">`.
Preserve the existing no-semicolon, single-quote TypeScript style.
Use `@/` path aliases for frontend-local imports.
Common import order is Vue imports, router or third-party imports, app-local value imports from `@/...`, then `import type` from `@/types`.
Prefer Composition API with `ref`, `computed`, and `watch`.
Local helpers are usually `const fn = (...) => { ... }` arrow functions.
Keep shared API/UI contracts in `frontend/src/types/index.ts` and update them together with affected stores/views when backend payloads change.
`frontend/tsconfig.app.json` enables `strict`, `noUnusedLocals`, and `noUnusedParameters`.
Do not add `@ts-ignore`, `@ts-expect-error`, or `as any`.
Route names are kebab-case strings, component files are `PascalCase.vue`, and store files are lower-case like `project.ts`.
Pinia stores usually define refs for state, async actions, loading/error handling inside actions, and return exposed state/actions from the store factory.
Axios response handling is centralized in `frontend/src/api/index.ts`; the interceptor returns `response.data`, so downstream code should expect business payloads, not full Axios responses.
Keep loose `Record<string, any>` or `unknown` only at genuinely dynamic payload edges.
Use typed `defineProps` and `defineEmits` when props/events are non-trivial.
Use `localeCompare(..., 'zh-CN')` for Chinese-facing sorting when nearby code already follows that pattern.

## Testing and verification expectations
If you change backend business logic, run targeted backend tests first.
If you change frontend code, run `cd frontend && npm run build`.
If you change orchestration, retries, prompts, resources, exports, or event flows, consider the `scripts/` validation scripts too.
There is currently no dedicated frontend unit test suite.
Do not claim frontend tests were run unless you actually ran the build or another concrete validation command.

## High-value files
- Backend entry: `backend/main.py`
- Job pipeline: `backend/worker/pipeline.py`
- Worker loop: `backend/worker/runner.py`
- Project orchestration: `backend/services/project_service.py`
- Generation control: `backend/services/generation_service.py`
- Prompt logic: `backend/services/prompt_service.py`
- Memory logic: `backend/services/memory_service.py`
- Frontend store hub: `frontend/src/stores/project.ts`
- Realtime page: `frontend/src/views/LiveWriting.vue`
- Type contracts: `frontend/src/types/index.ts`

## Final reminders
Match existing patterns before introducing new abstractions.
Read the local module `CLAUDE.md` before making non-trivial changes.
Prefer minimal, verifiable diffs.
When in doubt, trace the flow across API -> service -> worker/event -> frontend instead of patching one layer in isolation.
