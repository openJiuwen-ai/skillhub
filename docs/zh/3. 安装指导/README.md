# 安装指导

本目录面向自建 SkillHub 的部署者，说明如何在本地或容器环境启动 marketplace、frontend 及其依赖服务。

## 文档列表

| 文档 | 说明 |
|---|---|
| [本地安装](./本地安装/SkillHub安装指导.md) | Windows 为主的本地开发启动流程 |
| [Docker 一键部署](./Docker方式安装/SkillHub安装指导-一键部署.md) | Docker Compose 一条命令启动全套服务，适合快速试用 |
| [Docker 安装](./Docker方式安装/SkillHub安装指导.md) | 手动构建运行各容器，支持复用宿主机已有的 MySQL / MinIO |
| [K8s 安装](./K8s方式安装/SkillHub安装指导.md) | 完整 K8s 部署（marketplace + frontend + skill-runner） |
| [升级说明](./升级说明.md) | 升级前检查项、数据备份和变更记录阅读路径 |

## 安装前置依赖

- MySQL：存储市场资产、版本、审核、互动等数据。
- S3 兼容对象存储：存储 Skill 包、图标和索引文件。
- 登录与鉴权：浏览公开内容无需配置；Web 登录、发布和审核需配置 OAuth。
- Node.js：仅前端本地开发需要。

使用 Docker 一键部署时，MySQL 和对象存储由容器自动提供，无需预先安装。

## 如何选择

| 场景 | 文档 |
|---|---|
| 想最快跑起来，本地没有装 MySQL / MinIO | [Docker 一键部署](./Docker方式安装/SkillHub安装指导-一键部署.md) |
| 本地开发调试，方便改代码看日志 | [本地安装](./本地安装/SkillHub安装指导.md) |
| 容器化部署，且要复用宿主机已有的 MySQL / MinIO | [Docker 安装](./Docker方式安装/SkillHub安装指导.md) |
| 生产环境、多副本部署 | [K8s 安装](./K8s方式安装/SkillHub安装指导.md) |

## 可选能力

基础部署不要求启用检索增强、在线体验或多实例 Redis。如需启用，请参阅：

- [检索系统](../6.%20运维指南/可选能力/检索系统/README.md)
- [在线体验（仅 K8s）](../6.%20运维指南/可选能力/在线体验/README.md)

