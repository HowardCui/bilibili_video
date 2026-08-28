# 排行榜长期趋势实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Shiny 排行榜页面增加按分区和时间范围查询的长期趋势，包括视频在榜统计、排名及指标曲线、分区换血率和缺失区间。

**Architecture:** `web_app.trends.queries` 读取受限历史事实，`service` 计算稳定的页面模型并限制点数，`ui` 生成原生 SVG，`server` 负责 Shiny 响应绑定。现有排行榜最新快照模块保持独立，SQLite 表结构不变。

**Tech Stack:** Python 3.12、SQLite、Shiny for Python Core、htmltools 原生 SVG、pytest。

**Spec:** `docs/superpowers/specs/2026-08-28-ranking-long-term-trends-design.md`

## Global Constraints

- 原始 `ranking_snapshots`、`ranking_items` 和 `collection_partition_results` 是唯一事实来源。
- 页面继续只读，不启动采集任务。
- 每条图表序列最多 240 点。
- 相邻有效快照超过 24 小时时换血率为空。
- 业务代码不新增装饰器；Shiny 官方绑定装饰器可以使用。
- 测试只写普通 pytest 函数，不写测试类，不访问真实网络。
- 页面不得显示 SQL、堆栈、Cookie、API Key 或敏感本地路径。

---

### Task 1: 历史查询边界

**Files:**
- Create: `web_app/trends/__init__.py`
- Create: `web_app/trends/queries.py`
- Create: `tests/test_web_trend_queries.py`

**Interfaces:**
- Produces: `resolve_time_range(range_key: str, now: datetime) -> datetime | None`
- Produces: `load_partition_history(partition, range_key, database_path, now=None, row_limit=50000) -> dict`
- Result keys: `partition`, `range_key`, `started_at`, `ended_at`, `snapshots`, `partition_results`, `truncated`.

- [ ] **Step 1: Write failing query tests**

Create a temporary initialized SQLite database with several successful snapshots and one failed partition result. Assert that `24H`, `7D`, `30D`, and `ALL` resolve correctly, another partition is excluded, rows are ordered, and `row_limit` sets `truncated=True`.

- [ ] **Step 2: Run query tests and verify RED**

Run: `python -m pytest tests/test_web_trend_queries.py -q`

Expected: import failure because `web_app.trends.queries` does not exist.

- [ ] **Step 3: Implement parameterized bounded queries**

Use enabled-partition validation, timezone-aware UTC conversion, bound SQL parameters, a positive bounded `row_limit`, and one extra row to detect truncation. Return primitive dictionaries; do not calculate trend metrics in this module.

- [ ] **Step 4: Run query tests and verify GREEN**

Run: `python -m pytest tests/test_web_trend_queries.py -q`

Expected: all query tests pass.

### Task 2: 趋势指标服务

**Files:**
- Create: `web_app/trends/service.py`
- Create: `tests/test_trend_service.py`

**Interfaces:**
- Consumes: history dictionary from `load_partition_history`.
- Produces: `build_trend_page_data(history, selected_bvid=None, metric="views", max_points=240) -> dict`.
- Produces: `aggregate_series(points, max_points, mode) -> list[dict]`.
- Page result keys: `status`, `metadata`, `video_choices`, `selected_bvid`, `video_summary`, `rank_series`, `metric_series`, `turnover_series`, `heat_series`, `lists`, `missing_intervals`.

- [ ] **Step 1: Write failing service tests**

Use primitive history fixtures to assert range-scoped first/last appearance, cumulative and trailing consecutive counts, best/worst/current rank, re-entry after one valid absence, preserved metric decreases, stale turnover points, failed-result gaps, heat totals, and 240-point aggregation.

- [ ] **Step 2: Run service tests and verify RED**

Run: `python -m pytest tests/test_trend_service.py -q`

Expected: import failure because `web_app.trends.service` does not exist.

- [ ] **Step 3: Implement pure trend calculations**

