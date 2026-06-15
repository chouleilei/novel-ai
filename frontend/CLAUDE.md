# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the `frontend/` module.

> 导航：根文档 `/root/nv/CLAUDE.md` → `frontend/`

## 模块职责

`frontend/` 是 Vue 3 + Vite + Pinia 前端，负责：

- 项目列表与项目配置
- 模型配置与连通性测试 UI
- 大纲导入与 Prompt 编辑
- 实时写作监控页
- 摘要 / 角色 / 世界观资源展示
- 阅读与导出页面

## 本模块常用命令

```bash
cd frontend && npm install
cd frontend && NOVEL_AI_VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
cd frontend && npm run build
cd frontend && npm run preview
```

说明：当前 `package.json` 未定义独立 lint / test 脚本；构建会先执行 `vue-tsc -b`。

## 前端主干结构

- `src/main.ts`：应用入口。
- `src/router/index.ts`：主路由定义。
- `src/api/index.ts`：axios 实例，`baseURL` 固定为 `/api`。
- `src/stores/project.ts`：项目、模型、大纲、生成控制相关 API 封装。
- `src/types/index.ts`：前端与后端 payload 的主要类型契约。
- `src/views/ProjectList.vue`：项目列表页。
- `src/views/ProjectSetup.vue`：项目配置、模型配置、大纲导入。
- `src/views/PromptEditor.vue`：章节 Prompt 编辑。
- `src/views/LiveWriting.vue`：实时写作与事件流消费。
- `src/views/NovelReader.vue`：阅读与导出页面。

## 路由心智模型

主要路由：

- `/`：项目列表
- `/projects/new`：新建项目
- `/projects/:id`：项目配置
- `/projects/:id/prompt/:chapterNumber`：Prompt 编辑
- `/projects/:id/live`：实时写作页
- `/projects/:id/read`：阅读页

如果页面跳转有问题，先看 `router/index.ts`，再看页面内部是否依赖 query/tab 状态。

## 状态管理与 API 交互

`src/stores/project.ts` 是大多数页面共用的数据入口：

- `fetchProjects` / `fetchProject`
- `createProject` / `updateProject` / `deleteProject`
- `fetchModels` / `fetchDefaultModels` / `updateModels`
- `importOutlines`
- `startGeneration` / `pauseGeneration` / `resumeGeneration` / `retryChapter`
- `testModelConnectivity`

因为 `src/api/index.ts` 的响应拦截器直接返回 `response.data`，所以 store 中拿到的是业务 payload，不是完整 axios response。

## 类型契约关注点

`src/types/index.ts` 集中声明：

- 项目、模型配置、连通性测试结果
- 章节 / 大纲 / Prompt / attempt / review
- 摘要、角色、世界观与 revision 资源

前后端联调出问题时，除了查接口实现，也要查这里是否已反映最新字段。

## 实时页关注点

`src/views/LiveWriting.vue` 依赖 SSE 事件流驱动界面：

- 连接 `/api/projects/{id}/events/stream`
- 根据事件类型更新当前章节、实时正文、评分与资源刷新 token
- 已知核心事件包括：`pipeline_start`、`chapter_writing`、`precheck_done`、`prompt_generated`、`content_chunk`、`chapter_reviewing`、`chapter_scored`、`memory_updated`、`chapter_passed`、`chapter_paused`、`project_paused`、`pipeline_complete`

修改实时写作体验时，通常要同时核对后端 `events.py` / pipeline 发出的事件载荷。

## ProjectSetup 页面关注点

`src/views/ProjectSetup.vue` 同时承担多件事：

- 新建 / 编辑项目基础信息
- 管理 4 类模型角色（`writer` / `critic` / `memory` / `prompt_builder`）
- 在未启用 prompt_builder 时，让它跟随 writer 默认值
- 导入大纲
- 调用模型连通性测试

如果表单表现异常，优先检查：

- 默认模型构造逻辑
- `normalizeModelExtraConfig`
- `promptBuilderEnabled` 与 writer 同步逻辑
- route query 中 `tab` 的同步行为

## 主要页面职责

- `ProjectList.vue`：项目卡片列表、进度展示、删除确认。
- `ProjectSetup.vue`：项目基础信息、模型配置、大纲导入、模型连通性测试。
- `PromptEditor.vue`：版本列表、Prompt 编辑、保存、批准、生成新版本。
- `LiveWriting.vue`：章节状态、实时正文、评分面板、资源面板、事件时间线。
- `NovelReader.vue`：已通过章节的阅读排版与 TXT / Markdown / EPUB 下载。

## 组件协作

实时页和阅读页会组合多个业务组件，例如：

- `ScoreBoard.vue`：展示五维评分、阻断问题、违反要求、改进建议。
- `ProjectResourcesPanel.vue`：摘要 / 角色 / 世界观 / 审核提案四合一面板，也负责 apply / reject revision。
- `PromptVersionPanel.vue`：Prompt 版本列表切换。
- `ChapterProgress.vue`：章节状态圆点与重试数提示。
- `CharacterPanel.vue`：角色面板的简版展示。

这类组件通常依赖上层页面已经整理好的 payload，而不是自己再拼复杂业务状态。

## 联调提示

- 如果导出下载异常，优先看 `NovelReader.vue` 的 `responseType: 'blob'` 与后端导出接口响应头是否一致。
- 如果 Prompt 编辑后没生效，优先检查 `PromptEditor.vue` 的保存 / 批准链路，以及后端 approved prompt 复用逻辑。
- 如果资源面板没有刷新，优先检查 `LiveWriting.vue` 是否正确推进 `refreshToken`，以及 `ProjectResourcesPanel.vue` 的 watcher 是否触发。
