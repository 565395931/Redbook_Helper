# Agent.md

本文件用于让后续 Agent 在这个仓库内工作时，先基于已知索引行动，避免每次从头全量扫描目录，浪费 token。

## 1. 工作目标

- 先读最少的文件，建立足够上下文，再动手修改。
- 默认不要递归扫描整个仓库。
- 默认不要读取 `vendor/`、`.venv/`、`app/data/debug/` 里的大量文件。
- 只有当任务明确涉及外部爬虫、依赖源码或调试产物时，才进入这些目录。

## 2. 仓库概览

这是一个本地运行的 `FastAPI + SQLite` 小红书运营分析工具。

核心目录：

- `app/main.py`
  - 主应用入口。
  - 包含 FastAPI 初始化、页面路由、表单处理、主要业务编排。
  - 文件较大，修改前优先用搜索定位目标函数，不要整文件反复通读。
- `app/db.py`
  - SQLite 连接、JSON 辅助函数、全量 schema/migration。
  - 任何涉及数据结构、字段、表关系的任务，先读这里。
- `app/services/`
  - `crawler.py`：对接 `vendor/Spider_XHS` 的爬虫适配层。
  - `copywriting.py`：文案生成逻辑。
  - `ai_scoring.py`：AI 打分逻辑。
  - `dedupe.py`：重复内容检测。
  - `scoring.py`：评分相关辅助逻辑。
- `app/templates/index.html`
  - 当前主要页面模板。
- `app/static/css/app.css`
  - 页面样式。
- `app/static/js/app.js`
  - 前端交互脚本。
- `prompts/`
  - LLM 提示词模板。
- `scripts/setup_vendor.ps1`
  - 拉起本地 vendor 依赖的脚本。
- `README.md`
  - 启动方式、项目定位、vendor 说明。

高噪音目录：

- `vendor/`
  - 外部参考/依赖代码。
  - 默认视为黑盒，不要为普通业务修改先扫它。
- `.venv/`、`.venv.broken_*`
  - 虚拟环境，忽略。
- `app/data/debug/`
  - 调试日志和历史 JSON，数量可能持续增长，默认忽略。
- `.git/`
  - 无需扫描内容。

## 3. 首选阅读顺序

除非任务非常明确，否则按下面顺序建立上下文：

1. `README.md`
2. `pyproject.toml`
3. `app/main.py` 中与任务直接相关的函数或路由
4. `app/db.py`（如果任务涉及数据）
5. `app/services/` 中对应模块
6. `app/templates/index.html` / `app/static/css/app.css` / `app/static/js/app.js`（如果任务涉及 UI）

不要一开始就：

- 递归 `Get-ChildItem -Recurse` 全仓库
- 通读 `app/main.py` 全文件
- 深入 `vendor/Spider_XHS` 或 `vendor/xhs_ai_publisher`

正确做法是：

- 先用 `rg --files` 看顶层结构
- 再用 `rg "函数名|路由|字段名|文案关键字"` 精确定位
- 最后按需读取少量文件片段

## 4. 常见任务的最小读取集

### 4.1 改页面布局/交互

先读：

- `app/templates/index.html`
- `app/static/css/app.css`
- `app/static/js/app.js`
- `app/main.py` 中对应页面路由

一般不用读：

- `vendor/`
- `app/data/debug/`

### 4.2 改数据库字段/业务状态

先读：

- `app/db.py`
- `app/main.py`
- 对应 `app/services/*.py`

重点注意：

- 这是 SQLite 项目，schema 变更通常直接维护在 `migrate()` 里。
- 修改字段时，要同步检查表单保存、列表展示、查询排序、插入更新逻辑。

### 4.3 改爬虫或抓取流程

先读：

- `app/services/crawler.py`
- `app/main.py` 中调用抓取的路由

只有在适配层无法定位问题时，才进一步读：

- `vendor/Spider_XHS/`

### 4.4 改 AI 打分或文案生成

先读：

- `app/services/ai_scoring.py` 或 `app/services/copywriting.py`
- `prompts/` 下对应 prompt
- `app/main.py` 中调用入口
- `app/db.py`（如果结果要持久化）

### 4.5 查 bug

优先顺序：

1. 读报错位置对应文件
2. 搜索路由、函数名、字段名
3. 看最小必要调用链
4. 最后才查看 `app/data/debug/` 中单个相关 JSON

不要把 `app/data/debug/` 当作默认上下文来源。

## 5. 搜索和读取策略

推荐命令：

- 列文件：`rg --files`
- 搜文本：`rg "keyword" app prompts README.md`
- 找路由：`rg "@app\\.(get|post)|def " app/main.py`
- 找字段：`rg "field_name|table_name" app`

读取策略：

- 优先读单文件、少量片段。
- 发现 `app/main.py` 很大时，先搜索，再按命中位置读取附近内容。
- 如果已经确认任务只在某个模块，不要继续扩散式读取无关文件。

## 6. 已知架构判断

- 项目当前是单体应用，不是多服务架构。
- `app/main.py` 是事实上的主控制器，许多页面和流程直接在这里串起来。
- 数据库存储在 `app/data/redbook.db`。
- 图片媒体和调试产物都在 `app/data/` 下。
- `vendor/Spider_XHS` 是抓取能力来源，但属于外部依赖边界，不应作为大多数任务的首扫区域。

## 7. 修改时的约定

- 若只是修复局部问题，尽量局部改动，不做无关重构。
- 若新增字段或状态，记得同时检查：
  - `app/db.py`
  - `app/main.py`
  - 模板展示
  - 表单提交
  - 相关服务层
- 若修改 prompt，检查前端文案和结果落库字段是否仍匹配。
- 若任务完成后发现仓库结构认知发生变化，应顺手更新本文件，而不是让下一个 Agent 重新摸索。

## 8. 默认忽略清单

除非任务明确要求，否则不要主动读取：

- `vendor/**`
- `.venv/**`
- `.venv.broken_*/**`
- `app/data/debug/**`
- `app/data/media/**`
- `uv.lock`

其中 `uv.lock` 只有在依赖版本排查时才需要看。

## 9. 一句话执行规则

先看索引，再定点搜索，再读取最小上下文，再修改；不要为了“保险”把整个仓库重新扫一遍。
