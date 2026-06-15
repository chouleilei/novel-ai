# 🔍 Bug深度排查报告 (修订版)

> **复核状态**: 已经由 Oracle(架构专家)、Metis(分析专家)、Momus(质量专家) 三方独立复核
> **修订日期**: 2025-04-02
> **修订依据**: 专家复核意见 + 代码级验证

---

## 📊 问题总览 (按根因归类)

本报告将 **66个问题** 归类为 **4个系统级根因**，而非平铺式罗列：

| 根因类型 | 问题描述 | 影响面 | 子问题数 |
|----------|----------|--------|----------|
| 🔴 **R1: 认证授权模型缺失** | 后端无身份上下文，所有资源接口默认可越权访问 | 全局 | 15 |
| 🟠 **R3: 状态机一致性脆弱** | 缺少数据库级约束、幂等保护、事务边界不清 | 数据 | 25 |
| 🟠 **R4: 资源生命周期管理不当** | SSE会话泄漏、连接未正确关闭 | 运维 | 12 |
| 🟡 **R5: 代码质量与可维护性** | 类型安全、错误处理、日志规范 | 工程 | 14 |

---

## 🔴 R1: 认证授权模型缺失 (P0 - 最高优先级)

### 根因分析

**系统边界是假设出来的**：README声明依赖Nginx Basic Auth，但后端代码没有任何身份概念。一旦反代配置缺失、旁路访问API、或内部服务互访，**所有资源默认裸奔**。

这不是"某几个端点漏了校验"，而是**后端整体没有授权模型**。

### 受影响端点 (全部按 `project_id` 直接访问)

| 文件 | 端点模式 | 风险 |
|------|----------|------|
| `backend/api/projects.py:119-125` | `GET /projects/{project_id}` | 任意项目详情 |
| `backend/api/projects.py:142-148` | `DELETE /projects/{project_id}` | 删除任意项目 |
| `backend/api/generation.py:16-30` | `POST /projects/{project_id}/start` | 启动任意项目 |
| `backend/api/generation.py:33-49` | `POST /projects/{project_id}/pause` | 暂停任意项目 |
| `backend/api/prompts.py:180-199` | `PUT /projects/{project_id}/prompts/{version}` | 修改任意prompt |
| `backend/api/prompts.py:202-220` | `POST /projects/{project_id}/prompts/{version}/approve` | 批准任意prompt |
| `backend/api/resources.py:195-227` | `GET /projects/{project_id}/export` | **导出任意项目完整内容** |
| `backend/api/events.py:15-67` | `GET /projects/{project_id}/events/stream` | **订阅任意项目SSE流** |

### 遗漏的数据泄露面 (Oracle补充)

1. **导出接口** - `resources.py` 的 `/export` 可导出任意项目的完整小说内容
2. **SSE事件订阅** - 可订阅任意 `project_id` 的实时生成内容、评审结果
3. **attempt/review访问** - 可访问任意项目的章节内容、评审详情

---

## 🟠 R3: 状态机一致性脆弱 (P1)

### 3.1 租约模型缺少足够保护 (修正原"并发竞态"描述)

**原表述修正**:
- ❌ ~~"claim后立即commit释放锁，存在并发竞态"~~
- ✅ **正确表述**: "租约模型在长任务/续租失败场景下缺少幂等保护和失租中止机制"

**真正风险**:
1. `backend/worker/job_queue.py:76-95` - `renew_lease` 先更新Job lease再更新Project lease，无原子性
2. `backend/worker/runner.py:83-95` - 任务处理时间超过lease时长时，可能被另一worker重新claim
3. `backend/worker/pipeline.py:859-865` - `_ensure_project_running` 未验证当前worker仍持有lease

### 3.2 关键约束缺失

| 位置 | 缺失约束 | 影响 |
|------|----------|------|
| `backend/db/models/job.py` | `(project_id, chapter_number)` 联合唯一约束 | 可能创建重复的GenerationJob |
| `backend/db/models/project.py` | SystemSetting/SystemRuntimeSetting 唯一约束 | 并发可能创建多条记录 |

### 3.3 事务边界问题

