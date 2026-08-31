# DeepSearch 2.1 用户手册

> 适用于当前 CLI 与 Streamlit 工作台 · 最后校准：2026-08-28

## 1. 安装与第一次运行

DeepSearch 要求 Python 3.11 或更高版本。

```powershell
cd D:\code\pycharm\DeepSearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
deepsearch --mock ask "Python 3.13 有哪些新特性"
```

第一次建议使用 `--mock`：它完全不访问网络或模型，能快速确认安装、研究循环、引用、验证和报告保存都正常。默认报告目录为项目根 `reports/`。

只安装 CLI：

```powershell
python -m pip install -e .
```

安装 Web UI：

```powershell
python -m pip install -e ".[ui]"
```

## 2. CLI 提问

```powershell
deepsearch ask "AI 对高等教育的影响、风险和实践案例"
deepsearch --mock ask "对比主流 Agent 框架" --no-stream
python -m deepsearch --mock ask "Python 3.13 什么时候发布"
```

参数位置规则：

- `--mock`、`--config` 是全局参数，放在 `ask`、`interactive` 等子命令之前；
- `--no-stream` 属于 `ask`，放在问题之后；
- `--no-stream` 只隐藏完整报告输出，不会关闭进度或文件保存。

进度阶段：

| 标签 | 含义 |
|---|---|
| `PLAN` | 分类、拆解和初始搜索策略 |
| `SEARCH` | 并发执行查询和搜索源 |
| `FETCH` | 并发抓取正文，失败时保留摘要 |
| `CHECK` | 判断证据是否充分以及停止原因 |
| `RETRY` | 根据缺失维度生成下一轮查询 |
| `VERIFY` | 组织证据和提示可能冲突 |
| `WRITE` | 综合直接答案、关键结论、详细分析和建议 |
| `VALIDATE` | 校验引用、结构、覆盖和文本对应 |
| `SAVED` | 显示报告路径 |
| `INFO` | 当前实现中未单列标签的事件，例如质量评分 |

复杂问题通常至少两轮；事实问题可一轮停止。最大轮次是预算上限，不是每次必须执行的固定次数。

## 3. Web 研究工作台

```powershell
deepsearch web
# 指定端口
deepsearch web --port 8502
# 等价的开发启动方式
python -m streamlit run streamlit_app.py
```

默认访问 `http://localhost:8501`。请从项目根目录运行直接 Streamlit 命令；`deepsearch web` 会自动使用正确的仓库根。

### 页面说明

| 页面 | 用途 |
|---|---|
| **首页** | 产品定位、研究流程、系统状态和推荐问题 |
| **研究** | 选择研究强度、提交问题、实时查看阶段、阅读报告和追问 |
| **证据** | 五维评分、来源列表、轮次轨迹、停止原因和正文预览 |
| **报告** | 搜索、阅读和下载根 `reports/` 中的 Markdown/Text/JSON 资产 |
| **设置** | 配置引擎预算、缓存、自主任务和模型状态 |

### 三档研究强度

| 模式 | 预算 | 推荐场景 |
|---|---|---|
| 快速 | 1 轮、8 来源、每查询 3 候选 | 单一事实、快速验证 |
| 均衡 | 沿用 `config.json`，默认 3 轮/16 来源 | 常规分析 |
| 深度 | 4 轮、28 来源、每查询 6 候选 | 复杂对比、技术选型 |

模式只影响当前研究，不会改写持久化配置。

研究页的“报告与研究要求”还可设置：知识领域、新闻/公告/研究等信息类型、时间范围、Markdown/Text/JSON、300–5000 字目标篇幅、目标读者、报告章节和自定义模板要求。这些字段会进入查询规划、报告生成和文件保存；不是只改变页面显示。

## 4. 追问

CLI 使用：

```powershell
deepsearch --mock interactive
```

第一次输入执行完整研究；之后的输入视为对上一份结果的追问。新问题与已有报告词项重叠足够时复用原来源，不足时自动追加完整搜索。每次追问仍保存为独立报告。输入 `/quit` 或 `/exit` 结束。

