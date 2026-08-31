# DeepSearch

> 当前版本：2.1.0 · 文档校准：2026-08-28 · Python 3.11+

DeepSearch 是一个本地优先、可解释、可追溯的迭代式研究 Agent。用户输入问题后，系统会分类与拆解问题，并发搜索和读取网页，每轮判断证据是否充分；信息不足时会调整查询继续研究，最后先给出直接答案、关键结论和建议，再附带 `[n]` 引用、证据局限、研究轨迹和完整来源。

项目同时提供 CLI 和明亮简洁的 Streamlit 多页面工作台。没有 API Key、网络中断或免费搜索不可用时，系统会切换到确定性报告器和明确标识的 Mock 来源，保证核心反馈循环仍可演示和测试。

## 核心能力

- **Agent 反馈循环**：规划 → 搜索 → 抓取 → 充分性判断 → 查询调整，而不是固定的一次性流水线。
- **任务自适应**：区分事实、对比、开放探索和深度调研；事实问题可一轮停止，复杂问题要求更多角度。
- **多路检索**：在线模式并发调用 DuckDuckGo 与 Wikipedia；结果为空时降级到 Mock。
- **证据治理**：URL 去重、来源排序、域名多样性、正文/摘要状态、交叉验证和保守的数值冲突提示。
- **报告质量门**：校验引用编号、报告结构、子问题覆盖和论点—来源词项对应；失败时修复或回退到抽取式报告。
- **结论优先报告**：无模型模式也会综合摘要、关键结论、分维度分析和行动建议，不再把来源链接当成最终答案。
- **可解释评分**：输出引用完整性、问题覆盖度、来源多样性、来源质量和检索健康度五维评分。
- **工程可靠性**：超时、异常隔离、并发抓取、TTL 磁盘缓存、结构化轮次轨迹和可重复的 Mock 测试。
- **证据感知追问**：已有证据足够时复用；涉及最新、变化、新闻、公告或数据时自动补搜并重新评估。
- **自主研究任务**：支持每日、每周和单次周期，并配置领域、信息类型、时间范围、读者、篇幅、章节模板和研究强度。
- **CustomerService 联动**：任务完成后可自动提交最终报告，失败进入仅含路径与哈希的 outbox，不重复执行搜索。
- **多格式交付**：通过同一质量门后导出 Markdown、纯文本或 JSON，报告中心统一检索和下载。
- **清晰分层**：领域、应用、基础设施、展示和调度分层，由 `bootstrap.py` 统一装配依赖。

## 快速开始

### 1. 创建环境并安装

```powershell
cd D:\code\pycharm\DeepSearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

只使用 CLI 时也可执行 `python -m pip install -e .`；只增加 Web UI 时使用 `python -m pip install -e ".[ui]"`。

### 2. 首次离线验证

```powershell
deepsearch --mock ask "Python 3.13 有哪些新特性"
```

`--mock` 不访问网络或模型服务，适合初次安装、自动化测试和现场演示。报告统一保存到项目根目录的 `reports/`。

### 3. 启动 Web 工作台

```powershell
deepsearch web
# 等价命令
python -m streamlit run streamlit_app.py
```

浏览器打开 `http://localhost:8501`。页面包括：首页、研究、证据、报告和设置。CLI 与 Web 共享同一份配置、缓存和报告目录。

### 4. 在线研究

在线搜索不要求 API Key；配置兼容模型后可增强报告表达：

```powershell
$env:DEEPSEARCH_API_KEY = "your-key"
$env:DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"
$env:DEEPSEARCH_MODEL = "gpt-4o-mini"
deepsearch ask "评估当前主流 Agent 框架的生产适用性"
```

不要把真实密钥写进 `config.json` 或提交到 Git。Web 端也可使用未提交的 `.streamlit/secrets.toml`。

## 常用命令

```powershell
# 单次研究；--no-stream 只显示进度和保存路径
deepsearch --mock ask "对比主流 Agent 框架" --no-stream

# 支持上下文复用和必要时补搜的追问模式
deepsearch --mock interactive

# 历史报告
deepsearch history
deepsearch history --query "Agent"

# 自主研究任务和调度
deepsearch tasks add "AI 日报" "总结大模型与 Agent 领域的重要变化" `
  --time 10:00 --schedule-type daily --domain "人工智能" `
  --types "新闻,公告,研究" --time-scope "最近 24 小时" `
  --words 1800 --audience "技术负责人" --profile 深度 `
  --deliver-to-customer-service --classification internal
deepsearch tasks list
deepsearch --mock tasks run "AI 日报"
deepsearch schedule --once
deepsearch schedule --poll 30

# 查看 outbox；修正密钥或接收策略后强制重投 4xx 记录
deepsearch delivery list
deepsearch delivery retry --force

# 自定义 Web 端口
deepsearch web --port 8502
```

全局参数 `--mock`、`--config` 必须放在子命令之前。

## 配置

复制 `config.example.json` 为 `config.json`。相对路径均以配置文件所在目录为基准解析，因此从其他工作目录启动时仍能稳定找到报告和缓存。

