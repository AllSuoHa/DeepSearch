# DeepSearch 2.2 开发与维护

> 面向维护者 · 最后校准：2026-09-07

## 1. 当前基线

- 包版本：`2.2.0`
- Python：`>=3.11`
- Web：Streamlit `>=1.62,<2`
- 运行依赖：`trafilatura>=2,<3`、`pypdf>=6,<7`
- 默认回归：69 项离线优先测试
- 公共门面：`DeepSearchAgent.run()`；`research()`、`follow_up()` 保持兼容
- 当前主线：个人对话工作台 + 独立搜索 + 证据型研究 + 本地资产管理

原始需求中“搜、读、验证、写报告、追问、定时收集”的核心目标仍然有效，但早期“只有研究、CLI 为主”的产品描述已经不再代表当前实现。当前事实以根 README、用户手册和架构说明为准。

## 2. 关键技术决策

### 2.1 搜索和研究是两种产品流程

最初所有问题都进入研究报告，导致找资源、官网或播放平台时返回大量“摘要、论证、下一步”文字。2.2 把流程拆开：

- `SearchService` 直接返回链接、分类和风险提示，不调用模型；
- `ResearchService` 负责多轮检索、正文、证据、报告和质量门；
- `IntentClassifier` 只在 `AUTO` 时路由，歧义默认走更轻的搜索；
- `AgentRunResult` 保证一次运行只携带一种实际结果。

这样做不是给同一报告换模板，而是把用户意图、成本和交付结构分开。

### 2.2 轻量 Clean Architecture

具体搜索源、抓取、模型、缓存和文件存储只在 `bootstrap.py` 创建。应用层通过 `application/ports.py` 中的 Protocol 依赖能力，不依赖 `urllib`、Streamlit 或路径。

收益：

- 单元测试可以注入 fixture provider、fake LLM 和临时存储；
- 新增搜索源不改研究主循环；
- CLI、Web 和调度器复用完全相同的业务行为；
- 失败语义和质量门不会散落在页面代码中。

没有引入 LangChain/LlamaIndex。这个项目需要显式展示状态、停止条件、证据边界和失败策略；重型框架会隐藏这些核心学习与评审价值。未来若引入工作流引擎，应只承担后台状态和恢复，不应替代领域模型与质量门。

### 2.3 研究规划保持确定性

规划、问题拆解、查询调整和充分性判断由 `ResearchPlanner` 完成，不依赖模型。模型只负责证据综合和报告写作。

停止逻辑：

1. 简单事实有一个可用来源即可停止；
2. 复杂问题至少完成默认最低轮次；
3. 每个维度有足够证据且来源域具有多样性时停止；
4. 连续两轮无新增来源时停止；
5. 达到轮次或来源预算时强制停止。

查询调整围绕实际缺失维度；第一轮倾向官方文档和原始数据，后续倾向独立评测、局限、争议和反方观点。每轮决定写入 `RoundTrace`，不能只存在日志文字里。

### 2.4 三阶段报告 + 确定性质量门

只调用一次模型容易得到“引用堆砌、结论空泛”的报告。当前同一个模型分三种角色连续工作：

1. 证据编辑器建立直接答案、主张—来源关系、冲突和缺口；
2. 研究作者按读者、篇幅和章节写结论优先初稿；
3. 独立终稿编辑器检查直接性、重复、支持度和排版后重写。

模型输出之后仍由 `AnswerValidator` 做确定性检查。首次失败只允许一次针对性修订；第二次失败抛出 `ReportQualityError`，绝不落盘。隐性思维链不保存、不展示，只展示来源、检索轨迹、证据缺口和审校问题。

来源清单不是生成式内容：`MarkdownReporter` 会从当前 `Source` 集合确定性补齐它，避免模型漏掉一个标题就丢弃整份有用报告。`simple_fact` 只强制结论、有效引用和来源清单；比较、探索和深度研究继续要求分析章节。校验问题必须指出具体缺失章节，便于唯一一次修订准确处理。

### 2.5 进度实时更新，终稿校验后展示

Web 层的 `RunProgressTracker` 是线程安全的展示状态：后台线程只执行 Agent 并写入 phase/message，不能读取 `st.session_state` 或调用任何 Streamlit API；页面线程通过 `wait_for_background_result()` 读取不可变快照并更新 `st.status(type="compact")`。轮询对齐任务启动后的整数秒，阶段事件又可提前唤醒，因此计时稳定每秒更新，步骤变化也能立即显示。

折叠区标题为“思考步骤与说明”，保留准备、规划、搜索、抓取、充分性判断、补搜、交叉验证、证据整理、初稿、审校、验证、评分和保存等高层阶段。每个连续阶段只保留最新说明并计算独立持续时间；这是可审计运行摘要，不是隐性思维链。详细诊断仍由 `RoundTrace` 和轮转日志承担。

