# Bilibili 视频总结 Agent 设计规格

## 1. 产品目标

本项目第一阶段建设一个在 Windows 本地运行的 Bilibili 视频总结 HTTP API。用户提交 Bilibili 视频 URL 后，系统异步获取元数据和中文字幕，调用 Qwen 生成结构化分析，并通过任务接口返回进度和结果。

系统同时服务两类用户：

- 观众：快速了解视频内容、关键观点、章节和重要时间点。
- 视频博主：分析选题定位、受众、开场钩子、内容结构、节奏、亮点、可借鉴方法和潜在改进点。

系统还会每 6 小时采集 Bilibili 全站及指定分区的热门 Top 10 快照，为后续趋势分析、个性化推荐和微信小程序 AI 视频/直播顾问积累数据。

## 2. 第一阶段范围

### 2.1 包含

- FastAPI HTTP API。
- 异步视频总结任务及进度查询。
- Bilibili URL 校验、BV 号解析和元数据获取。
- 中文字幕下载、解析和清洗。
- Qwen 兼容接口调用。
- Token 估算、安全切分、分段总结和分层合并。
- 观众与博主双模式输出。
- SQLite 数据存储、缓存和断点恢复。
- APScheduler 每 6 小时采集热门榜。
- 全站、知识、科技、游戏、生活 Top 10 快照。
- Qwen 限流、配额、上下文超限和上游异常分类。
- `pyproject.toml` 依赖与项目配置。
- 自动化测试、健康检查和本地运行文档。

### 2.2 不包含

- 无字幕视频的 ASR。
- 视频画面、镜头或剪辑分析。
- 用户账户与登录。
- 个性化推荐算法。
- 自动生成趋势结论。
- 直播实时分析。
- 微信小程序前端。
- 云端部署、Docker、Redis 或 Celery。

## 3. 技术路线

采用模块化单体架构：

- FastAPI 提供 HTTP 接口。
- 本地后台执行器处理总结任务。
- APScheduler 执行周期采集任务。
- SQLite 保存业务数据。
- 现有 `video_processing` 和 `summarization` 能力重构为边界清晰的服务模块。

该路线不依赖额外基础设施，符合本地运行和零基础设施开支要求。未来迁移到云端时，可以将后台执行器替换为 Redis/Celery，而保持 API 和领域服务接口稳定。

程序关闭期间不会执行周期任务。程序重新启动时检查最近一次成功采集时间；若错过周期，则补采一次，不逐次回放所有错过的周期。

## 4. 模块划分

```text
FastAPI
├─ 视频总结 API
│  ├─ URL 校验与 BV 号解析
│  ├─ 任务创建与缓存查询
│  └─ 任务状态与结果查询
├─ 视频处理服务
│  ├─ 元数据获取
│  ├─ 中文字幕获取与清洗
│  ├─ Token 估算与安全切分
│  ├─ 分段总结与分层合并
│  └─ 观众/博主双模式分析
├─ 热门采集服务
│  ├─ 全站 Top 10
│  ├─ 知识/科技/游戏/生活 Top 10
│  └─ 排名与互动指标快照
├─ APScheduler
│  └─ 每 6 小时触发采集，启动时按需补采
└─ SQLite
   ├─ videos
   ├─ transcripts
   ├─ summary_tasks
   ├─ summary_chunks
   ├─ summaries
   ├─ ranking_snapshots
   └─ ranking_items
```

每个模块只承担一类职责。HTTP 层不直接操作 Qwen、yt-dlp 或数据库细节；调度器只触发应用服务；领域服务通过仓储接口持久化数据。

## 5. 单视频处理流程

1. 客户端提交 Bilibili URL。
2. API 校验域名、URL 和 BV 号。
3. 系统根据 BV 号、字幕摘要、模型和提示词版本查询缓存。
4. 命中缓存且未指定强制刷新时，返回已有结果。
5. 未命中缓存时创建异步任务并立即返回任务 ID。
6. 后台任务获取视频元数据和中文字幕。
7. 没有可用中文字幕时，任务结束为 `NO_SUBTITLE`。
8. 系统清洗字幕并保留时间戳。
9. 根据模型上下文限制估算 Token，生成安全分段。
10. 系统逐段调用 Qwen，并在每段成功后持久化结果和 Token 统计。
11. 系统按时间顺序进行分层合并，生成统一的基础内容理解。
12. 系统生成观众与博主两种视角的结果。
13. 最终结果通过 Pydantic 校验后保存，任务结束为 `SUCCEEDED`。

同一任务中已经成功的分段不会因后续失败而丢失。重新执行任务时从未完成分段继续，避免重复调用。

## 6. 热门榜采集流程

