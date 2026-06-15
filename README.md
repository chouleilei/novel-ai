# Novel AI - 长篇小说 AI 生成系统

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-brightgreen.svg)](https://vuejs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)

一个面向长篇中文小说创作的 AI 自动化生成系统，采用"三智能体 + 可选提示词构建子模块"的架构方案，支持从项目配置、大纲导入、自动生成、实时监控到导出的完整生产闭环。

## ✨ 核心特性

- 🤖 **多智能体协作**：Writer（创作）、Critic（评审）、Memory（记忆）三角色分工明确
- 📝 **提示词版本化**：章节级系统提示词自动生成、版本管理、人工微调
- 🔄 **可恢复的任务系统**：基于 PostgreSQL 的持久化任务队列，支持暂停/继续/重试
- 📊 **实时可视化监控**：SSE 事件流驱动的实时写作面板，流式展示生成过程
- 🧠 **智能记忆管理**：连续性预检查、摘要生成、人物/世界观资源维护
- 🛡️ **人工审核闸门**：高风险设定变更自动暂停，等待人工审核
- 📤 **多格式导出**：支持 TXT、Markdown、EPUB 格式导出
- 🐳 **容器化部署**：Docker Compose 一键启动全部服务

## 🏗️ 系统架构

```
┌────────────────────────────────────────────────────────────┐
│                     用户浏览器 (Vue 3)                      │
│    项目管理 / 大纲编辑 / 提示词微调 / 实时写作监控 / 阅读导出   │
└────────────────────────────┬───────────────────────────────┘
                             │ HTTP / SSE
                             ▼
┌────────────────────────────────────────────────────────────┐
│                  Nginx 反向代理 + Basic Auth                │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────┐
│                      FastAPI API 服务                       │
│     项目配置 / 提示词管理 / 任务控制 / SSE 订阅 / 导出接口     │
└───────────────┬──────────────────────────────┬─────────────┘
                │                              │
                │ 写入任务 / 读取状态            │ 读取事件流
                ▼                              ▼
┌────────────────────────────┐     ┌─────────────────────────┐
│       PostgreSQL 数据库     │     │      SSE 事件读取层      │
│ projects / chapters / jobs │     │ project_events + cursor │
│ attempts / reviews / events│     └─────────────────────────┘
└───────────────┬────────────┘
                │ 轮询 claim_job / 写入结果
                ▼
┌────────────────────────────────────────────────────────────┐
│                    Worker 生成执行进程                       │
│  Prompt Builder / Context Manager / Writer / Critic /      │
│  Memory / Export / Retry / Pause / Resume                  │
└───────┬────────────────────┬───────────────────┬───────────┘
        ▼                    ▼                   ▼
   Writer Model         Critic Model        Memory Model
```

### 智能体职责

| 智能体 | 职责 |
|--------|------|
| **Writer** | 负责正文创作，流式输出章节内容 |
| **Critic** | 结构化评审与验收，检查大纲遵循度、提示词遵循度、连续性、人物一致性、文笔质量 |
| **Memory** | 连续性预检查、摘要生成、人物/世界观更新、远期记忆压缩 |
| **Prompt Builder** | 可选角色，用于自动生成章节级系统提示词；未配置时回退到 Memory |

## 🛠️ 技术栈

### 后端

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| ORM / 数据库 | SQLAlchemy 2.x + PostgreSQL + JSONB |
| 配置管理 | Pydantic Settings |
| 数据库迁移 | Alembic |
| 异步驱动 | asyncpg |
| HTTP 客户端 | httpx |
| 服务启动 | uvicorn |
| 测试 | pytest, pytest-asyncio |

### 前端

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3 |
| 语言 | TypeScript |
| 构建工具 | Vite |
| 状态管理 | Pinia |
| 路由 | Vue Router |
| HTTP | Axios |
| UI 样式 | Tailwind CSS v4 |

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

1. **克隆项目并准备环境变量**

```bash
cd /root/nv
cp .env.example .env
```

2. **启动全部服务**

```bash
docker compose up -d --build
```

3. **访问服务**

- 🌐 Web 界面：`http://127.0.0.1:8051`
- 🔌 API 服务：`http://127.0.0.1:8050`
- ❤️ 健康检查：`http://127.0.0.1:8050/healthz`
- 🗄️ PostgreSQL：`127.0.0.1:15432`

### 方式二：本地开发

#### 1. 后端设置

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r backend/requirements.txt

# 准备环境变量
cp .env.example .env
```

#### 2. 启动 PostgreSQL

```bash
docker run --name novel-ai-db \
  -e POSTGRES_DB=novel_ai \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -d postgres:16-alpine
```

#### 3. 初始化数据库

```bash
# 推荐使用 Alembic 迁移
alembic upgrade head

# 或使用快速建表脚本
python -m scripts.init_db
```

#### 4. 启动 API 服务

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 5. 启动 Worker

```bash
# 新开终端
python -m backend.worker.runner
```

#### 6. 启动前端

```bash
# 新开终端
cd frontend
npm install
NOVEL_AI_VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

## 📁 项目结构

```
/root/nv/
├── backend/                    # 后端服务
│   ├── api/                   # HTTP 路由层
│   │   ├── projects.py       # 项目 CRUD、模型配置、大纲导入
│   │   ├── generation.py     # 生成控制（开始/暂停/继续/重试）
│   │   ├── prompts.py        # 提示词生成与版本管理
│   │   ├── events.py         # SSE 事件流
│   │   └── resources.py      # 摘要、角色、世界观、导出
│   ├── services/              # 核心业务逻辑层
│   │   ├── project_service.py
│   │   ├── generation_service.py
│   │   ├── prompt_service.py
│   │   ├── context_service.py
│   │   ├── memory_service.py
│   │   └── export_service.py
│   ├── db/                    # 数据库相关
│   │   ├── models/           # SQLAlchemy 数据模型
│   │   └── migrations/       # Alembic 迁移脚本
│   ├── worker/                # 后台任务处理
│   │   ├── runner.py         # Worker 主循环
│   │   ├── job_queue.py      # 任务队列管理
│   │   └── pipeline.py       # 章节生成流水线
│   ├── llm/                   # LLM 客户端抽象
│   ├── prompts/               # 系统提示词模板
│   └── tests/                 # 后端单元测试
├── frontend/                   # 前端应用
│   └── src/
│       ├── views/             # 页面级视图
│       │   ├── ProjectList.vue
│       │   ├── ProjectSetup.vue
│       │   ├── LiveWriting.vue
│       │   ├── PromptEditor.vue
│       │   └── NovelReader.vue
│       ├── components/        # 可复用业务组件
│       ├── stores/            # Pinia 状态管理
│       ├── api/               # Axios 实例
│       └── router/            # 路由配置
├── scripts/                    # 工具脚本
│   ├── init_db.py            # 数据库初始化
│   ├── seed_demo_project.py  # 种子数据
│   └── smoke_test.py         # 烟雾测试
├── docker-compose.yml          # 容器编排
├── alembic.ini                 # Alembic 配置
└── .env.example               # 环境变量示例
```

## 🎯 核心功能

### 1. 项目管理

- 创建、编辑、删除项目
- 配置项目基本信息（书名、题材、风格、全局提示词）
- 项目状态管理：draft → ready → running → paused/completed/failed
- 支持自动模式和手动模式

### 2. 模型配置

- 支持 4 种模型角色：writer、critic、memory、prompt_builder（可选）
- 支持 mock 和 openai_compatible 两种 provider
- API Key 安全策略：只保存环境变量名，不保存真实密钥
- 按项目独立配置，互不影响

### 3. 大纲与提示词

- 批量导入章节大纲（空行分隔）
- 全局系统提示词自动生成
- 章节级系统提示词版本化管理
- 支持生成、编辑、批准、回溯提示词版本

### 4. 自动生成

- 流式生成正文内容
- 结构化评审与验收（5 个维度评分）
- 自动重试机制（可配置最大重试次数）
- 高风险设定变更自动暂停

### 5. 实时监控

- SSE 事件流驱动的实时面板
- 流式展示 Writer 输出
- 实时展示 Critic 评分
- 资源面板（摘要、角色、世界观、审核提案）

### 6. 记忆管理

- 连续性预检查
- 章节摘要生成
- 人物/世界观资源更新
- 远期记忆压缩
- 高风险变更人工审核

### 7. 导出功能

- 支持 TXT、Markdown、EPUB 格式
- 基于已通过章节的 final_content 导出

## 🔌 API 接口

### 项目接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects` | 获取项目列表 |
| GET | `/api/projects/{id}` | 获取项目详情 |
| PUT | `/api/projects/{id}` | 更新项目信息 |
| DELETE | `/api/projects/{id}` | 删除项目 |
| PUT | `/api/projects/{id}/models` | 保存模型配置 |
| GET | `/api/projects/{id}/models` | 读取模型配置 |
| POST | `/api/projects/{id}/outlines/import` | 导入章节大纲 |

### 生成控制

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/{id}/start` | 启动生成 |
| POST | `/api/projects/{id}/pause` | 暂停项目 |
| POST | `/api/projects/{id}/resume` | 继续项目 |
| POST | `/api/projects/{id}/chapters/{num}/retry` | 重试章节 |
| GET | `/api/projects/{id}/chapters` | 获取章节列表 |
| GET | `/api/projects/{id}/chapters/{num}` | 获取章节详情 |

### 提示词管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/{id}/global-prompt/generate` | 生成全局提示词 |
| POST | `/api/projects/{id}/chapters/{num}/prompt/generate` | 生成章节提示词 |
| GET | `/api/projects/{id}/chapters/{num}/prompts` | 获取提示词版本列表 |
| PUT | `/api/projects/{id}/chapters/{num}/prompts/{ver}` | 编辑提示词版本 |
| POST | `/api/projects/{id}/chapters/{num}/prompt/approve` | 批准提示词版本 |

### 事件与资源

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects/{id}/events` | 获取历史事件 |
| GET | `/api/projects/{id}/events/stream` | SSE 实时事件流 |
| GET | `/api/projects/{id}/summaries` | 获取章节摘要 |
| GET | `/api/projects/{id}/characters` | 获取角色资源 |
| GET | `/api/projects/{id}/world-settings` | 获取世界观资源 |
| GET | `/api/projects/{id}/export?format=` | 导出小说文件 |

## ⚙️ 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `NOVEL_AI_HOST_API_PORT` | API 宿主机端口 | 8050 |
| `NOVEL_AI_HOST_DB_PORT` | PostgreSQL 宿主机端口 | 15432 |
| `NOVEL_AI_HOST_WEB_PORT` | Web 宿主机端口 | 8051 |
| `NOVEL_AI_LOG_LEVEL` | 日志级别 | INFO |
| `WRITER_API_KEY` | Writer 模型 API Key | - |
| `CRITIC_API_KEY` | Critic 模型 API Key | - |
| `MEMORY_API_KEY` | Memory 模型 API Key | - |
| `PROMPT_BUILDER_API_KEY` | Prompt Builder 模型 API Key | - |

### 模型配置示例

```json
[
  {
    "role": "writer",
    "provider": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "model_name": "gpt-4",
    "temperature": 0.8,
    "max_tokens": 4000
  },
  {
    "role": "critic",
    "provider": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "model_name": "gpt-4",
    "temperature": 0.2,
    "max_tokens": 2000
  },
  {
    "role": "memory",
    "provider": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "model_name": "gpt-4",
    "temperature": 0.1,
    "max_tokens": 2000
  }
]
```

## 🧪 测试

### 运行单元测试

```bash
python -m pytest -q
```

### 运行烟雾测试

```bash
NOVEL_AI_BASE_URL=http://127.0.0.1:8000 python -m scripts.smoke_test
```

烟雾测试会验证：
- 模型配置保存与回读
- 摘要资源接口字段
- 角色与世界观资源接口可访问性
- 导出接口可用性

## 🚢 部署

### Docker Compose 部署

```bash
# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f

# 停止服务
docker compose down
```

### 自定义端口

```bash
NOVEL_AI_HOST_API_PORT=18000 \
NOVEL_AI_HOST_DB_PORT=15432 \
NOVEL_AI_HOST_WEB_PORT=18001 \
docker compose up -d --build
```

### 更新配置

- 修改模型地址、模型名或 API Key：`docker compose up -d api worker`
- 修改宿主机端口：`docker compose up -d`
- 注意：不要只用 `docker compose restart`，它不会重新加载新的 `.env`

## 📊 数据模型

### 核心表

| 表名 | 说明 |
|------|------|
| `projects` | 项目主表 |
| `project_model_configs` | 项目模型配置 |
| `chapter_outlines` | 章节大纲 |
| `chapters` | 章节状态与内容 |
| `chapter_attempts` | 生成尝试记录 |
| `chapter_reviews` | 评审结果 |
| `chapter_prompts` | 提示词版本 |
| `generation_jobs` | 任务队列 |
| `project_events` | 事件流 |
| `chapter_summaries` | 章节摘要 |
| `characters` | 角色资源 |
| `character_revisions` | 角色变更提案 |
| `world_settings` | 世界观资源 |
| `world_setting_revisions` | 世界观变更提案 |

### 状态枚举

**项目状态**：draft → ready → running → paused/completed/failed

**章节状态**：pending → queued → writing → reviewing → passed/failed/paused

**提示词状态**：generated → edited → approved → superseded

## 🔧 首次联调

1. **创建项目**
   ```bash
   curl -X POST http://localhost:8000/api/projects \
     -H "Content-Type: application/json" \
     -d '{"title": "测试小说", "genre": "玄幻", "style": "爽文", "total_chapters": 10}'
   ```

2. **配置模型**
   ```bash
   curl -X PUT http://localhost:8000/api/projects/{id}/models \
     -H "Content-Type: application/json" \
     -d '[{"role": "writer", "provider": "mock", ...}]'
   ```

3. **导入大纲**
   ```bash
   curl -X POST http://localhost:8000/api/projects/{id}/outlines/import \
     -H "Content-Type: application/json" \
     -d '{"outlines": ["第一章大纲", "第二大纲"]}'
   ```

4. **启动生成**
   ```bash
   curl -X POST http://localhost:8000/api/projects/{id}/start
   ```

5. **订阅事件**
   ```bash
   curl http://localhost:8000/api/projects/{id}/events/stream
   ```

## 🚀 未来改进方向

基于对同类项目 [webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) 的分析，以下是可以考虑的改进方向：

### 1. 多维审查系统

当前系统只有 Critic 一个综合评审角色，可以借鉴 webnovel-writer 的六维并行审查：

| 审查维度 | 检查重点 | 优先级 |
|----------|----------|--------|
| **爽点检查** | 爽点密度、类型多样性、执行质量 | 高 |
| **一致性检查** | 战力/地点/时间线/角色一致性 | 高 |
| **节奏检查** | 主线/感情线/世界观扩展比例 | 中 |
| **OOC 检查** | 角色行为是否偏离人设 | 中 |
| **连贯性检查** | 场景与叙事连贯性 | 中 |



### 2. 项目记忆系统

增加从历史章节中学习的能力：

```json
{
  "project_memory": {
    "effective_patterns": ["危机钩设计", "迪化误解结构"],
    "failed_attempts": ["节奏过快导致读者流失"],
    "style_preferences": ["对话占比 30%", "心理描写偏多"],
    "reader_feedback": ["第 45 章爽点密度不足"]
  }
}
```

**功能**：
- 自动提取有效写作模式
- 记录失败尝试避免重复
- 学习用户风格偏好
- 支持 `/webnovel-learn` 命令

### 3. Dashboard 增强

增加更多可视化面板：

| 面板 | 功能 |
|------|------|
| **质量趋势** | 评审分数、通过率趋势图 |


### 4. 防幻觉机制

借鉴 webnovel-writer 的"防幻觉三定律"：

| 定律 | 说明 | 执行方式 |
|------|------|---------|
| **大纲即法律** | 遵循大纲，不擅自发挥 | Context Agent 强制加载章节大纲 |
| **设定即物理** | 遵守设定，不自相矛盾 | Consistency Checker 实时校验 |



### 5. 智能断点恢复

增强暂停/继续的智能化：

```python
# 自动识别断点
def detect_resume_point(project_id: str) -> dict:
    return {
        "last_completed_chapter": 45,
        "current_chapter_status": "writing",
        "attempt_progress": "60%",
        "suggested_action": "continue_from_chunk_120"
    }
```

---

## ⚠️ 已知限制

- 当前没有独立的用户系统，前端访问依赖 Nginx Basic Auth
- 阅读页已实现，但主界面暂无显式入口按钮
- 前端暂无自动化测试
- 系统更适合低并发、可恢复、重质量约束的场景
- Worker 中的 Memory 后处理为最小实现
- 默认模型配置存在跨层重复（后端和前端都有）
- 缺少 RAG 检索增强，上下文管理基于规则裁剪
- 评审维度单一，缺少多维并行审查
- 缺少题材特定的写作模板和检查规则
- 缺少追读力维度的量化评估

## 📚 相关文档

- [项目概览](项目概览.md) - 详细的项目功能与架构说明
- [项目说明书](项目说明书.md) - 完整的功能清单与实现细节
- [设计架构](设计架构.md) - 系统设计与技术决策

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[待补充]

---

**Novel AI** - 让 AI 为你的长篇创作保驾护航 🚀