Web 研究页同样保留当前浏览器会话的最近结果。点击“开始新研究”会清空当前会话上下文，但不会删除历史报告或配置。

当前追问是单会话、上一结果优先，不是长期记忆或跨报告知识库。

## 5. 在线搜索与模型配置

在线检索不要求模型 Key。默认同时使用 DuckDuckGo 和 Wikipedia；两者都无结果时自动切换到带明显标识的 Mock 来源。

兼容模型使用 `/chat/completions`：

```powershell
$env:DEEPSEARCH_API_KEY = "..."
$env:DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"
$env:DEEPSEARCH_MODEL = "gpt-4o-mini"
deepsearch ask "你的问题"
```

Web 可复制：

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

再编辑真实 `secrets.toml`。该文件已被 `.gitignore` 排除。普通设置保存会强制清空 `api_key`，但仍不要把密钥写入 `config.json`。

模型缺失时使用确定性报告器；调用错误或超时会重试一次，再降级，不丢失已获取证据。

## 6. 配置文件

```powershell
Copy-Item config.example.json config.json
deepsearch --config .\config.json --mock ask "测试配置"
```

配置优先级：默认值 → JSON → 环境变量 → 命令/调用参数。相对的报告和缓存路径以配置文件所在目录为基准，而不是 Python 包目录。

常调参数：

- `mode`：`auto` 或 `mock`；
- `max_rounds`：均衡模式最大轮次；
- `results_per_query`、`max_sources`：候选与来源预算；
- `request_timeout`、`fetch_workers`：网络超时和抓取并发；
- `cache_enabled`、`cache_ttl_seconds`：缓存开关和 TTL；
- `per_domain_limit`：降低单站点垄断；
- `reports_dir`、`cache_dir`：运行数据路径；
- `log_level`：默认 `INFO`。

完整字段和默认值见根 [README](../README.md)。

## 7. 历史报告

```powershell
deepsearch history
deepsearch history --query "大模型"
```

结果按新到旧排列，查询匹配文件名和 Markdown 正文。Web“报告”页可搜索、预览和下载同一目录的文件。

每份报告通常包含元信息、摘要、详细分析、可信度与争议信息、全部来源表。生成报告是运行产物；版本升级不会批量改写历史内容。

## 8. 自主研究任务

```powershell
deepsearch tasks add "AI 日报" "总结大模型与 Agent 领域的重要变化" `
  --time 10:00 `
  --schedule-type daily `
  --domain "人工智能与大模型" `
  --types "新闻,公告,研究" `
  --time-scope "最近 24 小时" `
  --format markdown `
  --words 1800 `
  --audience "技术负责人" `
  --sections "摘要,重大事件,技术进展,行业影响,建议与下一步" `
  --instructions "按重要性排序，区分事实、判断和建议" `
  --profile 深度 `
  --deliver-to-customer-service `
  --classification internal

deepsearch tasks list
deepsearch tasks disable "AI 日报"
deepsearch tasks enable "AI 日报"
deepsearch --mock tasks run "AI 日报"
deepsearch tasks remove "AI 日报"
```

周期类型：

- `daily`：每天指定时间；
- `weekly`：通过 `--weekdays "0,2,4"` 选择周一、周三、周五；
- `once`：通过 `--date 2026-09-01` 指定单次日期；
- 旧 `topics` 命令仍可使用，但新项目推荐 `tasks`。

任务配置会真正进入 Agent：领域、信息类型和时间窗口会加入查询；篇幅、读者、章节和模板要求会进入报告器；文件格式决定最终保存为 `.md`、`.txt` 或 `.json`。

如果使用 `--deliver-to-customer-service`，还要在 `config.json` 的 `customer_service.enabled` 开启全局联动，并通过环境变量提供：

```powershell
$env:DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY = "与 CustomerService 一致的专用密钥"
$env:DEEPSEARCH_CUSTOMER_SERVICE_API_KEY = "可选：CustomerService 全局 API Key"
```

Web 端可在“设置 → 知识库联动”完成普通配置，并在单个任务中勾选自动提交。投递失败不会重新搜索；报告路径、内容哈希和非敏感元数据会写入 `.cache/customer-service-outbox`，调度器后续轮询会指数退避重试。报告正文不会复制到 outbox。`confidential` 默认会被 CustomerService 拒绝，除非接收端显式允许。

```powershell
# 查看失败原因、报告路径和下次重试时间（不显示正文或密钥）
deepsearch delivery list

