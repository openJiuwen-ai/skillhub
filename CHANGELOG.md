# Changelog

本文件记录 SkillHub 的版本迭代、新功能、变更与兼容性说明。  
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- [TeamSkillsHub 接口参考](docs/zh/7.%20API参考/TeamSkillsHub-接口参考.md)：按模块组织的对外 API 文档（端点总览、curl 示例、可见性/审核状态表）
- 用户文档体系：新用户入门、前端操作手册、角色与权限、场景化指引与 FAQ、环境配置说明（使用者）
- 根目录 `CHANGELOG.md` 持续记录版本变更

### Changed

- 官方托管域名由 `teamskills.openjiuwen.com` 更名为 `swarmskills.openjiuwen.com`（文档与默认市场地址已同步）
- README 文档索引补充用户指南入口

---

## [0.0.1] - 2026-04-30

首个开源版本（tag: `v0.0.1`）。

### Added

- **Web 前端（Swarm Skills Hub）**
  - 首页 Skill / Swarm Skill 市场：分类、搜索、网格/列表视图、详情与下载
  - 发布抽屉：Skill 目录打包上传、模板下载、版本与 changelog
  - 个人中心：我的 Skills、收藏、点赞、Git 源同步
  - 审核管理员：待审核、审核历史、审计日志查询与 CSV 导出
  - 系统审查详情页（规则 + 可选 AI 语义复核）
  - 通知中心、多语言（中/英）、OAuth 登录（GitCode / GitHub）

- **marketplace 后端**
  - Skill 发布与版本治理、预签名下载、互动（浏览/点赞/收藏）
  - Skill 上架审核（系统审查可选 + 人工审核）
  - Git 公开仓库批量同步 Skill
  - ClawHub 兼容层 API
  - 审计日志与 Skill 审核操作追溯

- **CLI**
  - `openjiuwen-plugin`、`jiuwen-teamskills` 独立发行包

- **部署**
  - 本地安装与 Docker（Windows）文档
  - TeamSkillsHub OpenAPI 接口文档

### Security

- Git 源 URL 与 `skills_subpath` 安全校验，防止 SSRF 与路径穿越
- 统一错误响应与错误码，避免敏感信息泄露
- 检索与导入资源限流

---

## 升级须知

### 从 0.0.1 升级

1. 备份 MySQL 与对象存储桶
2. 对照 `.env.example` 检查新增环境变量（如 OAuth、系统审查、Git 同步相关项）
3. 重启 marketplace 与 frontend 容器/进程
4. 验证 `/api/health` 与 OAuth 登录回调

### 兼容性

- **数据库**：升级时 marketplace 启动会自动迁移 schema；建议在维护窗口操作
- **API**：客户端请优先使用 `detail.error_code` 解析错误；OpenAPI 见 `docs/zh/7. API参考/TeamSkillsHub.md`
- **CLI**：与市场 API 版本保持一致部署，避免跨大版本混用

---

[Unreleased]: https://gitcode.com/openJiuwen/skillhub/compare/v0.0.1...HEAD
[0.0.1]: https://gitcode.com/openJiuwen/skillhub/releases/tag/v0.0.1
