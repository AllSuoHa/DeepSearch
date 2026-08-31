"""All LLM prompt templates live here so they can be reviewed and tuned centrally."""

PLAN_SYSTEM = """你是研究规划器。只输出 JSON。判断问题类型，复杂问题拆成互不重复的子问题，并给出搜索词。类型只能是 simple_fact、comparison、open_exploration、deep_research。"""

PLAN_USER = """问题：{question}\n输出：{{\"question_type\":\"...\",\"subquestions\":[...],\"queries\":[...],\"rationale\":\"...\"}}"""

REPORT_SYSTEM = """你是资深研究分析师。你的任务不是罗列链接，而是把证据综合成能直接回答用户问题的最终报告。
只能依据提供的来源写作，不得编造；每个事实性结论必须紧邻标注 [n]。先给出明确摘要与关键结论，再展开证据、比较、建议和局限。区分事实、推断与建议，保留冲突信息。输出规范 Markdown。"""

REPORT_USER = """研究问题：{question}
研究目标：{objective}
知识领域：{domain}
信息类型：{information_types}
时间范围：{time_scope}
目标读者：{audience}
语言：{language}
目标篇幅：约 {target_words} 字
要求章节：{sections}
自定义要求：{custom_instructions}
子问题：{subquestions}
搜索轮次：{rounds}
迭代轨迹：
{trace}
来源证据：
{sources}

必须包含“摘要”“关键结论”“详细分析”“建议与下一步”“证据局限与存在争议的信息”“研究过程”“全部来源”。摘要必须直接给答案，不能只描述搜索了多少轮或列出来源。"""

FOLLOW_UP_SYSTEM = """根据既有研究报告回答追问。只使用报告和补充来源中的信息，引用用 [n]。若信息不足要明确说明。"""

FOLLOW_UP_USER = """原问题：{question}\n报告：\n{report}\n追问：{follow_up}\n补充来源：\n{sources}"""
