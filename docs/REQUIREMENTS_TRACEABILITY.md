# DeepSearch 需求追踪矩阵

> 产品版本：2.1.0 · 验收日期：2026-08-28 · 回归结果：24/24 通过

## 1. 状态定义

- **已实现**：当前代码存在可运行路径；
- **自动验证**：有明确回归测试覆盖主要行为；
- **手工验证**：功能可运行，但仍建议按演示脚本端到端检查；
- **边界**：说明当前实现没有声称解决的生产级问题。

## 2. F1–F16 追踪

| 编号 | 状态 | 当前实现 | 代码证据 | 验证证据 |
|---|---|---|---|---|
| F1 问题理解与策略制定 | 已实现 | 四类问题、子问题、初始查询、最低轮次 | `application/planner.py`, `domain/models.py` | `test_planner.py` 2 项 |
| F2 多路搜索与网页抓取 | 已实现 | DuckDuckGo + Wikipedia 并发；正文并发与摘要降级 | `application/service.py`, `infrastructure/search/`, `infrastructure/fetcher.py` | `test_fetcher.py`, `test_agent.py` |
| F3 迭代式搜索 | 已实现 | 每轮充分性判断、缺失维度、查询调整、轮次/来源/停滞预算 | `application/service.py`, `application/planner.py` | 复杂问题至少两轮测试 |
| F4 内容整理与交叉验证 | 已实现 | URL 去重、证据聚合、多源/单源/数字冲突标签 | `domain/ranking.py`, `application/verification.py` | 排序、多样性和端到端测试 |
| F5 带引用的结构化回答 | 已实现 | 结论优先摘要、关键结论、详细分析、建议、轨迹与来源；支持 LLM 增强 | `infrastructure/reporting.py` | 摘要直接答案、简单/复杂端到端测试 |
| F6 答案自校验 | 已实现 | 引用编号、章节、子问题覆盖、论点—来源词项对应，失败回退 | `application/verification.py`, `application/service.py` | `test_verification.py` 2 项 |
| F7 报告持久化 | 已实现 | 有意义文件名、UTF-8 Markdown/Text/JSON、统一列表与正文搜索 | `infrastructure/storage.py` | 三格式导出测试、端到端保存断言 |
| F8 交互入口 | 已实现 | CLI ask/interactive；五页面 Streamlit 工作台 | `presentation/cli.py`, `streamlit_app.py`, `app_pages/` | CLI 端到端；Streamlit AppTest |
| F9 流式输出与进度可见 | 已实现 | 网络前先发 PLAN；CLI 分行输出；Web 状态/分段输出 | `application/service.py`, `presentation/cli.py`, `app_pages/research.py` | 页面渲染测试；阶段顺序手工验证 |
| F10 多轮追问 | 已实现 | 已有证据充分时复用；最新/变化/新闻/公告/数据类追问强制补搜并重新评估 | `application/service.py`, `presentation/cli.py`, `app_pages/research.py` | 时效追问补搜自动测试 |
| F11 搜索策略自适应 | 已实现 | 根据 missing 和轮次生成下一批查询 | `application/planner.py`, `application/service.py` | 复杂问题迭代测试 |
| F12 可信度标注 | 已实现 | 多源、单源、冲突三类；来源质量与状态 | `domain/models.py`, `application/verification.py` | 报告/证据页手工验证 |
| F13 搜索过程可观测 | 已实现 | `RoundTrace`、`ResearchMetrics`、进度回调、日志 | `domain/models.py`, `application/service.py`, `presentation/` | 端到端 trace 断言与页面测试 |
| F14 定时自动收集 | 已实现 | 每日/每周/单次，成功后幂等；`schedule --once` 与前台 poll | `scheduling/topics.py`, `presentation/cli.py` | 每日/每周调度测试 |
| F15 主题管理 | 已实现 | 任务增删改、启停、立即运行；领域/类型/时间/模板/格式持久化 | `scheduling/topics.py`, `infrastructure/config.py` | 完整任务往返测试、Web 表单测试 |
| F16 历史查阅 | 已实现 | 按新到旧、按文件名/正文检索、Web 阅读下载 | `infrastructure/storage.py`, `app_pages/history.py` | 端到端保存；CLI/Web 手工验证 |

## 3. 超出原始需求的 2.1 能力

| 能力 | 实现位置 | 价值 |
|---|---|---|
| Clean Architecture | `domain/`, `application/`, `infrastructure/`, `presentation/`, `bootstrap.py` | 隔离变化和提高可测试性 |
| 双在线搜索源 | `infrastructure/search/` | 覆盖与容错 |
| 两级 TTL 缓存 | `infrastructure/cache.py` | 降低重复网络等待 |
| 稳定排序与域名多样性 | `domain/ranking.py` | 减少单站点偏差和并发随机性 |
| 结构化 trace/metrics | `domain/models.py` | UI、日志和测试共享可观测状态 |
| 五维质量评分 | `application/evaluation.py` | 暴露报告风险与改进方向 |
| 三档研究预算 | `bootstrap.py` | 按任务分配时间和来源预算 |
| 产品化 Web 工作台 | `streamlit_app.py`, `app_pages/`, `assets/` | 完整研究、证据和报告工作流 |
| 安全配置持久化 | `infrastructure/config.py`, `presentation/web/support.py` | Key 不落普通配置文件 |
| ResearchBrief/ReportSpecification | `domain/models.py` | 研究领域和交付要求真正驱动查询与报告 |
| 结论优先确定性报告 | `infrastructure/reporting.py` | 无 Key 时也交付总结而不是链接目录 |
| 证据感知追问 | `application/service.py` | 需要新时效时自动补搜 |
| 自主研究任务 | `scheduling/topics.py`, `app_pages/settings.py` | 周期、领域、信息类型、篇幅和模板一体化 |
| 多格式导出 | `infrastructure/storage.py`, `app_pages/history.py` | Markdown/Text/JSON 统一管理 |

## 4. 验收命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m deepsearch --mock ask "Python 3.13 有哪些新特性" --no-stream
.\.venv\Scripts\python.exe -m deepsearch --mock ask "对比 2025 年主流大模型的推理能力和成本" --no-stream
```

预期：24 项测试通过；第一题 1 轮且摘要直接给出发布日期；第二题至少 2 轮并显示查询调整；两份报告保存到根 `reports/`。

## 5. 尚未声称完成的能力

- 逐主张自然语言蕴含和事实真伪证明；
- 搜索来源时效、作者身份和统计口径的自动裁决；
- JavaScript 浏览器渲染、PDF/OCR 和复杂表格抽取；
- 后台任务、取消、断点恢复、多租户、配额和集中观测；
- 跨报告知识库、时间版本和主动变化检测；
- 真实网络稳定性 SLA。

这些是后续路线，不应在演示或面试中描述成当前已交付能力。
