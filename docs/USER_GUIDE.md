# DeepSearch 2.2 用户手册

> 适用版本：2.2.0 · 最后校准：2026-09-10

## 1. 安装与启动

要求 Python 3.11 或更高版本。Windows PowerShell：

```powershell
cd D:\code\pycharm\DeepSearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item config.example.json config.json
deepsearch web
```

默认地址为 `http://localhost:8501`。如果只运行应用，不需要测试工具，可安装 `.[ui]`。

显式离线演示：

```powershell
deepsearch --mock web
```

演示模式不访问网络，也不需要模型密钥；所有 Mock 内容都会明确标识，不能当作事实来源。

## 2. 第一次使用

1. 打开“设置 → 搜索”，确认数据来源是“在线”；没有 Brave Key 也能使用免费来源。
2. 时间、计算、问候、翻译和普通问答保持“智能判断”，系统会自动直接回答。
3. 只找链接或资源时，可直接回到“对话”并选择“搜索”。
4. 需要研究报告时，在“设置 → 研究与快速回答模型”确认研究模型状态为“模型可用”。
5. 输入区只需选择“智能判断”“搜索”或“研究”，输入问题后发送。
6. 搜索和研究结果可下载、继续追问或按配置推送；历史回答会保留各自来源链接。

## 3. 如何选择模式

### 智能判断

系统用可解释关键词做低成本路由：

- “现在几点、计算、翻译、你好、你能做什么”等请求倾向直接回答；
- “链接、官网、资源、价格、天气、新闻、当前状态”等请求倾向搜索；
- “论文、比较、研究、调研、综述、报告、分析”等请求倾向研究；
- 没有实时检索或深度研究信号时，默认直接回答，不会为了使用搜索而扩大问题。

智能判断不会覆盖用户显式选择的搜索或研究。

### 直接回答

适合本地时间、简单计算、翻译、普通常识、问候和功能咨询。时间与计算优先使用本地确定性能力；配置独立的快速回答模型后可生成其他自然短回复。

直接回答由“智能判断”自动匹配，没有单独的手动选项。天气、价格、新闻、政策和官网链接等网页实时事实进入搜索；明确的比较、调研和报告请求进入研究。直接回答会保存在会话中，但不会生成搜索快照、研究报告或资料库资产。

### 搜索

适合：

- 找官方网站、产品文档、代码仓库或网页入口；
- 找影视作品的正规播放平台和官方信息；
- 找论文、期刊或 DOI 的入口；
- 快速浏览多个来源，不需要长报告。

搜索会补充必要的中英文查询，并发调用搜索源，再执行 URL 校验、盗版/恶意下载过滤、去重、资源分类和风险标记。结果是简短答案加可点击卡片，不包含研究报告的“结论/分析/局限”套式章节。

### 研究

适合：

- 比较技术、产品或方案；
- 做文献综述、行业调研或决策分析；
- 需要正文阅读、跨来源综合、引用和局限说明；
- 希望产出可保存、可投递的结构化报告。

在线研究必须配置模型。系统先规划问题，再根据证据充分性进行一到多轮检索；简单问题可能一轮停止，复杂问题通常至少两轮，但仍受研究强度和来源预算约束。

## 4. 对话工作台

### 快捷输入

首页空状态提供默认快捷输入。悬浮可查看完整内容；点击只把模板放入输入框，不会立刻搜索或调用模型。通过“管理快捷输入”可以新增、修改或删除，最多保存 20 条。

### 高级选项

高级选项主要影响研究流程：

| 选项 | 作用 |
|---|---|
| 研究强度 | 快速减少预算，均衡使用默认值，深度扩大轮次、来源和单查询结果数 |
| 知识领域 | 给规划和查询提供领域上下文 |
| 信息类型 | 决定是否追加新闻、公告、研究、论文等搜索方向 |
| 时间范围 | 把时效要求带入查询与报告约束 |
| 地区 | 给支持地区上下文的搜索源使用 |
| 目标篇幅/读者 | 控制报告表达方式和长度目标 |
| 报告章节 | 指定希望保留的主要章节 |
| 补充要求 | 例如“区分事实与判断”“优先给可执行建议” |

### 运行和停止

运行状态默认折叠，使用稳定的“思考中”标题。研究计算在有限后台线程池中执行，页面线程会更新折叠区内部的当前阶段和累计用时，不重建折叠标题。

