# Bilibili 视频总结与排行榜

这是一个本地运行的 Python 应用，提供三个互相独立的工作入口：

- **排行榜**：读取已经采集到 SQLite 的五个分区快照，展示 Top 100、榜单变化和数据新鲜度。
- **视频总结**：接收 Bilibili 视频链接，在后台获取元数据、字幕和公开弹幕，保存结构化总结并展示弹幕词云。
- **UP 分析**：选择曾进入项目排行榜且已确认数字 UID 的 UP，采集历史投稿并分析投稿节奏、视频表现、爆款和上榜情况。

Web 页面负责读取排行榜数据、管理总结任务和按用户操作采集 UP 历史投稿。排行榜采集仍是独立的命令行流程；启动 Web 应用不会自动执行真实排行榜采集。

## 架构

```text
浏览器（仅本机）
  └─ web_app：Shiny 导航、排行榜查询、总结任务与静态样式
       ├─ ranking_collector：采集客户端、调度器、比较逻辑、SQLite 仓储
       ├─ uploader_analysis：上榜 UP 档案、历史投稿采集、快照和分析
       ├─ video_processing：元数据、字幕、文字稿处理
       └─ summarization：文本切分、模型调用、结构化结果

data/ranking.db：排行榜快照、总结任务、UP 档案、历史投稿和指标快照
data/：元数据、字幕、弹幕 XML、词云缓存、文字稿和总结等运行时产物
```

## 环境要求

- Python 3.12 或 3.13（项目元数据要求 `>=3.12,<3.14`）
- Windows PowerShell 或其他可运行 Python 命令的终端
- 仅在执行真实视频总结时需要可用的模型凭据与网络
- 仅在执行真实排行榜采集时需要访问 Bilibili；Cookie 是可选的
- 在 Web 页面采集 UP 历史投稿时需要访问 Bilibili；匿名请求失败后使用 Cookie 后备

## 安装

建议先创建并激活虚拟环境，然后在项目根目录安装应用和开发工具：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

`README.md` 使用 UTF-8 编码，可由 setuptools 作为项目说明读取。

## 配置

复制 `.env.example` 为 `.env`，并只在本机填写真实值：

```dotenv
SUMMARY_VIDEO_API_KEY=your-model-api-key
BILIBILI_COOKIE_FILE=C:\path\to\bilibili-cookies.txt
```

- `SUMMARY_VIDEO_API_KEY` 供视频总结模型使用；模型名和服务地址保存在 `config.json` 的 `model` 与 `base_url` 字段。
- `BILIBILI_COOKIE_FILE` 可指向 Netscape 格式的 Cookie 文件。排行榜和 UP 历史投稿客户端始终先发起匿名请求，只有匿名请求失败时才尝试读取 Cookie 并重试。
- `.env`、`.secrets/` 和 `config.json` 按当前仓库约定不提交。Web 页面不会显示、读取回显或允许编辑 API Key、Cookie 或其他秘密。

不要把真实凭据写入 README、测试、日志或浏览器表单。

## 启动 Web 应用

```powershell
python -m web_app.app
```

访问 [http://localhost:8000](http://localhost:8000)。应用固定监听 `127.0.0.1:8000`，不会监听局域网或公网地址。

总结任务由进程内后台工作线程执行，状态持久化在 `data/ranking.db`。应用重启时，未完成的 `PENDING` / `PROCESSING` 任务会恢复为待处理并重新调度；已完成和已失败任务保留在历史记录中。不要同时启动多个 Web 应用实例操作同一个数据库。

弹幕通过 yt-dlp 的 `danmaku` 字幕轨道下载。原始 XML 和不含用户身份的词频结果最多缓存 7 天；弹幕不可用或处理失败不会影响字幕总结任务完成。

### 使用 UP 分析

UP 分析只列出排行榜数据中已经保存数字 UID 的 UP。旧榜单记录如果没有 UID，不会按名称推测或错误合并；执行一次新的排行榜采集后，新快照会保存平台返回的 UP UID。

进入左侧“UP 分析”后选择 UP，点击“采集或更新历史投稿”。采集任务在后台分页执行，结果保存到 `data/ranking.db`：

- `uploader_profiles`：UP 数字 UID、当前名称及上榜时间范围。
- `uploader_collection_tasks`：采集状态、分页游标和安全错误码。
- `uploader_videos`：历史投稿及最近一次获取的指标。
- `uploader_video_snapshots`：不同采集时间的视频指标快照。

历史投稿接口可能触发 Bilibili 风控。客户端匿名请求失败后才读取 Cookie；Cookie 请求遇到 HTTP 412 时会采用较长的随机退避。Web 服务进程必须具备访问 Bilibili 的网络权限，否则页面会显示网络请求失败。停止或重启 Web 服务不会删除已经保存的 UP 数据。

## 独立运行排行榜采集

Web 启动命令不会采集数据。需要采集时，在另一个终端显式选择一次性模式或定时模式：

```powershell
python -m ranking_collector.ranking_collector_pipeline --once
python -m ranking_collector.ranking_collector_pipeline --schedule
```

这两个命令会访问 Bilibili；测试和文档验收不应把它们当作离线验证命令执行。查看参数不会触发采集：

```powershell
python -m ranking_collector.ranking_collector_pipeline --help
```

### Agent 按需执行一次采集

Codex 或本机计划任务需要执行一次采集时，优先使用带并发、超时和数据库验证的包装入口：

```powershell
python -m automation.ranking_once
python -m automation.ranking_once --json
```

包装入口会在可终止的子进程中调用现有排行榜 Service，并在进程结束后读取 SQLite，确认任务、分区结果、快照和条目确实写入。默认超时为 300 秒；可通过 `--timeout` 调整。它不会启动长期调度器，也不会控制 Web 服务。

结构化状态包括 `SUCCEEDED`、`PARTIAL_FAILED`、`FAILED`、`SKIPPED_ALREADY_RUNNING` 和 `TIMED_OUT`。退出码依次为 `0`、`2`、`1`、`3` 和 `4`。JSON 和文本报告不会输出 Cookie、API Key、堆栈或敏感本地路径。

不要同时运行该包装入口、原有 `--once` 和 `--schedule`。周期执行应只选择 Codex 自动化或 Windows 任务计划程序中的一种。

## 验证

```powershell
python -m pytest -q
python -m ruff check .
```

交互、响应式布局、视觉一致性和重启恢复请按[手动验收清单](web_ui/手动验收清单.md)逐项检查。

## 当前完成状态

- [x] 第一批：视频总结弹幕词云。
- [x] 第二批：排行榜长期趋势。
- [x] 第三批：上榜 UP 历史视频分析。
- [ ] 第四批：Codex 按需触发排行榜单次采集。
- [ ] 第五批：UP 分析数据可视化，图表计划放在历史投稿板块上方。

更详细的目标、边界和验收条件见[下阶段计划](docs/下阶段计划.md)。