| 文件 | 行号 | 问题 |
|------|------|------|
| `backend/services/memory_service.py` | 394-417, 431-456 | 循环内单条flush，无批量事务保护 |
| `backend/services/project_service.py` | 161-200 | 批量删除+循环插入无显式事务 |
| `backend/worker/pipeline.py` | 全文15处commit | 频繁commit，无法原子回滚 |

### 3.4 任务异常被静默吞掉

| 文件 | 行号 | 问题 |
|------|------|------|
| `backend/worker/runner.py` | 97-105 | `await done.pop()` 未检查任务异常 |

---

## 🟠 R4: 资源生命周期管理不当 (P1)

### 4.1 SSE数据库会话泄漏 (实锤)

| 文件 | 行号 | 问题代码 |
|------|------|----------|
| `backend/api/events.py` | 44-59 | 长循环内反复 `async for session in get_db_session()` |

**问题**: `get_db_session()` 是给FastAPI依赖注入用的generator，在长连接中手动消费会搞乱session生命周期。

### 4.2 SSE重连与恢复控制不足 (修正原"无自动重连")

**原表述修正**:
- ❌ ~~"SSE无自动重连机制"~~
- ✅ **正确表述**: "仅依赖浏览器EventSource默认重连，缺少应用层恢复控制"

| 文件 | 行号 | 问题 |
|------|------|------|
| `frontend/src/composables/useProjectEvents.ts` | 64-66 | `onerror` 只打印日志，无自定义退避/恢复 |
| `frontend/src/composables/useProjectEvents.ts` | 58-62 | `addEventListener` 后没有对应的 `removeEventListener` |

**缺失的能力**:
- 指数退避重连
- 重放控制 (从最新cursor恢复)
- 连接状态UI可视化
- 错误恢复策略监控

### 4.3 其他资源泄漏

| 文件 | 行号 | 问题 |
|------|------|------|
| `backend/worker/pipeline.py` | 660-667 | `_close_stream_iterator` 超时时静默吞掉异常 |
| `frontend/src/views/NovelReader.vue` | 47 | `createObjectURL` 后没有 `revokeObjectURL` |

---

## 🟡 R5: 代码质量与可维护性 (P2)

### 5.1 输入验证缺失

| 文件 | 字段 | 问题 |
|------|------|------|
| `backend/api/projects.py:23` | `ProjectCreateRequest.title` | 无 `max_length` |
| `backend/api/projects.py:26` | `ProjectCreateRequest.global_prompt` | 无长度限制 |
| `backend/api/prompts.py:25` | `PromptUpdateRequest.edited_prompt` | 无长度限制 |
| `backend/api/projects.py:39,44` | `OutlineItem.outline_text` | 无长度限制 |

### 5.2 类型安全

| 文件 | 问题 |
|------|------|
| `frontend/src/stores/project.ts` | 所有catch块使用 `err: any` |
| `frontend/src/types/index.ts` | 多处 `[key: string]: any` |

### 5.3 前端路由守卫缺失

**注意**: 在后端完全没有授权的情况下，前端守卫没有安全价值，只是UX问题。但仍需改进。

| 文件 | 问题 |
|------|------|
| `frontend/src/router/index.ts` | 无任何路由守卫 |

### 5.4 API配置

| 文件 | 问题 |
|------|------|
| `frontend/src/api/index.ts` | axios无timeout配置 |
| `backend/main.py` | 无CORS中间件配置 |

### 5.5 日志与调试

| 文件 | 问题 |
|------|------|
| `backend/logging_config.py` | 日志格式不包含trace_id |
| `backend/config.py:10` | `debug: bool = True` 默认开启 |

---

## 📋 修复优先级路线图

### P0: 必须先止血 (1-2天)

```
┌─────────────────────────────────────────────────────────────┐
│  后端授权加固                                                │
├─────────────────────────────────────────────────────────────┤
│  1. 后端引入最小身份依赖                                      │
│     - 添加 owner_user_id 字段到 Project                      │
│     - 所有项目查询改成"按 owner + project_id"                 │
│                                                              │
│  2. 修正 events.py 的 session 生命周期                        │
│     - 改用 async with AsyncSessionLocal()                    │
└─────────────────────────────────────────────────────────────┘
```

