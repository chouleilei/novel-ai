# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the `backend/` module.

> 导航：根文档 `/root/nv/CLAUDE.md` → `backend/`

## 模块职责

`backend/` 是 FastAPI + SQLAlchemy async 后端，负责：

- 项目 CRUD 与大纲导入
- 模型配置与连通性测试
- 章节生成控制（开始 / 暂停 / 继续 / 重试）
- Prompt 版本管理
- 摘要、角色、世界观、导出接口
- worker 任务编排与事件落库

## 本模块常用命令

```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
python -m backend.worker.runner
alembic upgrade head
python -m pytest -q
python -m pytest -q backend/tests/test_prompt_service.py
python -m pytest -q backend/tests/test_prompt_service.py -k approved
```

## 关键入口

- `backend/main.py`：FastAPI 应用入口与路由注册。
- `backend/api/projects.py`：项目、大纲、模型配置、模型测试接口。
- `backend/api/generation.py`：开始、暂停、恢复、重试与章节查询接口。
- `backend/api/events.py`：普通事件查询与 SSE 事件流。
- `backend/api/resources.py`：摘要、角色、世界观、导出等资源接口。

## 核心业务分层

### API 层

`backend/api/` 主要负责：

- Pydantic 请求校验
- HTTP 错误码与响应序列化
- 调用 service 完成业务操作

避免把核心状态迁移逻辑直接塞到 router。

### Service 层

- `project_service.py`：项目信息、默认模型配置、大纲导入重置逻辑。
- `generation_service.py`：项目开始/暂停/恢复/重试，决定何时向 `GenerationJob` 入队。
- `prompt_service.py`：章节 Prompt 生成、批准、回退与 fallback 逻辑；同时负责项目级 `global_prompt` 自动生成。
- `context_service.py`：writer / critic 上下文拼装、token 估算、重试反馈聚合、远期记忆裁剪。
- `memory_service.py`：连续性预检查、摘要生成、角色/世界观修订、远期记忆压缩与人工审核闸门。
- `event_service.py`：`ProjectEvent` 的统一写入与查询。
- `runtime_service.py`：按项目解析模型配置并创建运行时 client，负责 API Key 解析顺序（inline → encrypted → env var）。
- `model_connectivity_service.py`：对 writer / critic / memory / prompt_builder 做轻量冒烟测试，并校验结构化返回。
- `export_service.py`：导出 TXT / Markdown / EPUB。
- `secret_service.py`：项目级 API Key 加解密。

### Worker 层

- `worker/runner.py`：轮询任务、claim job、续租 lease、失败落事件。
- `worker/job_queue.py`：任务 claim / renew / finish / fail。
- `worker/pipeline.py`：真实生成流水线。

理解任务系统时，重点看 `runner.py` + `job_queue.py` + `pipeline.py` 的协作，而不是只看单个 service。

### service 协作主链路

最常见的运行时调用链是：

`GenerationService` 入队 → `worker/runner.py` claim job → `Pipeline` 调 `MemoryService` / `PromptService` / `ContextService` / `RuntimeService` → `EventService` 落事件 → `MemoryService` 回写 summary / revision / distant memory → `ExportService` 供阅读页和下载接口使用。

## 状态机关注点

### 项目状态

常见项目状态：`draft` → `ready` → `running` → `paused` / `completed` / `failed`

### 章节状态

常见章节状态：`pending` → `queued` → `writing` → `reviewing` → `passed` / `failed` / `paused`

### 重要行为

- 导入大纲会清空旧章节、任务、事件、Prompt、摘要与记忆修订数据。
- `pause` 不一定立刻中断当前模型调用，而是先取消排队任务并等待当前阶段尽快中断。
- `resume` 会优先恢复卡在 inflight 状态的章节。
- `retry` 会给 job payload 加 `retry=True`，Prompt 也可能因此被强制重建。
- memory 阶段若发现高风险修订，项目会自动暂停等待人工复核。

## 数据模型入口

统一导出在 `backend/db/models/__init__.py`，具体定义分散在：

- `project.py`
- `chapter.py`
- `prompt.py`
- `job.py`
- `event.py`
- `memory.py`

### 关键实体心智模型

- `Project`：项目主状态、全局提示词、当前推进章节、远期记忆缓存。
- `ProjectModelConfig`：四类模型角色配置，`project_id + role` 唯一。
- `ChapterOutline`：章节大纲源数据。
- `Chapter`：章节状态、最终正文、最终得分、重试计数。
- `ChapterAttempt`：每次写作尝试的输入快照、token 与正文。
- `ChapterReview`：critic 评分与阻断问题。
- `ChapterPrompt`：章节 Prompt 版本历史，包含 generated / edited / approved / superseded。
- `GenerationJob`：后台任务队列、lease、payload（如 `retry` / `resume`）。
- `ProjectEvent`：SSE 的事实来源。
- `ChapterSummary` / `Character` / `WorldSetting`：记忆层当前态。
- `CharacterRevision` / `WorldSettingRevision`：记忆层变更提案，可自动应用或等待人工审核。

涉及持久化改动时，先确认对应字段是否已存在于模型和迁移中。

## 测试分布

测试位于 `backend/tests/`，当前重点覆盖：

- PromptService
- Project / model config 校验
- GenerationService
- MemoryService
- Runtime / model connectivity
- Pipeline 规则
- Resources 接口

### 测试地图

- `test_prompt_service.py`：批准 Prompt 复用、重试时 builder fallback、global prompt 自动生成、fallback diagnostics。
- `test_context_service.py`：上下文裁剪预算、跨章节评审洞察、retry guidance 聚合、远期记忆拼接。
- `test_memory_service.py`：高风险 revision 的人工审核判定、远期记忆压缩 fallback。
- `test_generation_service.py`：暂停 / 恢复 / 重试时的状态迁移与入队行为。
- `test_pipeline_rules.py`：critic 评分归一化、通过门槛、重试链路、Prompt 超时回退继续执行。
- `test_resources.py`：导出响应头与文件名编码。
- `test_model_connectivity_service.py` / `test_runtime_service.py`：模型连通性与 API Key 解析。

如果改的是状态迁移或流水线逻辑，优先找同名 service / API / pipeline 测试。

## 改动提示

- 实时页面问题通常不只改后端接口，还要核对发出的事件类型是否与前端消费一致。
- 模型配置相关改动通常同时影响 `config.py`、`projects.py`、`project_service.py`、`runtime_service.py`。
- Prompt 相关问题通常横跨 `prompt_service.py`、`context_service.py`、`worker/pipeline.py`。