首页停止按钮由显式 `st.empty` 占位符承载。任务结束时先把 `agent_run_state` 设为 idle，再清空占位符并渲染结果，避免完成结果与旧“停止生成”按钮同时出现。协作式取消通过 tracker 的线程事件传到下一阶段回调；阻塞中的 HTTP 调用仍由各自超时控制。

回顶/到底没有原生 Streamlit 命令，因此单独封装在 `presentation/web/scroll_controls.py` 的内联 Custom Components v2 组件中。组件使用 Shadow DOM 隔离样式、Streamlit 主题变量和固定 SVG 图标，只查询受信任的页面滚动容器，不接收用户或模型内容；点击只调用浏览器滚动，不向 Python 回传事件或触发 rerun。不同 Streamlit 版本的实际滚动节点并不固定，组件会从 `stAppScrollToBottomContainer`、`stMain`、应用容器和浏览器根节点中动态选择“可滚动且滚动范围最大”的节点，并用捕获阶段滚动事件、`ResizeObserver` 与 `MutationObserver` 在内容增长时重新计算。按钮默认隐藏，仅主内容区 `wheel` 活动时按方向淡入，停止约 1.4 秒后淡出，并同步 ARIA/tab 顺序。按钮点击采用可取消的 `requestAnimationFrame` 缓动；新的滚轮事件先取消缓动但不阻止浏览器默认滚动，避免底部反向滚动被原生 `behavior: smooth` 动画吞掉。禁止改回 `components.v1`、iframe 或跨实例的全局 CSS/事件选择器。

研究终稿不做 token 级流式输出。三阶段模型调用和确定性校验完成前，正文都可能被重写或拒绝；提前输出会暴露未审校内容，并让失败语义和引用状态不一致。页面只实时更新任务计时与步骤，最终 Markdown 在通过质量门后一次性渲染。CLI 同样打印完整结果，不模拟流式效果。

### 2.6 在线失败不降级为 Mock

Mock 是显式、离线、可重复的演示能力，不是网络失败后的备用事实。`runtime_mode=online` 时：

- 组合根不会创建 Mock provider；
- 搜索和研究边界会再次排除 Mock；
- 全部搜索失败抛出明确错误；
- Mock 不参与多源验证和质量评分。

这条规则优先于“尽量给用户一个结果”，因为伪装成在线事实的演示数据风险更高。

### 2.7 标准库优先，第三方依赖隔离

- `urllib`：搜索 API、网页、模型和投递 HTTP；
- `ThreadPoolExecutor`：搜索和抓取并发；
- `argparse`：CLI；
- `trafilatura`：正文主体提取，仅存在于 fetcher；
- `pypdf`：公开 PDF 文本读取，仅存在于 fetcher；
- Streamlit：展示层，不进入领域和应用层。

JavaScript 页面、登录态浏览器和 OCR 没有用隐式复杂度“假装支持”，而是透明降级为摘要或明确失败。

### 2.8 本地文件优先，但所有可变写入都要有边界

- 配置、会话、快捷输入、缓存、调度状态和 outbox 使用临时文件替换；
- 配置保存使用进程内锁，且强制移除所有密钥；
- 会话消息通过字段白名单，阻止秘密和完整网页正文意外落盘；
- 资料库删除先移动到项目回收站，恢复和永久删除都重复校验路径；
- 投递重试保存路径与内容哈希，发送前重新读取并复核，不复制正文。

当前没有引入数据库，因为单用户、单进程的数据规模和查询方式仍适合文件系统。

## 3. 历史问题与解决原则

### 3.1 页面像宣传页，不像日常工具

2.2 将首页改为对话式工作台：导航、最近会话、固定输入区、工作模式、快捷输入和结果操作围绕日常任务组织。展示层优先使用原生 Streamlit 控件，CSS 只维护阅读宽度、间距、底部输入和少量视觉 token。

### 3.2 资源搜索生成冗长研究报告

根因是所有请求共享研究管线。解决方案是独立的 `SearchService` 和结构化 `SearchResponse`，不是修改报告 Prompt。影视查询额外补充地区和官方播放平台关键词，并过滤明显盗版/下载风险。

### 3.3 报告有论据但没有直接结论

根因是模型同时承担证据整理、写作和自评，角色冲突且缺乏确定性门禁。解决方案是三阶段模型调用、结论优先章节约束、独立审校和一次修订上限。

### 3.4 删除报告后选择器仍显示旧项

文件已经移动，但 Streamlit 固定 widget key 仍保留旧显示状态。当前让现有报告集合参与选择器身份，并在移动完成后清理选择状态和缓存。不要只依赖 `st.rerun()`；组件选项集发生变化时必须同步 widget 状态。