1. 程序启动时检查最近一次成功快照时间。
2. 若没有快照或已错过一个采集周期，立即补采一次。
3. APScheduler 每 6 小时触发一次采集。
4. 每次采集全站及知识、科技、游戏、生活五个榜单范围。
5. 每个范围保存 Top 10 的排名、标题、BV 号、UP 主、分区、发布时间、时长及可获得的播放、点赞、投币、收藏、评论、分享等指标。
6. 每次采集作为不可变快照保存。
7. 热门采集不调用 Qwen，不自动总结榜单视频。

分区列表由配置管理，后续可以在不修改业务代码的情况下增删。

## 7. HTTP API

### 7.1 接口

```http
POST /api/v1/summaries
GET  /api/v1/tasks/{task_id}
GET  /api/v1/videos/{bvid}/summary
GET  /api/v1/rankings/latest
GET  /api/v1/rankings/history
POST /api/v1/rankings/collect
GET  /health
```

`POST /api/v1/rankings/collect` 是本地管理接口。第一阶段 API 默认只监听本机，不提供公开访问；未来开放给小程序时必须增加身份验证、限流和 HTTPS。

### 7.2 创建总结任务

请求：

```json
{
  "url": "https://www.bilibili.com/video/BV...",
  "force_refresh": false
}
```

响应：

```json
{
  "task_id": "uuid",
  "status": "PENDING",
  "cached": false
}
```

任务查询响应包含当前阶段、已完成分段数、总分段数、错误类型、用户可读错误信息和 Token 使用统计。

## 8. 总结结果

最终结果包含视频信息、基础分析、观众视角、博主视角和分析元数据：

```json
{
  "video": {
    "bvid": "BV...",
    "title": "...",
    "author": "...",
    "duration_seconds": 600,
    "published_at": "...",
    "source_url": "..."
  },
  "overview": {
    "one_sentence": "...",
    "detailed_summary": "...",
    "keywords": ["..."],
    "key_points": ["..."],
    "chapters": [
      {
        "start_seconds": 0,
        "title": "...",
        "summary": "..."
      }
    ],
    "important_moments": [
      {
        "at_seconds": 95,
        "description": "..."
      }
    ]
  },
  "viewer_view": {
    "who_should_watch": "...",
    "what_you_will_learn": ["..."],
    "viewing_recommendation": "..."
  },
  "creator_view": {
    "topic_positioning": "...",
    "target_audience": "...",
    "opening_hook": "...",
    "content_structure": ["..."],
    "pacing_analysis": "...",
    "strengths": ["..."],
    "reusable_methods": ["..."],
    "possible_improvements": ["..."]
  },
  "analysis_meta": {
    "model": "...",
    "prompt_version": "v1",
    "created_at": "...",
    "source": "subtitle"
  }
}
```

创作者分析只能依据元数据和字幕。系统不得在没有画面分析能力时评价镜头、视觉包装或剪辑质量。

## 9. 任务状态和错误

正常状态流转：

```text
PENDING → FETCHING → SUMMARIZING → MERGING → SUCCEEDED
```

异常终态：

- `INVALID_URL`：URL 或 BV 号无效。
- `NO_SUBTITLE`：没有可用中文字幕。
- `TOKEN_LIMIT`：缩小分段后仍超过上下文限制。
- `RATE_LIMITED`：Qwen 限流且有限重试失败。
- `QUOTA_EXHAUSTED`：Qwen 余额或配额耗尽。
- `UPSTREAM_ERROR`：Bilibili、yt-dlp 或 Qwen 服务异常。
- `FAILED`：无法归类的任务失败。

错误响应包含稳定的机器可读错误码和简短的用户可读信息，不向客户端暴露密钥、堆栈或完整上游响应。

## 10. Token 与 Qwen 调用保护

- 模型配置声明上下文上限、最大输出 Token、安全余量和单任务调用上限。
- 每次调用前估算系统提示词、用户提示词、字幕和预期输出总量。
- 为输出和结构化响应预留 Token，不将输入填满整个上下文窗口。
- 字幕按照时间段和字幕边界切分，不从单个字幕片段中间硬截断。
- 合并输入过长时采用分层合并。
- 上下文超限时自动缩小分段并重试一次。
- 限流使用带抖动的指数退避，并限制重试次数。
- 余额或配额耗尽时不自动重复调用。
- 每次调用记录输入 Token、输出 Token、总 Token、调用阶段和上游请求 ID；上游未返回精确用量时记录估算值并标明来源。
- 可配置单视频最大时长、最大分段数和单任务最大调用次数。

## 11. 数据模型

- `videos`：稳定视频标识、元数据和最近一次观测指标。
- `transcripts`：字幕来源、语言、内容摘要、清洗文本和时间片段。
- `summary_tasks`：状态、进度、错误、重试次数和总 Token。
- `summary_chunks`：分段输入摘要、时间范围、结果、状态和 Token 统计。
- `summaries`：最终双模式结果、模型、提示词版本和字幕摘要。
- `ranking_snapshots`：采集批次、榜单范围、开始时间、完成时间和状态。
- `ranking_items`：快照中的视频、排名和采集时互动指标。

