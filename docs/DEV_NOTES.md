# DeepSearch 2.1 开发笔记

> 面向维护者 · 最后校准：2026-08-28

## 1. 当前实现基线

当前版本是 2.1.0。研究主循环位于 `deepsearch/application/service.py`，`deepsearch/bootstrap.py` 负责装配，`DeepSearchAgent` 只是兼容门面。2.1 在 2.0 分层基础上重点修正“检索完成但报告没有真正总结”的交付缺口，并把简单主题定时器升级为自主研究任务。

运行时核心仍只依赖 Python 标准库；Streamlit 是 `ui` 可选依赖，pytest/pytest-cov/Streamlit 被归入 `dev` extra。这样 CLI 和 Mock 模式在最小环境中可运行，同时 Web 页面和测试可以按需安装。

## 2. 关键技术决策

### 2.1 轻量 Clean Architecture

早期 `DeepSearchAgent` 同时创建搜索、抓取、缓存、规划、报告和存储，导致研究规则与技术细节混在一起。2.0 将其拆为：

- `domain`：跨层模型和稳定排序规则；
- `application`：研究用例、端口、规划、验证、评分；
- `infrastructure`：HTTP、文件、缓存、LLM 和搜索适配器；
- `presentation`：CLI 与 Streamlit；
- `scheduling`：主题和每日调度；
- `bootstrap.py`：唯一组合根。

只在组合根 `new` 具体适配器。应用层使用 Protocol 端口，因此测试可以直接注入 Mock provider、临时存储和 Fake 组件。

### 2.2 自己实现反馈循环

没有引入 LangChain/LlamaIndex。项目要展示的核心正是状态如何变化、何时停止、如何降级和如何验证；把这些隐藏在重型框架内部会降低可解释性和测试确定性。未来若采用工作流引擎，应让它承载后台状态与恢复，不应取代证据模型和质量门。

### 2.3 确定性能力作为可靠下限

分类、拆解、停止、交叉验证、引用校验和质量评分均不要求 LLM。配置模型后，LLM 只增强报告表达；调用失败有一次有界重试，随后由报告器退回确定性 Markdown。

Mock 搜索不是伪装的实时数据：来源 provider、URL、报告状态和 Web 标签均明确标识 Mock。它用于验证 Agent 行为和演示可靠性，不用于证明最新事实。

### 2.4 标准库优先

`urllib` 负责 DuckDuckGo、Wikipedia、网页正文和 OpenAI 兼容请求；`ThreadPoolExecutor` 负责搜索和抓取并发；`argparse` 负责 CLI。优势是安装简单、离线测试稳定；代价是 JavaScript 页面、PDF/OCR 和复杂正文抽取能力有限。替换点已被限制在 infrastructure adapter 内。

### 2.5 研究要求是领域输入，不是 UI 装饰

`ResearchBrief` 保存研究目标、知识领域、信息类型和时间范围；`ReportSpecification` 保存格式、目标篇幅、读者、语言、章节和自定义要求。规划器把范围提示加入初始查询和补充查询，报告器按规格控制结构与证据数量，存储器按格式导出。自主任务复用完全相同的对象，避免 Web、CLI 和调度器形成三套行为。

## 3. 研究循环

`ResearchService.research()` 的顺序是：

```text
plan
  → [search → deduplicate → rank → fetch → evaluate → adjust?] × budget
  → organize evidence/conflicts
  → generate
  → validate → repair → deterministic fallback if needed
  → save
  → evaluate scorecard
  → ResearchResult
```

### 3.1 为什么先发 PLAN

规划进度回调发生在任何网络或模型调用前，CLI/Web 能立即呈现反馈。它既满足等待体验，也便于从日志判断卡顿发生在哪一阶段。

### 3.2 停止条件

规划器返回 `SufficiencyDecision(sufficient, reason, missing, next_queries)`。循环按以下约束收敛：

1. 简单事实有一个可用来源后立即停止；
2. 复杂问题在 `minimum_rounds=2` 前不提前停止；
3. 来源数量和子问题覆盖足够时停止；
4. 连续两轮没有新增来源时停止；
5. 达到来源或轮次预算时停止。

