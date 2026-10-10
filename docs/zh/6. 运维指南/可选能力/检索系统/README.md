# 检索系统

检索系统是 openJiuwen Agentic Hub 的可选增强能力，用于提升 Skill 搜索和发现体验。基础部署可以不启用，未启用时搜索可降级为数据库查询。部署步骤见[安装指导](../../../3.%20安装指导/本地安装/openJiuwen-Agentic-Hub安装指导.md)第 8.2 节；本篇讲全配置变量和运维关注点。

## 主要配置变量

本表默认值为代码默认值。安装指导示例中的取值是推荐启用值（如 `REBUILD_ON_STARTUP=true`，启动时立即建索引，无需等待首次定时任务）。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` | Embedding API 地址 | 空（未配置则降级为数据库查询） |
| `MARKET_RETRIEVAL_EMBEDDING_API_KEY` | Embedding API 密钥 | 空 |
| `MARKET_RETRIEVAL_EMBEDDING_MODEL` | Embedding 模型名 | 空 |
| `MARKET_RETRIEVAL_MODEL_API_BASE_URL` | 检索 LLM API 地址 | 空 |
| `MARKET_RETRIEVAL_MODEL_API_KEY` | 检索 LLM API 密钥 | 空 |
| `MARKET_RETRIEVAL_DEFAULT_LLM_MODEL` | 默认检索 LLM 模型 | 空 |
| `MARKET_RETRIEVAL_BUILD_METHOD` | 索引构建方法：`bm25`、`embedding`、`embedding_bm25`、`all`（含 tree 索引，需配 LLM） | `embedding_bm25` |
| `MARKET_RETRIEVAL_SEARCH_METHOD` | 检索方法：`bm25`、`embedding`、`auto`、`progressive` | `embedding` |
| `MARKET_RETRIEVAL_REBUILD_CRON` | 重建索引的 cron 表达式 | `0 * * * *`（每小时） |
| `MARKET_RETRIEVAL_REBUILD_ON_STARTUP` | 启动时是否重建索引 | `false` |

> 未配置 Embedding API 时，检索自动降级为数据库模糊查询，不影响基础功能。

## 相关能力

- **Skill 分类标签**：与检索模块一同启动，由 LLM 为新发布的 Skill 自动打分类标签，用于首页类别展示。部署配置见[安装指导](../../../3.%20安装指导/本地安装/openJiuwen-Agentic-Hub安装指导.md)第 8.3 节。

## 相关开发文档

检索系统的实现、索引和 SDK 说明位于 [开发指南 / 检索系统](../../../5.%20开发指南/检索系统/README.md)。

## 运维关注点

- Embedding / LLM 服务地址和密钥。
- 索引构建策略（`BUILD_METHOD` 和 `REBUILD_CRON`）。
- 索引文件存储位置（对象存储中）。
- 定时重建任务。
- 多实例索引热加载通知。

## 根据日志调整阈值

检索日志使用 INFO 级别，查看后端标准输出或 `logs/app.log`（目录由 `INTERFACE_LOG_DIR` 指定），
按 `retrieval_` 搜索，再用 `request_id` 关联同一次请求，无需开启 DEBUG。

| 关键字 | 内容 |
|--------|------|
| `retrieval_params` | 搜索词、top_k、模型、生效的绝对/相对阈值、BM25 最少命中词数和两路权重 |
| `retrieval_stage` | 两路各自的命中分数、过滤统计、实际分数门槛和被过滤分数样本 |
| `retrieval_result` | 融合后的排序、两路排名、融合得分、资产 ID 和检索耗时 |

`effective_min_score` 是生效的分数下限，最高分为正时等于 `max(绝对阈值, best_score × 相对阈值)`，
未设置的条件不参与计算。`score_filter_removed_count` 是分数过滤数量，`top_k_removed_count` 是通过分数过滤但超出返回窗口的数量。
`truncation.reason` 表示决定截断位置的条件；结合 `score_rejected_samples` 的分数判断阈值是否过高。
被过滤的分数样本固定取最高的 10 条，剩余数量见 `score_rejected_omitted_count`；向量统计范围仅限先召回的 top_k。
BM25 的 `term_match_rejected_count` 表示正分候选因命中查询词数不足而被剔除。

`retrieval_result` 是业务筛选前的检索结果；后续仍可查看已有的 `retrieval path:` 和 `retrieval no hits after filter:` 日志。
修改阈值或权重后需重启后端，无需重建索引；Docker Compose 修改 `.env` 后需要重新创建后端容器以读取新环境变量。

## 配置边界

检索相关配置仍保留在 `.env.example` 中，因为它属于 marketplace 的可选增强能力，不需要独立运行时服务；但文档中应明确未启用时可留空或使用降级策略。