### 3.5 模型 404 与超时混在笼统错误里

模型客户端现在区分确定性 HTTP 配置错误和瞬时错误：

- 400/401/403/404 直接失败并给出参数、Key、权限、Base URL/模型提示；
- 408/409/429/5xx、网络和超时最多重试一次；
- 默认模型单次超时从 30 秒提高到 180 秒，并可在设置、JSON、环境变量或 Secrets 中调整；
- 报告器保留失败阶段，例如“证据整理”或“独立审校”。

网页抓取的 403/521 与模型失败不是同一类问题。前者通常只让单个来源降级到摘要，不应掩盖真正导致研究终止的模型错误。

### 3.6 CLI/Web 相对路径曾产生第二套数据

仓库入口路径必须按文件层级计算：

- `presentation/cli.py`：`Path(__file__).resolve().parents[2]`
- `presentation/web/support.py`：`Path(__file__).resolve().parents[3]`
- `presentation/web/styles.py`：`Path(__file__).resolve().parents[3]`
- `presentation/web/scroll_controls.py`：不解析项目路径，只注册受信任的内联 CCv2 资源

路径改动必须同时验证 CLI Web 子命令、报告目录、配置目录和静态资源。

## 4. 功能—实现—测试追踪

| 能力 | 主要实现 | 主要测试 |
|---|---|---|
| 智能判断 / 搜索 / 研究 | `application/intent.py`, `bootstrap.py` | `test_search_modes.py`, `test_agent.py` |
| 链接优先搜索、风险过滤 | `application/search_service.py` | `test_search_modes.py` |
| 多轮研究与停止条件 | `application/planner.py`, `application/service.py` | `test_planner.py`, `test_agent.py` |
| 排序、域名多样性和缓存 | `domain/ranking.py`, `infrastructure/cache.py` | `test_cache_ranking.py` |
| HTML/PDF 正文读取 | `infrastructure/fetcher.py` | `test_fetcher.py` |
| 三阶段报告与失败阶段 | `infrastructure/reporting.py`, `application/prompts.py` | `test_report_quality.py` |
| 模型超时、错误和密钥保护 | `infrastructure/llm.py`, `infrastructure/config.py` | `test_llm.py` |
| 引用、支持度、重复与噪声 | `application/verification.py` | `test_verification.py`, `test_report_quality.py` |
| 五维质量评分 | `application/evaluation.py` | `test_evaluation.py` |
| 多格式报告和搜索快照 | `infrastructure/storage.py` | `test_storage.py`, `test_conversations.py` |
| 会话、快捷输入和历史恢复 | `infrastructure/storage.py`, `presentation/web/support.py` | `test_conversations.py` |
| 资料库推送与安全回收站 | `app_pages/history.py`, `app_pages/trash.py`, `presentation/web/delivery.py`, `infrastructure/storage.py` | `test_customer_service.py`, `test_conversations.py`, `test_streamlit_app.py` |
| CustomerService v2 异步入库、状态轮询与幂等重投 | `infrastructure/customer_service.py` | `test_customer_service.py` |
| daily/weekly/once 自动任务 | `scheduling/topics.py` | `test_topics.py` |
| Streamlit 页面合同与入口路径 | `streamlit_app.py`, `app_pages/`, `assets/app.css` | `test_streamlit_app.py` |

## 5. 测试策略

标准命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

当前测试分布：

| 文件 | 数量 | 重点 |
|---|---:|---|
| `test_agent.py` | 5 | 研究循环、追问、配置隔离 |
| `test_cache_ranking.py` | 2 | 缓存、稳定排序和域名配额 |
| `test_conversations.py` | 7 | 会话、快照、快捷输入、回收站 |
| `test_customer_service.py` | 9 | v2 异步状态、幂等、重试、敏感信息和 outbox |
| `test_evaluation.py` | 2 | 质量维度和 Mock 排除 |
| `test_fetcher.py` | 4 | HTML、PDF、并发与控制字符 |
| `test_llm.py` | 4 | 超时、HTTP 错误、重试和配置安全 |
| `test_planner.py` | 3 | 分类、最低轮次和查询范围 |
| `test_report_quality.py` | 4 | 三阶段、阶段错误和失败不落盘 |
| `test_search_modes.py` | 5 | 路由、媒体结果、风险和在线失败 |
| `test_storage.py` | 1 | 多格式导出 |
| `test_streamlit_app.py` | 9 | 页面、控件、状态和视觉合同 |
| `test_topics.py` | 6 | 任务迁移、周期、幂等和失败隔离 |
| `test_verification.py` | 3 | 引用、支持度、重复和噪声 |