下一轮查询围绕缺失维度生成。第一轮偏官方信息和数据，后续轮次补独立评测、局限、争议等角度。每轮决定写入 `RoundTrace`，不要只把原因放在不可测试的日志字符串里。

### 3.3 并发与确定性

在线任务是“搜索源 × 查询”的笛卡尔积。future 可乱序完成，但 `_search_queries()` 按任务创建顺序重组结果；排序器再使用 URL/标题作为稳定次级键。因此并发不会让相同 Mock 输入随机改变报告结构。

单个搜索或抓取 future 的异常被隔离。正文失败时保留摘要和错误状态，整次研究继续进行。

## 4. 检索、排序与缓存

### 4.1 多路检索

在线模式同时注入 DuckDuckGo HTML 和 MediaWiki API。所有在线源为空时才启用 `MockSearch`；显式 `mode=mock` 则从开始就不访问网络。

### 4.2 去重和排序

- URL 去掉查询串、片段和尾部斜杠后用于跨 provider 去重；
- 相关性、域名质量、摘要/正文完整度共同产生候选分；
- `per_domain_limit` 控制候选阶段的站点集中度；
- 来源进入结果后重新编号，报告与证据页共享同一 `source_id`。

### 4.3 两级磁盘缓存

`ResearchCache` 分别缓存在线查询结果和成功抓取正文，默认 TTL 6 小时。键使用 SHA-256；写入采用同目录临时文件替换。失败请求和 Mock 内容不缓存，避免把瞬时故障或演示数据当成在线事实复用。

缓存命中/未命中以研究前后差值计入 `ResearchMetrics`，防止复用同一 Agent 时显示累计值。

## 5. 证据、引用和评分

### 5.0 结论优先报告

2.0 的确定性报告器只在摘要里陈述轮次和来源数，详细部分几乎原样列出摘要，导致最终产物像证据目录。2.1 改为：直接答案/综合发现 → 关键结论 → 分维度分析 → 建议 → 局限 → 逐轮研究过程 → 全部来源。Mock 元数据不会再冒充核心结论；无 LLM 时使用证据句综合，有 LLM 时 Prompt 也明确禁止只列链接。

### 5.1 交叉验证

`CrossVerifier` 对来源前部文本切句并构造保守主题键。多来源支持同类表述时标为多源；单来源标为待验证；同组出现多个数字时提示“需核对口径”，不自动裁决真伪。

### 5.2 答案校验

`AnswerValidator` 检查：

- 所有 `[n]` 都属于当前来源集合；
- 存在摘要、详细分析和完整来源表；
- 每个计划子问题有对应标题；
- 详细分析中的带引用条目与引用正文至少具有最低词项重叠。

“词项对应”不能写成“语义验证”。它只能拦截明显张冠李戴，不能证明蕴含、时效和统计口径。若轻量修复后仍失败，服务使用确定性抽取式报告重建。

### 5.3 五维评分

`ResearchQualityEvaluator` 根据引用完整性、问题覆盖、来源多样性、来源质量和检索健康度产生 `ResearchScorecard`。评分用于解释风险和给出改进建议，不是事实真实性概率。

## 6. 配置与路径

配置优先级是：默认值 → JSON → 环境变量 → 调用参数。`reports_dir` 和 `cache_dir` 的相对路径以配置文件目录解析。

仓库级入口的根目录必须使用正确层级：

- `presentation/cli.py`：`Path(__file__).resolve().parents[2]`；
- `presentation/web/support.py`：`Path(__file__).resolve().parents[3]`；
- `presentation/web/styles.py`：`Path(__file__).resolve().parents[3]`。

2026-08-28 的全项目审计修复了 CLI/Web 根路径少上溯一级的问题。此前 `deepsearch web` 会寻找不存在的包内入口，Web 报告会落入 `deepsearch/presentation/reports/`。现有误置报告已迁移到根 `reports/`，并增加路径一致性回归测试。

`save_settings()` 使用锁和临时文件替换；无论内存是否含密钥，落盘时都会把 `llm.api_key` 清空。