# 修正密钥、URL 或 confidential 接收策略后，强制重试原本不可自动重试的 4xx 记录
deepsearch delivery retry --force
```

运行调度器：

```powershell
# 检查一次所有到期且尚未成功执行的任务
deepsearch schedule --once

# 每 30 秒检查一次的前台进程
deepsearch schedule --poll 30
```

调度状态保存在配置文件旁的 `.deepsearch-schedule-state.json`。只有任务成功生成报告后才记录完成；一个任务失败不会阻止其他到期任务。长期运行建议用 Windows 任务计划程序或 cron 周期调用 `schedule --once`。

## 9. 缓存、来源分和质量分

在线搜索结果和成功正文默认缓存 6 小时；失败请求与 Mock 数据不进入在线缓存。Web 设置页可清理缓存，也可把 `cache_enabled` 设为 `false`。

来源排序综合问题相关性、域名质量、内容完整度和抓取状态，并限制单域名候选数量。来源分表示证据优先级，不表示该来源的每句话都是真的。

报告五维评分用于暴露引用、覆盖、来源结构和抓取健康度风险。它不是事实真伪概率，也不能替代查看原文、时效检查或人工审核。

## 10. 测试与演示前检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前应看到 `Ran 31 tests` 和 `OK`。再执行：

```powershell
.\.venv\Scripts\python.exe -m deepsearch --mock ask "Python 3.13 有哪些新特性" --no-stream
.\.venv\Scripts\python.exe -m deepsearch --mock ask "对比 2025 年主流大模型的推理能力和成本" --no-stream
```

验收：事实题 1 轮；复杂题至少 2 轮并显示补充搜索；报告都位于根 `reports/`，包含引用和来源表。

## 11. 常见问题排查

### `deepsearch` 命令不存在

确认虚拟环境已激活，并重新执行 `python -m pip install -e .`。也可以直接使用 `python -m deepsearch ...`。

### Web 命令提示未安装 Streamlit

```powershell
python -m pip install -e ".[ui]"
```

### 页面能打开但在线研究很慢

免费搜索和网页抓取受网络影响。先切换 Mock 验证流程；在线调试可适当降低 `request_timeout` 或减少来源预算。日志位于配置文件目录下的 `logs/deepsearch.log`。

### 一直使用 Mock 来源

检查是否传了 `--mock`、`mode` 是否为 `mock`、`search_provider` 是否为 `mock`。若为 `auto`，查看日志是否记录两个在线 provider 都无结果。

### API Key 无效

确认 Key、`base_url` 和模型名属于同一兼容服务。系统会记录两次调用失败并使用确定性报告器；搜索结果仍会保存。

### 网页只有摘要

JavaScript 页面、登录墙、PDF 或反爬站点可能无法提取正文。报告来源表会明确标成“仅使用搜索摘要”，不会伪装为正文成功。

### 完全断网

显式使用 `--mock` 或设置 `$env:DEEPSEARCH_MODE = "mock"`。`auto` 会先等待在线超时再降级，因此更慢。

### 报告出现在错误目录

当前 2.1 已统一根目录逻辑。确认使用的是本项目最新 editable install，并从 `D:\code\pycharm\DeepSearch` 重新执行 `python -m pip install -e .`。默认结果应只写入根 `reports/`。

### 中文终端乱码

优先使用 PowerShell 7 或 Windows Terminal；旧控制台可运行 `chcp 65001`。Markdown 始终按 UTF-8 保存。
