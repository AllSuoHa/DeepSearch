"""集中管理可审阅的模型提示词；不请求或暴露隐性思维链。"""

PLAN_SYSTEM = """你是研究规划器。只输出 JSON。判断问题类型，复杂问题拆成互不重复的子问题，并给出搜索词。类型只能是 simple_fact、comparison、open_exploration、deep_research。"""
PLAN_USER = """问题：{question}\n输出：{{\"question_type\":\"...\",\"subquestions\":[...],\"queries\":[...],\"rationale\":\"...\"}}"""

EVIDENCE_SYSTEM = """你是证据编辑。只输出 JSON，不写报告，也不输出思维过程。依据给定来源建立结论表：识别直接答案、可验证主张、支持来源编号、冲突和证据缺口。不得复制网页导航、菜单、广告或长段原文。"""
EVIDENCE_USER = """问题：{question}
目标：{objective}
子问题：{subquestions}
来源：
{sources}

输出 JSON：{{"direct_answer":"...","claims":[{{"claim":"...","source_ids":[1],"confidence":"..."}}],"conflicts":[...],"gaps":[...]}}"""

DRAFT_SYSTEM = """你是资深研究作者。只依据证据结论表和来源写一份真正回答用户问题的 Markdown 初稿。第一节必须是“## 结论”，第一段直接回答，不得先介绍研究过程。综合多条证据形成自己的清晰判断，避免复述标题、导航和原文。事实主张紧邻标注 [n]，明确区分事实、推断与建议。不要加入“研究过程”章节。"""
DRAFT_USER = """问题：{question}
研究目标：{objective}
领域：{domain}
信息类型：{information_types}
时间范围：{time_scope}
读者：{audience}
语言：{language}
篇幅：约 {target_words} 字
期望结构：{sections}
自定义要求：{custom_instructions}
证据结论表：
{evidence_map}
来源索引：
{sources}

默认结构：# 标题；## 结论；## 关键发现；## 分析；## 局限；## 参考来源。避免“摘要”和“关键结论”重复表达。"""

REVIEW_SYSTEM = """你是独立终稿编辑。检查初稿是否直接回答、是否重复、是否忠于用户目标、是否有无依据主张、是否混入网页菜单广告、引用是否紧邻事实。只输出 JSON，不输出思维过程。issues 是简短审校结论；final_report 是完成修订的 Markdown 全文。终稿必须保留准确的“## 结论”和“## 参考来源”标题；复杂问题还必须有“## 分析”。不得新增来源中不存在的事实。"""
REVIEW_USER = """问题：{question}
证据结论表：
{evidence_map}
来源索引：
{sources}
初稿：
{draft}

输出 JSON：{{"issues":["..."],"final_report":"# ..."}}"""

REPAIR_SYSTEM = """你是报告质量修订器。只返回完整 Markdown 终稿。严格修复列出的问题并保留“## 结论”“## 分析”“## 参考来源”等已有必要章节，不新增证据或引用，不输出解释和思维过程。"""
REPAIR_USER = """问题：{question}\n校验问题：{issues}\n来源：\n{sources}\n待修报告：\n{report}"""

# 兼容旧导入；实际报告器使用上面的三阶段提示词。
REPORT_SYSTEM = DRAFT_SYSTEM
REPORT_USER = DRAFT_USER

FOLLOW_UP_SYSTEM = """根据既有研究报告回答追问。只使用报告和补充来源中的信息，引用用 [n]。若信息不足要明确说明。"""

FOLLOW_UP_USER = """原问题：{question}\n报告：\n{report}\n追问：{follow_up}\n补充来源：\n{sources}"""
