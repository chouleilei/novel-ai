# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the `scripts/` module.

> 导航：根文档 `/root/nv/CLAUDE.md` → `scripts/`

## 模块职责

`scripts/` 主要放本地初始化、联调与验证脚本，不是正式服务入口。

## 常用脚本

```bash
python -m scripts.init_db
NOVEL_AI_BASE_URL=http://127.0.0.1:8000 python -m scripts.smoke_test
NOVEL_AI_BASE_URL=http://127.0.0.1:8050 python -m scripts.validate_five_chapter_flow
NOVEL_AI_BASE_URL=http://127.0.0.1:8050 python -m scripts.validate_retry_flow
```

## 文件说明

- `init_db.py`：本地快速建表，适合临时初始化；正式场景仍优先 `alembic upgrade head`。
- `smoke_test.py`：最小端到端冒烟，走项目创建 → 模型配置 → 大纲导入 → 启动 → 结果校验。
- `validate_five_chapter_flow.py`：五章完整流程验证，额外检查事件、Prompt、attempt、review、summary、export。
- `validate_retry_flow.py`：重试相关流程验证。
- `seed_demo_project.py`：写入示例项目数据。

## 使用约定

- 这些脚本直接调用 HTTP API，默认通过 `NOVEL_AI_BASE_URL` 指定目标服务。
- 大多数验证脚本依赖 mock provider 或可用的后端服务，不适合作为单元测试替代。
- 如果你修改了项目状态流转、事件类型、导出格式或模型配置接口，优先检查这些脚本是否也需要同步。
