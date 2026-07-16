# 检索系统

检索系统是 SkillHub 的可选增强能力，用于提升 Skill 搜索和发现体验。基础部署可以不启用，未启用时搜索可降级为数据库查询。部署步骤见[安装指导](../../../3.%20安装指导/本地安装/SkillHub安装指导.md)第 8.2 节；本篇讲全配置变量和运维关注点。

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

- **Skill 分类标签**：与检索模块一同启动，由 LLM 为新发布的 Skill 自动打分类标签，用于首页类别展示。部署配置见[安装指导](../../../3.%20安装指导/本地安装/SkillHub安装指导.md)第 8.3 节。

## 相关开发文档

检索系统的实现、索引和 SDK 说明位于 [开发指南 / 检索系统](../../../5.%20开发指南/检索系统/README.md)。

## 运维关注点

- Embedding / LLM 服务地址和密钥。
- 索引构建策略（`BUILD_METHOD` 和 `REBUILD_CRON`）。
- 索引文件存储位置（对象存储中）。
- 定时重建任务。
- 多实例索引热加载通知。

## 配置边界

检索相关配置仍保留在 `.env.example` 中，因为它属于 marketplace 的可选增强能力，不需要独立运行时服务；但文档中应明确未启用时可留空或使用降级策略。