总计 69 项。真实网络和真实模型不进入默认回归，因为内容、频率限制和可用性不稳定。可以增加非阻断契约测试，但不能替代离线单元测试。

## 6. 代码注释约定

- 模块 docstring 解释职责和层级边界。
- 公共类/方法 docstring 解释输入、输出和重要不变量。
- 行内注释只解释“为什么”：安全边界、顺序要求、兼容策略、降级和确定性。
- 不逐行翻译代码，不记录容易过期的实现数量，不在注释中保留已完成 TODO。
- 并发重组、原子替换、Mock 排除、质量门和路径校验必须保留原因说明。
- 错误消息是用户界面的一部分；修改错误分类时同步测试和用户手册。

## 7. 维护检查清单

### 新增搜索源

1. 实现 `SearchProvider.name` 和 `search()`。
2. 映射为统一 `SearchResult`，只返回合法 URL。
3. 在 `build_search_providers()` 或学术源列表中注册。
4. 明确超时、异常和地区/语言上下文。
5. 增加 provider 解析测试和在线失败不降级 Mock 的回归。

### 修改研究流程

1. 保持规划进度发生在网络请求之前。
2. 保持 `RoundTrace` 能解释每轮为什么继续或停止。
3. 保持不通过质量门不保存。
4. 同步 Web/CLI 进度标签、架构文档和用户手册。
5. 用 fake provider/LLM 做离线确定性测试。

### 修改配置

同时更新：

- `Settings`/嵌套 settings 默认值；
- `load_settings()`、`save_settings()`；
- `config.example.json`；
- `.streamlit/secrets.toml.example`（仅密钥或安全覆盖项）；
- 设置页、根 README、用户手册和测试。

密钥不得进入 `config.json`、会话、日志、报告或测试快照。

### 修改数据目录或删除逻辑

1. 解析绝对路径并验证允许根目录。
2. 避免覆盖已有文件。
3. 可恢复操作优先于永久删除。
4. 同步会话中的资产引用和 Streamlit widget 状态。
5. 覆盖损坏元数据、路径穿越、同名冲突和重复操作。

### 修改文档

只维护四个入口：根 README、`docs/USER_GUIDE.md`、`docs/ARCHITECTURE.md`、`docs/DEV_NOTES.md`，以及本索引。不要为单次版本另建“项目总结”“演示手册”或重复需求表。生成报告属于用户数据，不作为项目文档编辑。

## 8. 当前妥协与技术债

- 意图识别、规划和充分性判断以关键词/词项为主，成本低且可测，但复杂语义准确率有限。
- 交叉验证是保守主题聚合，不能证明两个来源真正独立，也不能自动裁决冲突。
- 引用支持使用最低词项重叠，不是自然语言蕴含模型。
- 搜索模式只使用搜索摘要，不做正文级答案综合。
- `urllib` 同步调用配合线程池足够单机使用，但不支持任务级超时、取消传播和断点恢复。
- Streamlit 停止是会话级协作式中断，阻塞中的 HTTP 调用仍受各自超时控制。
- 本地 JSON 没有跨进程事务；当前锁主要保护同进程配置/缓存写入。
- 资料库搜索按文件正文扫描，资产规模很大时需要索引。
- 当前没有 OCR、JavaScript 渲染、登录态抓取或跨报告知识检索。

## 9. 演进记录

- **V1**：问、搜、抓、答、保存和简单调度的单体门面。
- **V1.1**：多路检索、缓存、稳定排序、结构化轨迹和基础引用校验。
- **V2.0**：分层架构、五维质量评分、研究预算和多页面 Streamlit。
- **V2.1**：结论优先、`ResearchBrief`、多格式导出、证据感知追问和完整自动任务。
- **V2.2**：对话式个人工作台、搜索/研究分流、学术源、三阶段审校、本地会话、资料库回收站和统一投递。
- **V2.2 维护更新**：删除后选择器状态同步；搜索卡片历史恢复；模型超时可配置；HTTP/超时错误可操作化。

早期 `pro.md`、2.1 项目总结和单独需求追踪表已在 2026-09-07 合并到当前文档体系并删除。Git 历史保留它们的原始内容。

## 10. 下一步优先级

1. 建立 claim-evidence graph，让每条主张绑定具体证据片段、来源身份和时间。
2. 建立可重复离线评测集，联合衡量事实、覆盖、引用、来源、时延和成本。
3. 增加持久后台任务状态机，支持取消、重试、断点恢复和事件流。
4. 增加 BM25 + embedding + cross-encoder 的可选混合召回与重排。
5. 增加 OCR、表格结构化和可选浏览器渲染，但仍不绕过访问控制。
6. 建立跨报告索引与时间版本，识别结论变化并形成个人知识积累。
