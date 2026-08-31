# DeepSearch 文档索引

> 对应产品版本：2.1.0 · 最后校准：2026-08-28

## 推荐阅读顺序

| 读者 | 文档 | 解决的问题 |
|---|---|---|
| 第一次使用 | [`../README.md`](../README.md) | 项目是什么、如何安装和首次运行 |
| 日常使用者 | [`USER_GUIDE.md`](USER_GUIDE.md) | CLI、Web、配置、追问、调度和排错 |
| 开发者 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前目录、依赖规则、时序和扩展方式 |
| 维护者 | [`DEV_NOTES.md`](DEV_NOTES.md) | 技术取舍、算法边界、测试和演进历史 |
| 验收/面试 | [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) | F1–F16 是否实现、代码和测试证据在哪里 |
| 演示者 | [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) | 运行、测试、难点、评估、问答和演示脚本 |
| 需求追溯 | [`../pro.md`](../pro.md) | 原始产品需求历史基线和当前状态说明 |

## 文档边界

- `README.md` 与 `docs/*.md` 描述当前 2.1.0 实现；
- `pro.md` 保留最初的业务目标和验收口径，顶部说明实现差异；
- `reports/*.md` 是每次研究生成的运行产物，不属于手写产品文档；
- `.streamlit/secrets.toml.example`、`config.example.json` 是配置模板，不保存真实密钥；
- 源码目录、命令、默认值或测试数量变化时，应同步更新当前文档集。

## 当前统一口径

- 产品版本：2.1.0；
- Python：3.11+；
- 默认均衡预算：3 轮、16 来源、每查询 4 候选；
- Web 导航：首页、研究、证据、报告、设置；
- 默认报告目录：仓库根 `reports/`；
- 默认缓存目录：`.cache/deepsearch/`；
- 当前回归：24 项测试全部通过；
- 架构：domain/application/infrastructure/presentation/scheduling + `bootstrap.py` 组合根。

## 维护检查

提交涉及架构或入口的变更前，至少执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
rg -n "V1|V1\.1|15 项|概览|研究工作台|证据智能|报告中心|引擎设置|presentation/reports" README.md pro.md docs --glob "!docs/README.md"
```

命中不一定是错误：`pro.md` 和 `DEV_NOTES.md` 会保留明确标注的历史版本；重点检查是否把历史描述误写为“当前”。
