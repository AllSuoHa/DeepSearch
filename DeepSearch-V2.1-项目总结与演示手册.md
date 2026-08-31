# DeepSearch 2.1 项目总结、使用与演示手册

> 版本：2.1.0  
> 校准日期：2026-08-28  
> 定位：能自主迭代、综合结论、追问补搜并按计划生成研究资产的本地 Deep Research Agent

## 一、项目结论

DeepSearch 2.1 的完整工作方式是：

```text
用户问题/自主任务
  → 理解目标、领域、信息类型、时间范围和交付要求
  → 拆解研究维度并生成查询
  → 多搜索源并发检索、去重、排序和正文读取
  → 评估来源数、来源域和每个维度的交叉证据
  → 不够：记录证据缺口，改写查询，再搜索
  → 够了或达到安全预算：交叉验证和冲突提示
  → 先生成直接答案、关键结论、详细分析和建议
  → 校验引用、章节、覆盖度和论点—来源文本对应
  → 评分并导出 Markdown/Text/JSON
  → 用户追问时判断复用证据还是重新检索
```

因此它不是“搜索链接聚合器”，也不是固定的“搜一次 → 总结”流水线。Agent 的自主性体现在每轮都有结构化状态、充分性判断、缺失维度、下一轮查询和明确停止原因。

## 二、本次 2.1 升级解决了什么

### 1. 报告不再只是链接清单

旧确定性报告器的摘要只写“搜索了几轮、获得几个来源”，详细分析几乎逐条摘录来源。2.1 改为结论优先结构：

1. 摘要：事实题第一句话直接回答；复杂题给出 3–4 条综合发现；
2. 关键结论：结论、引用和可信度紧邻展示；
3. 详细分析：按规划维度给综合判断和证据依据；
4. 对比表：对比任务给出可验证发现，而非只列标题；
5. 建议与下一步：根据证据缺口、冲突、Mock 或来源集中度生成；
6. 证据局限与争议：明确摘要降级、Mock、数字口径等风险；
7. 研究过程：逐轮展示查询、来源增量、充分性评估和后续动作；
8. 全部来源：最后提供完整溯源表。

配置 LLM 时，集中式 Prompt 明确要求“不是罗列链接”；无 LLM 时，确定性报告器使用证据句综合，仍能输出完整报告。

### 2. 研究要求真正进入 Agent

新增两个领域对象：

| 对象 | 字段 | 作用 |
|---|---|---|
| `ResearchBrief` | 研究领域、目标、信息类型、时间范围 | 加入初始查询和补充查询，限定研究边界 |
| `ReportSpecification` | 文件格式、篇幅、读者、语言、章节、自定义要求 | 驱动模型 Prompt、确定性报告结构和最终文件导出 |

这避免了“前端有输入框，但后端完全不用”的伪功能。

### 3. 追问更像研究 Agent

追问先计算已有报告和来源对问题的证据覆盖：

- 覆盖充分且不要求新时效：复用已有来源，快速生成追问报告；
- 包含“最新、最近、变化、更新、新闻、公告、数据、来源”等要求：强制重新搜索；
- 关键词覆盖不足：合并原问题和追问，重新进入完整迭代循环。

每次追问仍生成独立报告，保留引用和来源。

### 4. 从主题定时器升级为自主研究任务

`ScheduledResearchTask` 支持：

| 模块 | 可配置内容 |
|---|---|
| 时间周期 | 每日、每周、单次 |
| 时间参数 | HH:MM、每周执行日、单次日期 |
| 任务状态 | 启用、停用、立即运行、删除 |
| 研究对象 | 问题、知识领域、研究目标 |
| 信息边界 | 新闻、公告、知识、研究、数据、政策；最近 24 小时/7 天/30 天/不限 |
| 研究预算 | 快速、均衡、深度 |
| 报告交付 | Markdown/Text/JSON、300–5000 字、目标读者、语言 |
| 模板 | 章节列表和自定义内容要求 |

任务到期后创建对应强度的 Agent，重新执行完整研究循环。只有成功生成报告才写幂等状态；单个任务失败不会影响其他任务。

## 三、核心架构

```text
presentation/    CLI、Streamlit、多格式报告中心
       ↓
bootstrap.py     组合根、研究强度、公共 DeepSearchAgent facade
       ↓
application/     ResearchService、Planner、Validator、Evaluator、Ports
       ↓
domain/          ResearchBrief、ReportSpecification、Plan、Source、Trace、Result
       ↑
infrastructure/  Search、Fetcher、Cache、LLM、Reporter、Storage、Config

scheduling/      ScheduledResearchTask、TaskManager、TaskScheduler
       ↓
bootstrap.py     到期时创建指定强度的 Agent
```

关键边界：

