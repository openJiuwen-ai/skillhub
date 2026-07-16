# 在线体验 marketplace 配置说明

在线体验的 marketplace 配置以仓库根目录 [`.env.example`](../../../../../.env.example) 中的 `SKILL PLAYGROUND` 段为准，本文不重复维护变量模板。

- 本地部署：将 `.env.example` 复制为 `.env` 后按需修改。
- Docker 部署：按 [Docker 安装指导](../../../3.%20安装指导/Docker方式安装/SkillHub安装指导.md) 复制为 `.env.docker`。
- K8s 部署：修改 [`docker/k8s/marketplace-config.yaml`](../../../../../docker/k8s/marketplace-config.yaml) 中的在线体验配置。

只有启用在线体验时才需要设置这些变量；基础 SkillHub 部署可保持默认关闭。

## 配置边界

- PLAYGROUND_ENABLED、SKILL_RUNNER_URL、PLAYGROUND_DAILY_LIMIT、PLAYGROUND_MULTI_INSTANCE 属于 marketplace 侧配置。
- SKILL_RUNNER_*、LLM、worker pod、K8s executor 等配置属于 skill-runner.env.example。
- Redis 只有在多实例或需要共享状态时才需要配置。