### P1: 影响一致性和可恢复性 (3-5天)

```
┌─────────────────────────────────────────────────────────────┐
│  状态机加固                                                  │
├─────────────────────────────────────────────────────────────┤
│  1. 给关键路径补唯一约束                                      │
│     - GenerationJob(project_id, chapter_number)              │
│     - SystemSetting id=1 唯一                                │
│                                                              │
│  2. 租约模型增强                                              │
│     - 条件更新确保幂等                                        │
│     - 失租后安全中止                                          │
│     - 续租失败时清理                                          │
│                                                              │
│  3. 事务边界收口                                              │
│     - memory_service 批量flush改为统一事务                    │
│     - pipeline 减少commit次数                                │
│                                                              │
│  4. Task异常正确传播                                          │
│     - runner.py 检查 done.pop() 异常                          │
└─────────────────────────────────────────────────────────────┘
```

### P2: 质量和可维护性 (持续)

```
┌─────────────────────────────────────────────────────────────┐
│  工程质量提升                                                │
├─────────────────────────────────────────────────────────────┤
│  1. 日志增强 - 添加 trace_id                                  │
│  2. SSE恢复策略 - 指数退避、重放控制                           │
│  3. 类型收敛 - 减少 any 使用                                  │
│  4. 输入验证 - 文本字段添加 max_length                         │
│  5. 前端路由守卫 - UX改进                                     │
│  6. API超时配置 - axios timeout                              │
│  7. 错误边界组件 - 降级UI                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 详细问题清单

<details>
<summary>点击展开完整66项问题清单</summary>

### R1: 认证授权模型缺失 (15项)

| # | 文件 | 行号 | 问题 | 严重度 |
|---|------|------|------|--------|
| 1 | `backend/api/projects.py` | 119-125 | GET /projects/{id} 无归属校验 | P0 |
| 2 | `backend/api/projects.py` | 142-148 | DELETE /projects/{id} 无归属校验 | P0 |
| 3 | `backend/api/generation.py` | 16-30 | POST /start 无归属校验 | P0 |
| 4 | `backend/api/generation.py` | 33-49 | POST /pause 无归属校验 | P0 |
| 5 | `backend/api/generation.py` | 52-68 | POST /resume 无归属校验 | P0 |
| 6 | `backend/api/generation.py` | 71-87 | POST /retry 无归属校验 | P0 |
| 7 | `backend/api/prompts.py` | 180-199 | PUT /prompts 无归属校验 | P0 |
| 8 | `backend/api/prompts.py` | 202-220 | POST /approve 无归属校验 | P0 |
| 9 | `backend/api/resources.py` | 90-116 | GET /characters 无归属校验 | P0 |
| 10 | `backend/api/resources.py` | 195-227 | GET /export 无归属校验 (数据外泄) | P0 |
| 11 | `backend/api/events.py` | 15-35 | GET /events 无归属校验 | P0 |
| 12 | `backend/api/events.py` | 39-67 | GET /events/stream 无归属校验 (SSE订阅任意项目) | P0 |
| 13 | `backend/api/resources.py` | 119-145 | GET /world_settings 无归属校验 | P0 |
| 14 | `backend/api/resources.py` | 148-192 | revision apply/reject 无归属校验 | P0 |
| 15 | `backend/api/generation.py` | 90-111 | GET /chapters 无归属校验 | P0 |

### R3: 状态机一致性脆弱 (25项)

| # | 文件 | 行号 | 问题 | 严重度 |
|---|------|------|------|--------|
| 16 | `backend/worker/job_queue.py` | 76-95 | renew_lease 多UPDATE无原子性 | P1 |
| 17 | `backend/worker/runner.py` | 97-105 | Task异常未检查 | P1 |
| 18 | `backend/worker/pipeline.py` | 859-865 | 未验证lease仍持有 | P1 |
| 19 | `backend/db/models/job.py` | - | 缺(project_id, chapter_number)唯一约束 | P1 |
| 20 | `backend/db/models/project.py` | - | SystemSetting无唯一约束 | P1 |
| 21 | `backend/services/memory_service.py` | 394-417 | 循环内单条flush | P1 |
| 22 | `backend/services/memory_service.py` | 431-456 | 循环内单条flush | P1 |
| 23 | `backend/services/project_service.py` | 161-200 | 批量操作无显式事务 | P1 |
| 24 | `backend/services/generation_state.py` | 107-122 | max_retries为None时异常 | P1 |
| 25 | `backend/services/generation_service.py` | 64-66 | pause未排除终态 | P1 |
| 26 | `backend/services/generation_service.py` | 115-117 | resume未排除终态 | P1 |
| 27 | `backend/worker/pipeline.py` | 421-423 | scalar_one()空值风险 | P1 |
| 28-40 | `backend/worker/pipeline.py` | 多处 | 15次commit无法原子回滚 | P1 |

### R4: 资源生命周期管理不当 (12项)

| # | 文件 | 行号 | 问题 | 严重度 |
|---|------|------|------|--------|
| 41 | `backend/api/events.py` | 44-59 | SSE会话泄漏 | P0 |
| 42 | `frontend/src/composables/useProjectEvents.ts` | 64-66 | SSE应用层恢复控制不足 | P1 |
| 43 | `frontend/src/composables/useProjectEvents.ts` | 58-62 | eventListener未移除 | P1 |
| 44 | `backend/worker/pipeline.py` | 660-667 | 流迭代器超时异常吞掉 | P1 |
| 45 | `backend/worker/runner.py` | 34-47 | stop_event异常未设置 | P1 |
| 46 | `frontend/src/views/NovelReader.vue` | 47 | Blob URL未释放 | P2 |
| 47 | `frontend/src/composables/useLiveWritingState.ts` | 347-350 | 章节选择竞态 | P1 |
| 48-52 | `backend/worker/pipeline.py` | 769-775等 | 流式响应资源清理 | P1 |

### R5: 代码质量与可维护性 (14项)

| # | 文件 | 行号 | 问题 | 严重度 |
|---|------|------|------|--------|
| 53 | `backend/api/projects.py` | 23 | title无max_length | P2 |
| 54 | `backend/api/projects.py` | 26 | global_prompt无长度限制 | P2 |
| 55 | `backend/api/prompts.py` | 25 | edited_prompt无长度限制 | P2 |
| 56 | `frontend/src/stores/project.ts` | 多处 | 大量any类型 | P2 |
| 57 | `frontend/src/types/index.ts` | 多处 | any类型定义 | P2 |
| 58 | `frontend/src/router/index.ts` | - | 无路由守卫 | P2 |
| 59 | `frontend/src/api/index.ts` | 3-8 | 无timeout配置 | P2 |
| 60 | `backend/main.py` | - | 无CORS中间件 | P2 |
| 61 | `backend/logging_config.py` | - | 无trace_id | P2 |
| 62 | `backend/config.py` | 10 | debug默认True | P2 |
| 63-66 | 多个组件 | - | 无错误边界 | P2 |

</details>

---

## 🏁 验收标准

### P0修复验收

```bash
# 1. 验证归属校验
curl -u user:pass http://localhost:8050/api/projects/other-user-project-id
# 应返回 404 "Project not found or access denied"

# 2. 验证SSE会话
# 监控数据库连接数，SSE连接期间应稳定
watch -n 1 "ss -s | grep estab"
```

### P1修复验收

```bash
# 1. 验证唯一约束
# 尝试创建重复job应失败

# 2. 验证租约保护
# 模拟长任务，验证lease过期后安全中止

# 3. 验证事务边界
# 检查memory_service批量更新原子性
```

---

## 📝 专家复核意见摘要

| 专家 | 结论 |
|------|------|
| **Oracle** | 报告方向正确，核心发现成立，但2处定性需修正，3处遗漏需补充 |
| **Metis** | 报告可执行，但建议按根因归类而非平铺；注意AI生成报告的典型失败模式 |
| **Momus** | **OKAY** - 报告可执行，引用文件存在，行号准确 |

---

*报告修订完成。如需针对具体问题深入分析或开始修复，请告知。*