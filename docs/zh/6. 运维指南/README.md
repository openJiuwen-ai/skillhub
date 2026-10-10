# 运维指南

本目录面向 openJiuwen Agentic Hub 的部署和运维人员。文档按“基础部署”和“可选能力”拆分，避免把在线体验、检索增强、个性化推荐、多实例等可选能力误认为基础部署必需项。

## 基础部署

基础部署用于把 openJiuwen Agentic Hub 市场服务跑起来，通常只需要：marketplace、frontend、MySQL、对象存储和鉴权服务。

| 文档 | 说明 |
|---|---|
| [K8s 完整部署](../3.%20安装指导/K8s方式安装/openJiuwen-Agentic-Hub安装指导.md) | marketplace + frontend + skill-runner 全部部署到 K8s |
| [对象存储配置](./基础部署/对象存储配置.md) | MinIO / OBS 配置要点 |
| [数据库迁移](./基础部署/数据库迁移.md) | SQL 脚本和升级注意事项 |
| [故障排查](./基础部署/故障排查.md) | 基础服务常见问题 |

## 可选能力

以下能力按需启用，基础部署不要求配置。

| 可选能力 | 说明 |
|---|---|
| [在线体验](./可选能力/在线体验/README.md) | 通过 skill-runner 为已审核 Skill 提供在线试用 |
| [检索系统](./可选能力/检索系统/README.md) | 启用 Embedding / BM25 / 混合检索增强 Skill 发现能力 |
| [推荐系统](./可选能力/推荐系统/README.md) | 启用个性化推荐；含启动是否跑任务、验收步骤与 `source` 含义 |

## 配置文件边界

| 配置文件 | 归属 | 是否基础必需 |
|---|---|---|
| `.env.example` | marketplace / frontend 基础配置模板 | 是 |
| `.env` | 本地安装 / Docker 一键部署的实际配置（从 `.env.example` 复制） | 是 |
| `skill-runner.env.example` | 在线体验运行时配置 | 否 |
| [marketplace 在线体验配置示例](./可选能力/在线体验/marketplace.env.example.md) | 在线体验 marketplace 侧开关 | 否 |

原则：先按 [安装指导](../3.%20安装指导/README.md) 完成基础部署；需要在线体验、检索增强或个性化推荐时，再阅读可选能力文档。