- `ResearchService` 拥有反馈循环，但不创建搜索器、缓存、模型或存储；
- `bootstrap.py` 是唯一组合根；
- 报告先以规范 Markdown 通过质量门，再导出其他格式；
- Streamlit 页面只负责输入和展示，不实现研究算法；
- 调度器不复制研究逻辑，只把任务中的 `ResearchBrief` 交给 Agent。

## 四、Agent 如何反复评估

复杂问题不是固定搜索三轮。每轮主要检查：

1. 可用来源数量；
2. 独立来源域数量；
3. 每个子问题是否至少有两条支持证据；
4. 本轮是否获得新来源；
5. 是否到达轮次或来源硬预算。

第一轮不足时，Planner 根据缺失维度生成“官方文档、原始数据”查询；后续仍不足时转向“独立评测、局限、争议、反方观点”。并发搜索中的相同 URL 会合并查询来源，因此充分性评估知道一个页面覆盖了哪些研究维度。

报告中的“研究过程”表只展示可审计的决策摘要，不暴露或伪造隐藏思维链。

## 五、安装与运行

```powershell
cd D:\code\pycharm\DeepSearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

离线验收：

```powershell
deepsearch --mock ask "Python 3.13 什么时候发布"
deepsearch --mock ask "对比 2025 年主流大模型的推理能力和成本"
```

启动 Web：

```powershell
deepsearch web
# 或
python -m streamlit run streamlit_app.py
```

默认访问 `http://localhost:8501`。

## 六、Web 使用

| 页面 | 作用 |
|---|---|
| 首页 | 说明价值、选择研究强度、快速发起问题 |
| 研究 | 配置领域、信息类型、时间窗口、格式、篇幅、读者和章节；查看实时循环和追问 |
| 证据 | 查看五维评分、来源质量、证据缺口、下一步查询和轮次轨迹 |
| 报告 | 检索、预览、下载 Markdown/Text/JSON |
| 设置 | 引擎预算、缓存、自主任务、模型连接 |

自主任务表单使用 Streamlit 原生 form 批量提交，避免填写一个字段就触发保存；Session State 只保存单个浏览器会话的研究上下文，任务本身持久化到配置文件。

## 七、自主任务命令示例

每天上午 10 点生成 AI 行业报告：

```powershell
deepsearch tasks add "AI 行业日报" "总结大模型与 AI Agent 领域的重要变化" `
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
  --profile 深度