展开状态中的“思考步骤与说明”按实际模式展示：直接回答通常只有“理解问题 → 生成直接回复”，搜索增加检索与保存步骤，研究才显示规划、读取正文、评估证据、交叉验证、初稿、审校和质量检查。用户展开后，运行中的内容更新不会自动收起；只有用户主动点击才会收起。这里展示的是可审计的任务阶段，不是模型隐性思维链。

输入区会在运行时出现“停止生成”按钮。任务成功或失败后，页面会立即清除这个按钮；聊天输入自身在脚本结束后恢复正常提交状态。停止仍是协作式取消：系统会在下一个阶段边界中止后台任务，但若程序正阻塞在一次 HTTP 调用中，需要等该调用返回或超时后才能观察到停止信号。未完成内容不会保存为正式资产。

结果正文不使用 token 级流式输出。研究必须先完成证据整理、初稿、独立审校和确定性质量门；直接回复会完整返回后再持久化。运行过程仍通过阶段说明和计时反馈。

### 页面滚动

首页输入栏上方右侧有回顶和到底两个悬浮按钮，默认保持隐藏。鼠标滚轮作用于主内容区时，当前可用方向的按钮会淡入；停止滚动约 1.4 秒后自动淡出。页面顶部不显示回顶、底部不显示到底，没有可滚动内容时两者都不出现。点击按钮使用可被滚轮即时取消的短平滑动画，因此到达底部后第一下反向滚轮就能接管页面，不会先被尚未结束的原生平滑滚动吞掉。移动端按钮会提高位置，避免遮挡可能换行的输入工具栏。按钮不触发 Python rerun，也不会改变会话数据。

### 继续追问

结果操作区可以针对当前结果继续追问：

- 研究追问与既有证据高度相关且不要求最新信息时，可以复用当前证据；
- 包含“最新、当前、新闻、更新、数据”等时效信号，或已有证据覆盖不足时，会重新进入研究循环；
- 从磁盘恢复的会话只保留轻量来源元数据，不保留完整网页正文，因此研究追问通常需要重新检索。

## 5. 阅读结果

### 搜索结果卡片

卡片显示标题、域名、检索源、摘要、资源类型、发布时间和风险标签。主要标签含义：

| 标签 | 含义 |
|---|---|
| 可信来源 | 已知官方、正规平台或可信学术入口 |
| 普通网页 | 社区、聚合页或一般网页线索 |
| 未验证 | 系统无法确认来源身份或质量，需要用户核对 |
| 谨慎访问 | 页面含下载、短链或跳转等风险信号 |

影视搜索优先展示正规播放平台，其次是官方信息、社区与普通网页。地区版权、订阅状态和实际上架情况必须以平台页面为准。

### 研究报告

研究模式使用同一个已配置模型连续完成三个阶段：

1. **证据整理**：从已抓取正文和摘要中提取直接答案、主张、引用、冲突和缺口。
2. **初稿生成**：按目标读者、篇幅和章节生成结论优先 Markdown。
3. **独立审校**：检查直接性、重复、证据支持和排版，并输出重写后的终稿。

之后程序执行确定性质量门，检查引用编号、来源表、章节覆盖、详细论点的词项支持、重复块和模板噪声。来源清单由系统根据本次真实来源确定性补齐；简单事实题不强制扩写“分析”，复杂问题仍要求分析章节。首次不通过时只允许一次针对性修订；仍不合格则报告失败，不保存也不推送。

报告后的“证据详情与审校”折叠区包含来源数、轮次、审校摘要、五维质量分和检索轨迹。它不会显示或保存模型的隐性思维链。

质量分用于暴露工程风险，不是事实真实性概率。即使显示“可靠”，关键决策仍应打开原始来源核验。

## 6. 会话、资料库与回收站

### 会话

- 首次发送消息后自动创建会话；侧栏展示最近 6 条。
- “全部记录”支持标题搜索、分页、继续对话和二次确认删除。
- 删除会话会永久删除对应 JSON，但不会删除报告或搜索快照。
- 会话保存消息、工作模式、轻量来源元数据、资产路径和投递状态，不保存密钥或完整网页正文。

### 资料库

资料库同时扫描：

- `data/artifacts/` 中的搜索 Markdown 快照；
- `reports/` 中的 Markdown、Text、JSON 研究报告。

