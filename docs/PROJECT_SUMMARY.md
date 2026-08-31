# DeepSearch 2.1 项目总结

> 当前版本：2.1.0 · 回归：24/24 通过 · 校准：2026-08-28

完整的运行、演示、专家评估、面试问题和重难点说明见根目录 [`DeepSearch-V2.1-项目总结与演示手册.md`](../DeepSearch-V2.1-项目总结与演示手册.md)。本文件保留架构文档集中的简明结论。

## 1. 项目是什么

DeepSearch 是一个本地优先的迭代式研究 Agent。它不是返回链接，而是围绕问题或自主任务执行：规划 → 搜索 → 阅读 → 评估证据 → 补搜 → 再评估 → 生成总结 → 引用校验 → 保存报告。

## 2. 2.1 关键升级

- 报告摘要直接给答案或综合发现，不再只描述轮次和来源数；
- 输出关键结论、分维度分析、建议、局限、研究过程和来源表；
- `ResearchBrief` 让领域、信息类型和时间窗口进入查询；
- `ReportSpecification` 控制篇幅、读者、章节、要求和 Markdown/Text/JSON 导出；
- 追问需要最新证据或已有覆盖不足时自动补搜；
- 自主任务支持每日、每周、单次、启停、立即运行和成功幂等；
- Streamlit 研究页和设置页已经提供相应配置；
- 旧测试报告归档到 `reports/archive/pre-2.1/`，根目录只展示新版验收报告。

## 3. 可审计的研究循环

每轮 `RoundTrace` 包含查询、搜索结果、新来源、正文数、耗时、证据缺口、充分性决定、是否降级和下一轮查询。复杂问题至少产生一次反馈；每个维度优先要求两条证据和多来源域。连续无新增或达到硬预算时停止，并在报告中诚实披露未解决缺口。

## 4. 自主任务模型

| 方面 | 支持内容 |
|---|---|
| 周期 | daily / weekly / once |
| 时间 | HH:MM、星期列表、单次日期 |
| 研究 | 问题、目标、领域、信息类型、时间范围 |
| 预算 | 快速、均衡、深度 |
| 交付 | Markdown/Text/JSON、300–5000 字、读者、章节、自定义要求 |
| 运维 | 启用、停用、立即运行、删除、成功幂等、失败隔离 |

## 5. 快速运行

```powershell
cd D:\code\pycharm\DeepSearch
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
deepsearch --mock ask "Python 3.13 什么时候发布"
deepsearch web
```

自主任务示例：

```powershell
deepsearch tasks add "AI 日报" "总结最近一天的大模型行业变化" `
  --time 10:00 --schedule-type daily `
  --domain "人工智能" --types "新闻,公告,研究" `
  --time-scope "最近 24 小时" --words 1800 --profile 深度
deepsearch schedule --once
```

## 6. 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

24 项测试覆盖结论摘要、复杂迭代、研究范围、追问补搜、缓存与排序、引用校验、多格式导出、每日/每周/单次任务、任务规格持久化、Streamlit 页面和入口路径。

## 7. 当前边界

Mock 只能验证工作流；无 LLM 的复杂语义综合偏保守；当前不支持 JavaScript/PDF/OCR、自然语言蕴含证明、后台任务队列、取消恢复、跨报告知识库和通知渠道。生产化优先建设 claim-evidence graph、后台任务状态机和可重复评测集。
