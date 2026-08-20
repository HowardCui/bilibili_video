# Bilibili 视频总结与排行榜

这是一个本地运行的 Python 应用，提供两个互相独立的工作入口：

- **排行榜**：读取已经采集到 SQLite 的五个分区快照，展示 Top 100、榜单变化和数据新鲜度。
- **视频总结**：接收 Bilibili 视频链接，在后台获取元数据、字幕和公开弹幕，保存结构化总结并展示弹幕词云。

Web 页面只负责读取排行榜数据和管理总结任务。排行榜采集仍是独立的命令行流程；启动 Web 应用不会自动执行真实采集。

## 架构

```text
浏览器（仅本机）
  └─ web_app：Shiny 导航、排行榜查询、总结任务与静态样式
       ├─ ranking_collector：采集客户端、调度器、比较逻辑、SQLite 仓储
       ├─ video_processing：元数据、字幕、文字稿处理
       └─ summarization：文本切分、模型调用、结构化结果

data/ranking.db：排行榜快照和可恢复的总结任务
data/：元数据、字幕、弹幕 XML、词云缓存、文字稿和总结等运行时产物
```

## 环境要求

- Python 3.12 或 3.13（项目元数据要求 `>=3.12,<3.14`）
- Windows PowerShell 或其他可运行 Python 命令的终端
- 仅在执行真实视频总结时需要可用的模型凭据与网络
- 仅在执行真实排行榜采集时需要访问 Bilibili；Cookie 是可选的

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
- `BILIBILI_COOKIE_FILE` 可指向 Netscape 格式的 Cookie 文件。排行榜客户端始终先发起匿名请求，只有匿名请求失败时才尝试读取 Cookie 并重试。
- `.env`、`.secrets/` 和 `config.json` 按当前仓库约定不提交。Web 页面不会显示、读取回显或允许编辑 API Key、Cookie 或其他秘密。

不要把真实凭据写入 README、测试、日志或浏览器表单。

## 启动 Web 应用

```powershell
python -m web_app.app
```

访问 [http://localhost:8000](http://localhost:8000)。应用固定监听 `127.0.0.1:8000`，不会监听局域网或公网地址。

总结任务由进程内后台工作线程执行，状态持久化在 `data/ranking.db`。应用重启时，未完成的 `PENDING` / `PROCESSING` 任务会恢复为待处理并重新调度；已完成和已失败任务保留在历史记录中。不要同时启动多个 Web 应用实例操作同一个数据库。

弹幕通过 yt-dlp 的 `danmaku` 字幕轨道下载。原始 XML 和不含用户身份的词频结果最多缓存 7 天；弹幕不可用或处理失败不会影响字幕总结任务完成。

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

## 验证

```powershell
python -m pytest -q
python -m ruff check .
```

交互、响应式布局、视觉一致性和重启恢复请按[手动验收清单](web_ui/手动验收清单.md)逐项检查。