支持按文件名或正文搜索、预览、下载和推送。选择资产后，操作栏的“推送”按钮可选择“公开”“内部”或“机密”并发送到 CustomerService（接口值分别为 `public`、`internal`、`confidential`）；未启用联动时会显示配置提示。若该资产来自会话，资料库会复用首页相同的投递身份，重复推送不会创建第二份知识库文档。“移到回收站”需要确认，完成后会清除会话中的失效资产路径，但保留会话文字和来源摘要。

报告集合发生变化时，选择器会同步重建。删除当前报告后，如果还有其他报告，会自动切换到现存项；如果列表为空，则显示空状态，不会继续展示已删除标题。

### 回收站

回收站位于 `data/trash/`，记录原路径和移入时间：

- 恢复时会创建原目录，但不会覆盖原位置的同名文件；
- 永久删除只针对经过目录和元数据双重校验的当前条目；
- 永久删除需要再次确认，完成后无法撤销；
- 元数据损坏、资产缺失或路径超出允许目录的条目不会显示为可操作项。

## 7. 下载、复制和 CustomerService

搜索快照与研究报告都必须先落盘，才能显示结果操作区。

- **下载**：在 Markdown、Word、PDF、TXT、JSON 和 HTML 中选择一种格式即时导出；不会在资料库中生成重复文件。
- **复制**：在只读 Markdown 代码框右上角复制。
- **切换模式重跑**：使用原问题改走搜索或研究。
- **推送**：把现有资产发送到 CustomerService，不重复执行搜索或研究。

启用联动需要：

1. 在“设置 → 知识库联动”填写 CustomerService **API 根地址**并启用，默认 `http://127.0.0.1:8000`，不要填写 Streamlit 页面端口；
2. 在 DeepSearch 的 `.streamlit/secrets.toml` 配置 `DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY`；
3. 在 CustomerService 的 `.env` 配置值完全相同的 `DEEPSEARCH_INTEGRATION_KEY`；
4. 如目标服务还要求 Bearer 凭据，再配置 `DEEPSEARCH_CUSTOMER_SERVICE_API_KEY`；
5. 修改 Secrets 后重启 DeepSearch，再使用“公开”或“内部”文档验证。

联动未启用或缺少本地密钥时，页面会直接显示配置提示，不发送请求。正常推送先收到 CustomerService v2 的 `202 + job_id`，随后等待后台任务成为 `succeeded`；页面不会再把“已排队”误报为“已入库”。连接、密钥、接口版本、文档策略、后台任务失败和等待超时都会显示具体原因。outbox 只保存资产路径、内容哈希和非敏感元数据；重试投递前会复核哈希，防止排队期间文件被替换。

常见提示：

| 页面提示 | 应检查的项目 |
|---|---|
| 缺少联动密钥 | DeepSearch `.streamlit/secrets.toml` 是否配置并已重启 |
| 拒绝了联动密钥 | 两边密钥是否逐字一致 |
| v2 联动接口不存在 | 地址是否误填为前端端口，CustomerService 是否为最新版 |
| 尚未完成联动配置 | CustomerService `.env` 是否配置 `DEEPSEARCH_INTEGRATION_KEY` |
| 拒绝此文档 | 演示模式不接受 `confidential`，或正文命中凭据特征检查 |
| 已接收但未完成入库 | 到 CustomerService 的“入库任务”查看 worker 状态和错误 |

```powershell
deepsearch delivery list
deepsearch delivery retry
deepsearch delivery retry --force
```

`--force` 也会重试 4xx 等默认判定为不可自动重试的记录，请先确认配置已经修正。

## 8. 自动任务

Web 的“自动任务”页和 CLI 都支持 daily、weekly、once：

```powershell
deepsearch tasks add "AI 日报" "总结最近一天 AI Agent 领域的重要变化" `
  --time 10:00 --schedule-type daily --profile 深度 `
  --domain "人工智能" --types "新闻,公告,研究" `
  --time-scope "最近 24 小时" --words 1800

deepsearch tasks list
deepsearch tasks run "AI 日报"
deepsearch tasks disable "AI 日报"
deepsearch tasks enable "AI 日报"
deepsearch schedule --once
```

自动任务始终执行研究模式，在线运行需要模型。调度器是本地前台循环：只有 `deepsearch schedule` 进程持续运行时才会按计划检查任务。每个计划实例成功后才记录幂等状态；失败不会标记成功，也不会阻断同一批次中的其他任务。

## 9. 配置与密钥

### 推荐方式

普通配置放在 `config.json`；密钥放在环境变量或 `.streamlit/secrets.toml`。示例：

```toml
DEEPSEARCH_API_KEY = "your-key"
DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"
DEEPSEARCH_MODEL = "gpt-4o-mini"
DEEPSEARCH_LLM_TIMEOUT = 180
DEEPSEARCH_BRAVE_API_KEY = "your-brave-key"