## 7. Streamlit 决策

- 根入口使用 `st.navigation(position="top")` 与 `app_pages/`，没有旧式 `pages/` 自动发现；
- 页面保持直接脚本，只处理输入、布局和展示，共享适配逻辑位于 `presentation/web/`；
- Session State 在入口集中初始化，保存当前会话消息、最近结果、进度和研究模式；
- 昂贵研究由提交动作触发，不做 UI 级结果缓存；报告文件列表可以使用有 TTL/容量上限的 `st.cache_data`；
- 快速/均衡/深度通过配置副本实现，不修改用户保存值；
- 优先使用原生 Streamlit 组件和 Material Symbols；主题在 `.streamlit/config.toml`，品牌 CSS 集中在 `assets/app.css`；
- 不使用已弃用的 `use_container_width` 或 `st.components.v1`；
- secrets 从环境变量或 `.streamlit/secrets.toml` 读取，普通设置表单不保存 Key。

## 8. 调度与追问

追问先比较新问题与上一报告的词项覆盖。重叠足够时复用旧来源并生成独立报告；不足时把原问题和追问合并后重新进入完整研究循环。当前不维护长期对话记忆。

`ResearchTaskScheduler` 是单进程轻量调度器，支持 daily/weekly/once。状态文件位于配置旁，记录任务最近成功的计划实例；执行失败不写成功状态，也不阻断其他任务。旧 `TopicManager`/`TopicScheduler` 保留为兼容别名，旧 `name/query/time` 配置读取时自动迁移。

## 9. 测试策略

当前 24 项测试全部离线可执行，新增覆盖：

- 事实问题一轮停止与复杂问题多轮反馈；
- 在线空结果降级；
- 搜索/正文缓存、排序和域名多样性；
- Mock 并发抓取和控制字符清理；
- 无效引用与缺少文本支持的论点；
- 质量评分和研究模式配置隔离；
- 结论优先摘要确实包含直接答案；
- 追问需要最新证据时重新进入搜索循环；
- 研究领域、信息类型和时间窗口进入每条初始查询；
- Markdown/Text/JSON 三种导出；
- 旧每日主题兼容、每周任务选择日与幂等性、完整任务规格持久化；
- Streamlit 自主任务表单渲染；
- Streamlit 默认页面渲染；
- CLI/Web 仓库根和启动入口一致性。

真实网络不进入默认回归套件，因为免费搜索的可用性和内容会随时间变化。可新增非阻断契约测试，但不能替代 Mock 单元测试。

## 10. 维护约定

- 重要注释解释“为什么、边界、失败策略”，避免逐行翻译代码；
- 新基础设施先实现 `application/ports.py` 的最小协议，再在组合根注册；
- 不让页面直接导入 `infrastructure.search`、缓存或文件存储实现；
- 新的进度阶段应同步更新 CLI 标签、Web 展示和用户手册；
- 修改默认值时同步 `Settings`、`config.example.json`、README 配置表和测试；
- 修改目录或命令时同步 `README`、架构文档、用户手册、项目总结和需求追踪；
- 生成的 `reports/*.md` 是研究产物，不作为手写文档批量改写。

## 11. 演进历史与下一步

- **V1**：单体 facade 完成问、搜、抓、答、保存和调度。
- **V1.1**：多路检索、缓存、稳定排序、结构化 trace、词项引用校验。
- **V2.0**：Clean Architecture、五维质量评分、三档预算、产品化多页面 Streamlit 工作台。
- **V2.1**：结论优先报告、结构化 ResearchBrief、证据感知追问、多格式导出和自主研究任务。

下一步按价值排序：

1. claim-evidence graph：每条主张绑定证据片段、来源身份和时间；
2. BM25 + embedding + cross-encoder 的混合召回和重排；
3. HTML 主文抽取、PDF/表格和可选浏览器渲染；
4. 后台任务、取消、重试、断点恢复和事件流；
5. 离线评测集，联合衡量事实、覆盖、引用、来源、时延和成本；
6. 跨报告索引与时间版本，识别结论变化。