SQLite 启用外键约束。时间统一以 UTC 保存，通过 API 输出 ISO 8601 时间。

## 12. 缓存与幂等

- 总结缓存键由 BV 号、字幕内容摘要、模型名称和提示词版本组成。
- `force_refresh=true` 创建新分析，不覆盖历史结果。
- 同一缓存键已有运行中任务时，重复请求返回该任务，不创建并发重复任务。
- 热门榜项目以快照、范围和 BV 号组成唯一约束。
- 原始字幕与总结分开存储，升级提示词时无需重新下载未变化的字幕。

## 13. 配置、依赖与安全

新增 `pyproject.toml`，统一声明：

- 支持的 Python 版本。
- FastAPI、Uvicorn、yt-dlp、LangChain、langchain-openai、Pydantic、SQLAlchemy、APScheduler 等运行依赖。
- pytest、httpx 等测试依赖。
- 测试、格式化和静态检查配置。
- 项目包信息和本地启动入口。

首版使用兼容且可复现的版本范围。重建有效虚拟环境后生成锁定依赖，避免直接复制已损坏环境的 `pip freeze` 输出。

Qwen API Key 只从环境变量读取。配置文件仅保存非敏感的模型名称、基础 URL、Token 限制、分区列表和调度周期。日志不得输出 API Key、完整鉴权请求头或完整字幕。

现有明文 API Key 已出现在本地文件及开发会话中，应撤销并替换。

## 14. 现有代码迁移

现有能力不是废弃重写，而是逐步迁移：

- 保留元数据、字幕下载、字幕解析、切分和总结算法中已验证的逻辑。
- 统一所有数据路径，以项目根目录或配置的数据目录为基准，不依赖当前工作目录。
- 将包内导入改为稳定的绝对导入。
- 修复源文件、日志和 JSON 产物的 UTF-8 编码问题。
- 放宽 SRT 解析器，使其支持多行字幕和常见格式差异。
- 将字符数切分升级为 Token 预算切分。
- 将直接打印和脚本式错误处理替换为结构化日志与领域异常。

当前 `summarization/summarizer.py` 中用户尚未提交的分段大小修改必须保留，实施时不得覆盖。

## 15. 测试策略

### 15.1 单元测试

- Bilibili URL 和 BV 号解析。
- 中文字幕优先级选择。
- 多行 SRT、换行符、样式标签和无效块解析。
- Token 预算计算和分段边界。
- 分层合并规划。
- 缓存键和幂等逻辑。
- Qwen 错误分类。
- 热门榜响应到领域模型的映射。

### 15.2 集成测试

- 使用模拟的 Bilibili、yt-dlp 和 Qwen 响应完成整条总结任务。
- 无字幕视频返回 `NO_SUBTITLE`。
- Qwen 限流、配额耗尽、超时和上下文超限状态正确。
- 失败后重试复用已完成分段。
- SQLite 唯一约束阻止重复任务和重复榜单项目。
- 调度器启动补采和 6 小时周期行为正确。
- FastAPI 请求与响应通过 Pydantic 校验。

默认测试不访问真实 Bilibili 或 Qwen 网络服务。真实网络验证作为手动冒烟测试，必须显式启用。

## 16. 验收标准

1. 从项目根目录可以按文档创建环境、安装依赖并启动 API。
2. 提交有效 Bilibili URL 后立即获得任务 ID。
3. 有中文字幕的视频能返回观众与博主两种视角。
4. 无中文字幕的视频稳定返回 `NO_SUBTITLE`。
5. 重复提交同一视频复用缓存，不再次调用 Qwen。
6. 长视频安全切分，单次请求不超过配置的 Token 预算。
7. Qwen 限流、配额耗尽、超时和上下文超限被正确区分。
8. 中途失败后不会重复处理已经完成的分段。
9. 每 6 小时采集全站及四个分区 Top 10。
10. 程序重启后能按规则补采一次。
11. 热门采集过程不调用 Qwen。
12. 核心测试不依赖真实外部网络并全部通过。

## 17. 实施顺序

1. 建立 `pyproject.toml`、有效虚拟环境说明和基础测试框架。
2. 修复编码、项目路径、包导入和字幕解析。
3. 建立配置、数据库和领域模型。
4. 将现有单视频流水线迁移为可测试的应用服务。
5. 实现 Token 保护、错误分类、缓存和断点恢复。
6. 实现 FastAPI 总结任务接口。
7. 实现热门榜采集、定时调度和查询接口。
8. 完成端到端模拟测试、真实手动冒烟测试和 README。