Keep calculations independent of SQLite and Shiny. Compare adjacent valid snapshots for turnover. Do not count failed collection results as valid absences. Choose the latest snapshot's highest-ranked video when no valid selected BVID is supplied.

- [ ] **Step 4: Run service tests and verify GREEN**

Run: `python -m pytest tests/test_trend_service.py -q`

Expected: all service tests pass.

### Task 3: 原生 SVG 和趋势页面组件

**Files:**
- Create: `web_app/trends/ui.py`
- Create: `tests/test_trend_ui.py`
- Modify: `web_app/www/layout.css`

**Interfaces:**
- Produces: `build_trends_ui() -> Tag`.
- Produces: `render_line_chart(series, title, value_label, reverse_y=False, missing_intervals=None) -> Tag`.
- Produces: `render_trend_summary(page_data) -> Tag` and `render_trend_lists(page_data) -> Tag`.

- [ ] **Step 1: Write failing UI tests**

Assert fixed responsive SVG `viewBox`, escaped labels, reverse ranking axis metadata, `<title>` point details, no polyline across `None` values, single-point rendering, and stable empty-state copy.

- [ ] **Step 2: Run UI tests and verify RED**

Run: `python -m pytest tests/test_trend_ui.py -q`

Expected: import failure because `web_app.trends.ui` does not exist.

- [ ] **Step 3: Implement safe SVG rendering and controls**

Build SVG through `htmltools.Tag`, not raw HTML strings. Use existing CSS variables for grid, line, point, text, success, warning, and muted states. Add responsive CSS without horizontal overflow or continuous animation.

- [ ] **Step 4: Run UI tests and verify GREEN**

Run: `python -m pytest tests/test_trend_ui.py -q`

Expected: all UI tests pass.

### Task 4: Shiny integration

**Files:**
- Create: `web_app/trends/server.py`
- Modify: `web_app/ranking/ui.py`
- Modify: `web_app/ranking/server.py`
- Modify: `tests/test_web_app.py`
- Create: `tests/test_trend_server.py`

**Interfaces:**
- Produces: `load_trend_page_data(partition, range_key, selected_bvid, metric, database_path, now=None) -> dict`.
- Produces: `register_trends_server(input, output, session, database_path) -> None`.
- `register_ranking_server` continues to own the shared partition selector and registers the trend bindings once.

- [ ] **Step 1: Write failing integration tests**

Assert the ranking UI contains time, video and metric controls plus summary/chart/list outputs. Assert expected SQLite and repository failures map to `QUERY_FAILED` without exception text. Assert app construction exposes all output IDs.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python -m pytest tests/test_trend_server.py tests/test_web_app.py -q`

Expected: missing trend controls or bindings.

- [ ] **Step 3: Integrate trends into ranking page**

Mount `build_trends_ui()` below Top 100. Reuse `input.ranking_partition()`. Update video choices from the safe page model and preserve a valid selection when possible. Register summary, charts, lists, status and metadata outputs.

- [ ] **Step 4: Run integration tests and verify GREEN**

Run: `python -m pytest tests/test_trend_server.py tests/test_web_app.py -q`

Expected: all integration tests pass.

### Task 5: Full verification and documentation state

**Files:**
- Modify: `docs/下阶段计划.md`

**Interfaces:**
- No new runtime interface.

- [ ] **Step 1: Run focused trend suite**

Run: `python -m pytest tests/test_web_trend_queries.py tests/test_trend_service.py tests/test_trend_ui.py tests/test_trend_server.py -q`

Expected: all trend tests pass.

- [ ] **Step 2: Run static checks**

Run: `python -m ruff check web_app/trends tests/test_web_trend_queries.py tests/test_trend_service.py tests/test_trend_ui.py tests/test_trend_server.py`

Expected: no lint errors.

- [ ] **Step 3: Run complete regression suite**

Run: `python -m pytest -q`

Expected: all existing and new tests pass.

- [ ] **Step 4: Update the phase checklist**

Mark batch two complete only after focused, lint, full regression, and manual page checks succeed. Record any manual-only browser verification still required instead of claiming it was automated.
