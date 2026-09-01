# 使用 Codex 定时采集排行榜

本教程介绍如何在 Codex 中创建本地定时自动化，通过项目现有的单次包装入口采集 Bilibili 排行榜。自动化只负责定时调用项目代码，不会启动常驻调度进程。

## 前置条件

1. 项目已经安装依赖，并且可以正常导入 `automation` 和 `ranking_collector`。
2. 项目所在设备在执行时间保持开机，Codex 本地运行环境可以访问项目和 Bilibili。
3. 如果需要 Cookie 后备，使用者已经在本机配置 `BILIBILI_COOKIE_FILE`；不要把 Cookie 文件提交到 Git。

创建自动化前，建议先在项目根目录手动执行一次：

```powershell
python -m automation.ranking_once --json
```

如果终端没有可用的 `python` 命令，可以使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m automation.ranking_once --json
```

手动执行成功时，输出应包含整体状态、`run_id`、开始和结束时间，以及各分区的保存条目数。

## 创建自动化卡片

在绑定本项目的 Codex 任务中，要求 Codex 创建一个定时自动化，并使用下面的任务说明：

```text
在当前项目根目录执行一次排行榜采集。先解析当前工作区可用的 Python
运行时，再用该解释器运行 `-m automation.ranking_once --json`。

只允许调用现有单次采集包装入口并读取 SQLite 验证结果；不得修改源代码、
配置或 Git，不得启动长期调度进程。数据库位置必须从
`ranking_collector.config.DATABASE_PATH` 获取，并直接检查该路径、通过
SQLite 查询 `collection_runs`、`collection_partition_results`、
`ranking_snapshots` 和 `ranking_items`。不得使用 `rg --files`、
`git ls-files` 或 Git 状态判断数据库是否存在。

完成后汇报整体状态、collection_run ID、开始与结束时间、耗时，以及全站、
知识、科技、游戏、生活各分区的状态、保存条目数和安全错误摘要。不得显示
Cookie、API Key、请求头、堆栈或敏感本地路径。如果检测到已有采集任务，
按包装入口结果汇报并退出。
```

建议使用以下设置：

| 设置 | 建议值 |
|---|---|
| 名称 | `Bilibili 排行榜定时采集` |
| 项目 | 当前克隆的项目 |
| 执行环境 | 本地 |
| 时区 | `Asia/Shanghai` |
| 执行时间 | 每天 `00:00、06:00、12:00、18:00` |
| 状态 | 启用 |

Codex 生成自动化卡片后，需要在界面中点击一次“创建/启用”。卡片没有启用时，不会按计划运行。

## Python 运行时选择

定时任务不应写死项目作者电脑上的 Python 绝对路径。不同使用者可以采用以下方式之一：

1. 让 Codex 每次解析当前工作区提供的 Python 运行时。
2. 使用当前项目中已经验证可用的 `.venv`。
3. 使用系统 PATH 中稳定可用的 `python`。

优先选择第一种方式，避免系统 PATH、IDE 解释器和项目虚拟环境不一致。无论使用哪一种解释器，自动化都必须绑定当前项目，并在项目根目录执行模块入口。

## 数据库验证

数据库位置由项目配置决定：

```python
from ranking_collector.config import DATABASE_PATH
```

默认位置是 `data/ranking.db`。自动化应以包装入口返回值为主，并从 SQLite 验证：

- `collection_runs`：本轮任务是否创建、结束和成功。
- `collection_partition_results`：各分区是否成功及安全错误摘要。
- `ranking_snapshots`：各分区是否产生快照。
- `ranking_items`：快照实际保存的条目数。

不要通过 `rg --files`、`git ls-files` 或 `git status` 判断数据库是否存在。运行数据通常已被 `.gitignore` 排除，不会出现在 Git 文件列表中。

## 手动测试自动化

创建并启用卡片后，在 Codex 自动化界面执行一次“立即运行”。报告至少应包含：

- `SUCCEEDED`、`PARTIAL_FAILED`、`FAILED`、`SKIPPED_ALREADY_RUNNING` 或 `TIMED_OUT` 状态。
- `collection_run` ID。
- 开始时间、结束时间和耗时。
- 全站、知识、科技、游戏、生活的状态和保存条目数。

分区保存不足 100 条不一定是错误。Bilibili 接口返回不足 100 条时，项目按实际结果保存，不使用其他数据补位。

## 常见问题

### 找不到 `python`

说明自动化所在的 shell 没有配置 Python PATH。让 Codex解析工作区 Python，或者明确使用当前项目中已经验证可用的虚拟环境。不要复制其他电脑上的解释器绝对路径。

### 自动化称数据库不存在，但 Web 页面可以读取数据

先确认自动化是否错误地使用了 Git 文件扫描。应直接读取 `DATABASE_PATH`，再用 SQLite 查询表结构和采集记录。

### 显示已有采集任务

包装入口带有进程锁和未结束任务检查。收到 `SKIPPED_ALREADY_RUNNING` 时，不要绕过锁或再启动另一种采集入口，等待当前任务结束后再检查。

### 定时任务没有执行

检查自动化卡片是否已经创建并启用、项目设备是否开机、项目是否仍位于自动化绑定的位置，以及本地 Codex 是否能够访问该项目。

## 安全要求

- 每位使用者自行配置 `.env`、Cookie 和模型凭据。
- 不要分享或提交 `.env`、`.secrets/`、数据库、日志和本地自动化记忆。
- 自动化报告不得输出 Cookie、API Key、完整请求头、堆栈或敏感本地路径。
- 不要让定时任务修改源代码、配置或 Git。
- 不要同时启用 Codex 自动化、项目 `--schedule` 模式和其他系统计划任务。