| 字段 | 含义 | 默认值 |
|---|---|---:|
| `mode` | `auto` 在线优先；`mock` 完全离线 | `auto` |
| `search_provider` | `duckduckgo` 或 `mock` | `duckduckgo` |
| `max_rounds` | 均衡模式最大搜索轮次 | `3` |
| `results_per_query` | 每个搜索源、每个查询的候选数 | `4` |
| `max_sources` | 单次研究最多进入报告的来源数 | `16` |
| `request_timeout` | 单个搜索/抓取请求超时秒数 | `8.0` |
| `fetch_workers` | 正文抓取线程数 | `8` |
| `reports_dir` | Markdown/Text/JSON 报告目录 | `reports` |
| `cache_dir` | 查询与正文缓存目录 | `.cache/deepsearch` |
| `cache_ttl_seconds` | 在线缓存有效期 | `21600` |
| `cache_enabled` | 是否启用在线缓存 | `true` |
| `per_domain_limit` | 候选阶段每域名最多保留数 | `2` |
| `log_level` | 文件日志级别 | `INFO` |
| `llm` | OpenAI 兼容接口地址、模型和空密钥占位 | 见示例 |
| `customer_service` | 自动投递开关、服务地址、重试和 outbox；密钥字段始终保存为空 | 默认关闭 |
| `research_tasks` | 自主任务列表及研究/交付要求 | 示例中含一个默认停用任务 |

Web 研究强度只修改本次运行的配置副本：快速为 1 轮/8 来源，均衡沿用配置，深度最多 4 轮/28 来源。

## 真实项目结构

```text
DeepSearch/
├── deepsearch/
│   ├── domain/                 # 纯领域模型、来源排序
│   ├── application/            # 研究用例、端口、规划、验证、质量评估
│   ├── infrastructure/         # 搜索、抓取、缓存、LLM、报告、配置、文件存储和知识库投递
│   │   └── search/             # DuckDuckGo、Wikipedia、Mock 适配器
│   ├── presentation/
│   │   ├── cli.py              # CLI 与 Web 启动子命令
│   │   └── web/                # Streamlit 状态、视图模型和共享样式入口
│   ├── scheduling/             # 自主任务模型、管理和周期调度
│   ├── bootstrap.py            # 组合根、公共 facade、研究强度
│   └── __main__.py             # python -m deepsearch
├── app_pages/                  # 首页、研究、证据、报告、设置
├── assets/                     # 集中式 CSS 和品牌 SVG
├── .streamlit/                 # 明亮主题与 secrets 示例
├── docs/                       # 架构、用户、开发、追踪和演示文档
├── tests/                      # 31 项离线优先回归测试
├── reports/                    # 统一的运行报告目录
├── streamlit_app.py            # Streamlit 多页面入口
├── config.example.json
└── pyproject.toml
```

运行时缓存、日志、真实 secrets、用户配置和生成报告不属于源码架构。`__pycache__` 也不应作为模块看待。

## 处理流程与依赖方向

```text
CLI / Streamlit / Scheduler
          ↓
bootstrap.py（创建并注入具体实现）
          ↓
ResearchService（迭代研究用例） → domain（模型与排序规则）
          ↑
infrastructure（实现 application/ports.py 中的端口）
```

`DeepSearchAgent` 是兼容外部调用的 facade；真正的研究循环位于 `application/service.py`。展示层不会直接创建搜索器或写报告。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# 或
.\.venv\Scripts\python.exe -m pytest
```

当前共 31 项测试，覆盖规划与停止条件、研究要求进入查询、复杂问题迭代、结论优先摘要、时效追问补搜、搜索降级、并发抓取、缓存、来源排序、引用支持校验、三种文件导出、质量评分、研究模式隔离、每日/每周/单次任务幂等性、CustomerService 成功投递与 outbox 恢复、密钥不落盘/不进入错误记录，以及 CLI/Web 仓库根目录一致性。

## 文档导航

- [文档索引](docs/README.md)：文档边界、阅读顺序和维护规则。
- [架构说明](docs/ARCHITECTURE.md)：模块职责、依赖规则、时序和扩展点。
- [用户手册](docs/USER_GUIDE.md)：CLI、Web、配置、调度与故障排查。
- [开发笔记](docs/DEV_NOTES.md)：技术取舍、算法边界和继续开发指南。
- [需求追踪](docs/REQUIREMENTS_TRACEABILITY.md)：F1–F16 的实现与测试证据。
- [项目总结与演示手册](docs/PROJECT_SUMMARY.md)：评估、难点、面试问题和演示脚本。
- [2.1 完整项目总结](DeepSearch-V2.1-项目总结与演示手册.md)：本次升级、运行、任务配置、验收和 8 分钟演示。
- [原始产品需求](pro.md)：需求历史基线；顶部状态说明标识当前实现差异。

## 已知边界与下一步

- 免费 HTML 搜索会受网络和页面结构影响；Mock 降级保证流程可用，但不代表实时事实。
- 标准库正文抽取不处理 JavaScript 渲染、登录墙、PDF/OCR 和复杂表格。
- 当前论点校验是词项对应，不是自然语言蕴含证明；质量分是风险提示，不等于事实真伪。
- Streamlit 研究仍为同步任务；生产化需要任务队列、取消、重试、断点恢复、配额和集中观测。
- 下一阶段优先建设 claim-evidence graph、语义重排、离线评测集和跨报告知识索引。
