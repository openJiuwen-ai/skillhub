# Changelog

本文件记录 openJiuwen Agentic Hub 的版本迭代、新功能、变更与兼容性说明。  
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 第四类 Agent 资产 **专家团**（`plugin_type` / `runtime.type` = `agent-group`，内层 `package_type` = `agent_group`；对象存储 `agent-groups/`）
- 用户文档体系：新用户入门、前端操作手册、角色与权限、场景化指引与 FAQ、环境配置说明（使用者）
- 根目录 `CHANGELOG.md` 持续记录版本变更

### Changed

- Agent 四类资产（插件 / 连接器 / 专家 / 专家团）发布：manifest 声明的 `skills`、`tools`、`persona` 等缺失文件不再拒发；Hub 只做包结构、路径安全与危险内容扫描，运行时齐套留给 JiuwenSwarm
- 产品展示名与文档由 SwarmSkillsHub / SkillHub / TeamSkillsHub 统一为 **openJiuwen Agentic Hub**（仓库目录、Docker/K8s 内部名、`swarmskill` 类型与官方域名暂不改）
- 官方托管域名由 `teamskills.openjiuwen.com` 更名为 `swarmskills.openjiuwen.com`（文档与默认市场地址已同步）
- 市场与发布展示名：`agent-plugin` 为**插件**、`agent-mcp` 为**连接器**、`agent-template` 为**专家**、`agent-group` 为**专家团**（Tab / 发布类型顺序：插件、连接器、专家、专家团）；发布成功提示按所选类型显示
- **Git 源接入**
  - 同一仓库不同 `skills_subpath` 可由不同用户分别注册（路径归一化）
  - 同步跳过改为以 Skill 目录内容摘要为主，避免无关 commit 导致重复更新
  - 删除 Git 源时级联删除该源导入的 Skill，释放去重后可再次注册

---

## [0.0.1] - 2026-04-30

首个开源版本（tag: `v0.0.1`）。

### Added

- **Web 前端（openJiuwen Agentic Hub）**
  - 首页 Skill / Swarm Skill 市场：分类、搜索、网格/列表视图、详情与下载
  - 发布抽屉：Skill 目录打包上传、模板下载、版本与 changelog
  - 个人中心：我的 Skills、收藏、点赞、Git 源同步
  - 审核管理员：待审核、审核历史、审计日志查询与 CSV 导出
  - 审查详情页（规则 + 可选 AI 语义复核）
  - 通知中心、多语言（中/英）、OAuth 登录（GitCode / GitHub）

- **marketplace 后端**
  - Skill 发布与版本治理、预签名下载、互动（浏览/点赞/收藏）
  - Skill 上架审核（可选审查 + 审核）
  - Git 公开仓库批量同步 Skill
  - ClawHub 兼容层 API
  - 审计日志与 Skill 审核操作追溯

- **CLI**
  - `openjiuwen-plugin`、`jiuwen-teamskills` 独立发行包

- **部署**
  - 本地安装与 Docker（Windows）文档
  - openJiuwen Agentic Hub OpenAPI 接口文档

### Security

- Git 源 URL 与 `skills_subpath` 安全校验，防止 SSRF 与路径穿越
- 统一错误响应与错误码，避免敏感信息泄露
- 检索与导入资源限流

---

## 升级须知

### 从 0.0.1 升级

1. 备份 MySQL 与对象存储桶
2. 对照 `.env.example` 检查新增环境变量（如 OAuth、审查、Git 同步相关项）
3. 重启 marketplace 与 frontend 容器/进程
4. 验证 `/api/health` 与 OAuth 登录回调

### 兼容性

- **数据库**：升级时 marketplace 启动会自动迁移 schema；建议在维护窗口操作
- **API**：客户端请优先使用 `detail.error_code` 解析错误；OpenAPI 见 `docs/zh/7. API参考/openJiuwen-Agentic-Hub.md`
- **CLI**：与市场 API 版本保持一致部署，避免跨大版本混用

---

[Unreleased]: https://gitcode.com/openJiuwen/skillhub/compare/v0.0.1...HEAD
[0.0.1]: https://gitcode.com/openJiuwen/skillhub/releases/tag/v0.0.1
