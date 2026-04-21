# NarraWorld

NarraWorld 是一个“上传故事 -> 生成世界 -> 进入剧情 -> 续写新篇章”的互动叙事产品原型。

## 核心能力

- 故事导入：支持上传小说、剧本、设定文档
- 结构化抽取：角色、关系、事件链、场景、线索、秘密、世界规则
- 叙事图谱：统一的 Zep 风格故事图谱，作为角色初始化、剧情推进、续写的底层索引
- 世界状态引擎：统一管理剧情阶段、场景状态、角色状态、公开信息与私有信息
- 聊天流剧情游玩：角色消息、系统叙事、场景变化、关键节点选项
- 续写引擎：继承当前 world state 与关系张力生成下一篇章

## 页面结构

- `/`：NarraWorld 首页
- `/create`：上传故事并生成世界
- `/world/:id`：世界总览
- `/world/:id/play`：主剧情游玩页
- `/world/:id/graph`：图谱页
- `/world/:id/characters`：角色页
- `/world/:id/debug`：开发调试页
- `/world/:id/continuation`：续写页

原有多智能体模拟流程仍保留为高级模式，入口位于 `/process/new`。

## 快速开始

1. 创建根目录 `.env`

```env
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
ZEP_API_KEY=your_zep_api_key
```

2. 安装依赖

```bash
npm run setup:all
```

3. 启动项目

```bash
npm run dev
```

默认地址：

- 前端：`http://localhost:3000`
- 后端：`http://127.0.0.1:5002`

## 当前重点

当前版本重点围绕故事世界体验而不是社交媒体推演：

- 稳定角色切分
- 图谱进入 runtime
- 聊天流驱动剧情
- 关键节点选项交互
- 结局后续写