```

管理与执行：

```powershell
deepsearch tasks list
deepsearch tasks disable "AI 行业日报"
deepsearch tasks enable "AI 行业日报"
deepsearch --mock tasks run "AI 行业日报"
deepsearch tasks remove "AI 行业日报"
deepsearch schedule --once
deepsearch schedule --poll 30
```

Windows 长期运行推荐让任务计划程序每分钟或每五分钟执行一次 `deepsearch schedule --once`。内置 `--poll` 适合本地临时运行。

## 八、模型与降级

```powershell
$env:DEEPSEARCH_API_KEY = "your-key"
$env:DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"
$env:DEEPSEARCH_MODEL = "gpt-4o-mini"
```

没有 Key：使用结论优先确定性报告器。错误 Key、超时或服务故障：模型调用重试一次，然后自动降级。搜索与引用证据不会丢失。

Mock 报告会明确说明它只能验证工作流，不能当作实时事实报告；这是可靠性边界，不是缺陷隐藏。

## 九、测试与验收

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前结果：`Ran 24 tests`，全部通过。覆盖：

- 事实问题一轮停止且摘要直接给出答案；
- 复杂问题至少两轮并产生补充查询；
- 研究要求进入所有初始查询；
- 时效追问重新搜索；
- 空在线源降级 Mock；
- 并发抓取、控制字符清理、缓存和稳定排序；
- 引用编号和论点文本支持校验；
- Markdown/Text/JSON 导出；
- 每日、每周和单次任务幂等性；
- 完整任务规格持久化；
- Streamlit 首页和自主任务表单；
- CLI/Web 仓库根和启动入口。

根 `reports/` 只保留 2.1 验收报告；旧测试报告已移动到 `reports/archive/pre-2.1/`，可恢复但不会出现在默认报告列表。

## 十、重难点与解决方案

### 1. “有搜索”不等于“有总结”

解决方式不是简单改标题，而是把报告结构改为结论优先，并增加摘要内容回归断言，确保事实答案真的出现在来源表之前。

### 2. 自主性不能只靠多跑几轮

每轮必须有输入状态、证据增量、缺口、充分性决定和下一步查询。`RoundTrace` 记录这些字段，Web、报告和测试共享同一结构。

### 3. 无 LLM 时如何有可读报告

确定性报告器只做证据允许的综合：抽取可引用结论、去掉 Mock 元数据、按研究维度分配来源、给出风险和下一步。它不会假装拥有不存在的语义理解；复杂语言综合仍推荐配置模型。

### 4. 文件格式不能绕过质量门

系统先生成并验证规范 Markdown，再由存储适配器导出 Text/JSON。这样任何格式都来自同一份通过引用校验的内容。

### 5. 定时任务如何避免重复和连锁失败

任务以日期计划实例作为幂等键，成功后才更新状态；一次任务失败只记录日志并继续下一项。配置使用临时文件替换，降低中断损坏风险。

## 十一、专家评估

| 方向 | 评分 | 说明 |
|---|---:|---|
| Agent 反馈循环 | 8.7/10 | 有证据缺口、查询调整、来源预算和结构化轨迹 |
| 最终报告交付 | 8.3/10 | 已从链接目录升级为结论优先；无模型语义综合仍有限 |
| 追问能力 | 8.2/10 | 能区分上下文复用和新时效补搜，尚无长期记忆 |
| 自主任务 | 8.6/10 | 周期与完整研究/交付要求一体化，适合个人工具 |
| 架构清晰度 | 8.8/10 | Brief、Task、Service 和 Adapter 边界明确 |
| 工程可靠性 | 8.8/10 | 离线可跑、降级、缓存、原子保存、24 项测试 |
| 生产可运营性 | 6.8/10 | 仍缺任务队列、取消恢复、集中观测和通知渠道 |

## 十二、面试官可能追问

1. 你如何证明系统是 Agent，而不是固定 Pipeline？
2. “信息充分”如何定义，为什么每个维度要求交叉证据？
3. 到达最大轮次仍不充分时，报告应该怎样表达？
4. 为什么摘要原先会退化成来源目录，你如何用测试防止回归？
5. 追问在什么条件下复用证据，什么条件下补搜？
6. `ResearchBrief` 为什么属于领域输入，而不是 UI DTO？
7. 为什么先验证 Markdown，再导出 JSON/Text？
8. 每日、每周和单次任务的幂等键如何设计？
9. 调度中某一任务失败，为什么不能写成功状态？
10. 无 LLM 模式的能力边界是什么？
11. 来源质量分为什么不能等同于事实真实性？
12. 如果升级成生产系统，如何加入后台队列、取消和断点恢复？

回答主线应围绕：状态如何变化、证据如何约束结论、失败如何降级、任务如何幂等、扩展如何隔离。

## 十三、8 分钟演示脚本

1. 0:00–0:40：首页说明“从问题交付报告，而不是返回链接”；
2. 0:40–1:30：研究页配置领域、信息类型、篇幅和章节；
3. 1:30–3:20：Mock 深度模式执行复杂对比，展示第一轮不足和第二轮补搜；
4. 3:20–4:20：打开报告，先看摘要/关键结论，再看来源表；
5. 4:20–5:10：证据页展示证据缺口、下一查询、停止理由和质量评分；
6. 5:10–6:00：追问“最近有什么更新”，展示强制补搜；
7. 6:00–7:10：设置页创建每天 10 点、1800 字、新闻+公告的自主任务；
8. 7:10–8:00：展示 24 项测试和三条生产路线：claim-evidence graph、后台任务、评测集。

## 十四、当前边界与下一步

- 免费搜索和网页结构不稳定，在线质量受网络影响；
- 标准库读取器不支持 JavaScript 渲染、PDF/OCR 和复杂表格；
- 当前引用支持校验是词项对应，不是自然语言蕴含证明；
- 无 LLM 的跨来源语义综合仍偏保守，优先保证不编造；
- 内置调度器是单进程轮询，不支持后台队列、取消和断点恢复；
- 未实现跨报告知识库、通知渠道、多租户和成本配额。

后续优先级：

1. claim-evidence graph 与逐主张蕴含/时效验证；
2. BM25 + embedding + cross-encoder 混合检索；
3. 后台任务、事件流、取消、重试和断点恢复；
4. PDF/表格/浏览器阅读器；
5. 可重复研究评测集；
6. 跨报告时间版本和变化检测；
7. 邮件、Webhook 或桌面通知。

## 十五、关键文件

- `deepsearch/domain/models.py`：ResearchBrief、ReportSpecification 和研究结果模型；
- `deepsearch/application/service.py`：迭代循环与追问策略；
- `deepsearch/application/planner.py`：问题拆解、充分性与查询调整；
- `deepsearch/infrastructure/reporting.py`：结论优先报告；
- `deepsearch/infrastructure/storage.py`：多格式导出；
- `deepsearch/scheduling/topics.py`：自主任务和调度；
- `app_pages/research.py`：研究与报告要求；
- `app_pages/settings.py`：自主任务工作台；
- `docs/ARCHITECTURE.md`：完整架构；
- `docs/REQUIREMENTS_TRACEABILITY.md`：需求—代码—测试追踪。