DEEPSEARCH_CHAT_API_KEY = "your-lightweight-model-key"
DEEPSEARCH_CHAT_BASE_URL = "https://your-provider.example/v1"
DEEPSEARCH_CHAT_MODEL = "your-lightweight-model"
DEEPSEARCH_CHAT_TIMEOUT = 30

DEEPSEARCH_CUSTOMER_SERVICE_INTEGRATION_KEY = "integration-key"
DEEPSEARCH_CUSTOMER_SERVICE_API_KEY = "optional-api-key"
```

配置优先级：内置默认值 → JSON → 环境变量/Streamlit Secrets → 调用参数。`reports_dir`、`cache_dir`、`conversation_dir` 和 outbox 相对路径都相对于配置文件所在目录解析。

旧配置迁移规则：

- 旧 `mode=mock` 映射为 `runtime_mode=mock`；其他旧值映射为 `online`。
- `default_work_mode=auto/search/research` 只控制 Web 默认工作模式；旧值 `chat` 会自动迁移为 `auto`。
- 旧 `topics` 和旧任务字段会在读取时兼容为新版研究任务结构。

## 10. CLI 参考

```powershell
deepsearch [--config PATH] [--mock] ask QUESTION [--mode auto|search|research] [--no-stream]
deepsearch [--config PATH] [--mock] interactive
deepsearch [--config PATH] history [--query TEXT]
deepsearch [--config PATH] tasks list|add|remove|run|enable|disable ...
deepsearch [--config PATH] schedule [--once] [--poll SECONDS]
deepsearch [--config PATH] delivery list|retry [--force]
deepsearch web [--port PORT]
```

`topics` 是 `tasks` 的兼容别名。`interactive` 是研究追问模式，不提供搜索/研究自动路由。

## 11. 故障排查

### 搜索没有结果

- 检查网络、DNS 和代理；
- 可配置 Brave Key 提升覆盖率；
- 站点的 403、521 或正文过短只影响相应页面，系统会保留摘要并继续；
- 如果所有在线搜索源都失败，系统会明确报错，不会自动改用 Mock。

### 研究提示缺少模型

确认当前不是仅搜索配置，并设置 `DEEPSEARCH_API_KEY`。模型名称和 Base URL 仅显示默认值并不代表密钥可用；修改 Secrets 后重启 Streamlit。

### 侧栏显示“本地基础回答”

这表示 `DEEPSEARCH_CHAT_API_KEY`、`DEEPSEARCH_CHAT_BASE_URL` 或 `DEEPSEARCH_CHAT_MODEL` 至少缺少一项，或当前使用显式演示模式。本地时间、简单计算、问候和功能介绍仍可使用。配置后在“设置 → 研究与快速回答模型”的“快速回答模型”区域核对状态，云端应用需要修改云端 Secrets 并重启。

### 模型返回 HTTP 错误

| 状态 | 常见原因 | 处理建议 |
|---|---|---|
| 400 | 参数或协议不兼容 | 确认服务支持 Chat Completions 和当前模型参数 |
| 401 | Key 无效或业务空间错误 | 重新检查密钥来源与所属空间 |
| 403 | 当前 Key 无模型权限 | 开通权限或换用可用模型 |
| 404 | Base URL、地域路径或模型 ID 不匹配 | 确认接口根地址最终能拼成 `/chat/completions` |
| 429 | 频率或额度受限 | 等待后重试并检查配额 |
| 5xx | 模型服务暂时不可用 | 程序会自动重试一次，仍失败则稍后再试 |

404 等确定性配置错误不会做无意义重试；408、409、429 和常见 5xx 会重试一次。

### 模型响应超时

默认单次模型调用超时为 180 秒。研究一般调用模型三次；质量门首次失败时还可能多一次修订。思考模型处理长证据时首个响应较慢，可在设置页把模型超时提高到 300–900 秒，或用 `DEEPSEARCH_LLM_TIMEOUT` 覆盖。该值不是整个研究任务的总时长。

### 只有摘要，没有正文

目标页可能需要 JavaScript、登录、付费订阅，或拒绝抓取。系统不会绕过访问控制。PDF 只读取公开、未加密且包含可提取文本的前 80 页；扫描图片型 PDF 不支持 OCR。

### 报告未保存

检查页面或日志中的具体阶段。模型阶段失败会标明“证据整理/初稿/独立审校”；引用或内容校验两次失败会抛出质量门错误。失败输出不保存是预期的安全行为。

### 删除后仍看到旧报告

2.2 当前实现会让选择器键随报告集合变化而更新。若浏览器仍显示旧界面，请确认运行的是当前工作区代码，并刷新或重启 Streamlit；文件是否实际存在可在资料库或 `data/trash/` 中核对。

## 12. 数据与安全边界

- 只读取公开可访问的 HTTP(S) HTML、纯文本和 PDF。
- 不绕过登录、付费墙、加密、验证码或访问限制。
- 不把密钥写入普通设置、会话、报告或 outbox。
- 风险过滤会排除明显盗版、破解、恶意下载和非 HTTP(S) 在线链接，但不能替代人工判断。
- 项目回收站只允许操作 `reports/` 与 `data/artifacts/` 中的受支持文件。

## 13. Streamlit Community Cloud 部署

Streamlit Community Cloud 可免费部署公开应用。DeepSearch 当前 GitHub 仓库为公开仓库，因此部署后默认公开，并可能被搜索引擎收录。部署时填写：

| 字段 | 内容 |
|---|---|
| Repository | `AllSuoHa/DeepSearch` |
| Branch | `main` |
| Main file path | `streamlit_app.py` |
| Python version | `3.12` |

仓库根目录的 `requirements.txt` 直接安装正文提取、PDF 和 Streamlit 运行依赖；应用代码从仓库根目录加载。代码更新推送到 `main` 后，Community Cloud 会自动重新部署。

### 云端 Secrets

在应用部署页的 **Advanced settings → Secrets**，或部署后的 **App settings → Secrets** 中按 TOML 格式填写。最小研究配置示例：

```toml
DEEPSEARCH_API_KEY = "your-model-key"
DEEPSEARCH_BASE_URL = "https://api.openai.com/v1"
DEEPSEARCH_MODEL = "gpt-4o-mini"
DEEPSEARCH_LLM_TIMEOUT = 180
```

快速回答模型必须单独配置，不能复用研究 Key：

```toml
DEEPSEARCH_CHAT_API_KEY = "your-lightweight-model-key"
DEEPSEARCH_CHAT_BASE_URL = "https://your-provider.example/v1"
DEEPSEARCH_CHAT_MODEL = "your-lightweight-model"
DEEPSEARCH_CHAT_TIMEOUT = 30
```

Streamlit Cloud 不会读取开发者电脑中的 `.streamlit/secrets.toml`，必须在云端应用设置重新填写。不配置任何模型仍可使用普通搜索、本地时间、简单计算和基础问候。轻量模型不等于一定免费，额度、计费和频率限制取决于部署者选择的供应商；公开应用应在供应商侧设置限额和限流。需要 Brave Search 时再加入 `DEEPSEARCH_BRAVE_API_KEY`；只有同时部署了可从公网访问的 CustomerService API 时，才配置联动地址和对应凭据。

不要把真实值写入仓库中的 `.streamlit/secrets.toml.example`，也不要提交 `.streamlit/secrets.toml`、`.env` 或 `config.json`。云端 Secrets 的内容不会写入 GitHub。

### 上线检查

1. 打开首页，确认“搜索”模式能返回真实链接；
2. 已配置模型时，再用一个简短问题验证“研究”模式；
3. 检查设置页是否只显示已配置状态，不回显密钥；
4. 把生成的 `https://<subdomain>.streamlit.app` 地址作为项目网站的 DeepSearch 入口；
5. 如果应用长时间无人访问后唤醒较慢，等待实例恢复后重试。

### 云端运行边界

Community Cloud 的本地文件系统不保证持久保存。`data/`、`reports/`、`.cache/`、`logs/`、页面保存的 `config.json` 和任务状态都可能在实例重启、重新部署或休眠恢复后丢失。因此：

- 云端版本适合公开体验智能问答、搜索与研究流程；
- 重要报告应及时下载；
- 需要长期保存会话、任务或报告时，应实现外部对象存储或数据库适配器；
- `deepsearch schedule` 是本地前台轮询器，Community Cloud 休眠时不会持续执行。
