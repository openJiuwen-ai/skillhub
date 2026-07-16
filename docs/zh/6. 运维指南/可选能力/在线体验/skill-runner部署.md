# skill-runner 部署

skill-runner 是在线体验运行时，负责会话编排、沙箱执行和流式输出。

## 部署关系

```text
frontend -> marketplace -> skill-runner -> worker sandbox
```

marketplace 通过反向代理把在线体验请求转发给 skill-runner。skill-runner 根据 executor 配置创建或复用执行环境。

## 前置条件

- marketplace 已部署且 `PLAYGROUND_ENABLED=true`
- `SKILL_RUNNER_URL` 指向 skill-runner 地址
- skill-runner 以独立 Deployment 部署，依赖已构建进控制面镜像
- **K8s 集群**：在线体验只能通过 K8s 部署。开发或测试可使用 Docker Desktop K8s、kind 或 minikube，生产环境使用实际集群
- **worker 镜像**：需提前构建 `skill-agent-worker:latest` 镜像并推送到集群可拉取的仓库

> **重要**：修改 marketplace ConfigMap 中的 `PLAYGROUND_ENABLED` 后，必须重启 marketplace Deployment，playground 路由才会注册。

## 部署

请使用 [K8s 安装指导](../../../3.%20安装指导/K8s方式安装/SkillHub安装指导.md) 和仓库中的 [docker/k8s](../../../../../docker/k8s) 清单部署 skill-runner。

## 环境变量注入

skill-runner 仅从 K8s 注入的进程环境读取配置，不依赖隐式 `.env` 路径。请使用独立的 ConfigMap 和 Secret，避免与 marketplace 配置混用。

## 关键配置

### marketplace 侧（K8s ConfigMap）

```env
PLAYGROUND_ENABLED=true
SKILL_RUNNER_URL=http://skill-runner.skillhub-system.svc.cluster.local:8900
PLAYGROUND_DAILY_LIMIT=20
PLAYGROUND_MULTI_INSTANCE=false
```

### skill-runner 侧（K8s ConfigMap 与 Secret）

```env
# 控制面固定使用 k8s；local 仅由 worker Pod 内部使用，不是本地部署模式
SKILL_RUNNER_EXECUTOR=k8s

# 控制面通过以下配置创建 worker Pod
SKILL_RUNNER_K8S_POD_IMAGE=skill-agent-worker:latest
SKILL_RUNNER_K8S_IMAGE_PULL_POLICY=IfNotPresent
SKILL_RUNNER_PROXY_BASE_URL=http://skill-runner.skillhub-system.svc.cluster.local:8900

# LLM 配置（前缀 SKILL_RUNNER_LLM_*，与 marketplace 完全隔离）
SKILL_RUNNER_LLM_API_KEY=your-llm-key
SKILL_RUNNER_LLM_API_BASE=https://your-llm-service/v1
SKILL_RUNNER_LLM_MODEL_NAME=your-model-name

# 每用户每日 token 预算（超出后 llm_proxy 拒绝该用户的 LLM 请求，测试期可调大）
SKILL_RUNNER_USER_DAILY_TOKEN_LIMIT=50000000

# Redis（多实例时必须）
SKILL_RUNNER_REDIS_HOST=
```

> **注意**：`SKILL_RUNNER_SESSION_MAX_LIFETIME`（K8s 清单默认 `"3600"`）会写入 worker Pod 的 `activeDeadlineSeconds`——**从 Pod 创建起计时，到点 K8s 无条件强杀 Pod**。多角色协作等长耗时 Skill 的单轮运行时长必须小于该值，否则会话会在中途被掐断（前端报 `pod stream error`、Session failed）。调大后需 `kubectl -n skillhub-system rollout restart deployment/skill-runner` 并删除 `skillhub-workers` 下的旧 Pod 才会生效。

### executor 类型说明

| executor | 说明 | 适用场景 |
|----------|------|----------|
| `k8s` | 控制面通过 K8s API 为每个会话创建独立 worker pod，agent 在 pod 内运行 | 生产环境、集成测试 |
| `local` | agent 在 worker pod 内部直接运行（无 jiuwenbox 沙箱），由 worker Dockerfile 自动设置 | worker pod 内部使用 |

> **重要**：`local` executor 由 worker pod 内部自动设置，**不要在控制面手动配置 `SKILL_RUNNER_EXECUTOR=local`**。控制面始终使用 `k8s` executor。
>
> 在线体验没有宿主机本地安装模式。开发或测试使用 Docker Desktop K8s、kind、minikube 时，本质上仍是 K8s 部署。

## worker 镜像

worker 是执行 Skill 的沙箱镜像，与控制面镜像（`Dockerfile.skill-runner`）相互独立，不要混淆。构建：

```bash
docker build -f docker/skill-agent-worker/Dockerfile -t skill-agent-worker:latest .
```

- worker 内不内置真实 LLM Key（由控制面持有、经会话 token 代理），安全边界见 [LLM 代理与密钥配置](./LLM代理与密钥配置.md)；构建时注入依赖使用构建参数，不硬编码。
- 验证：`docker run --rm skill-agent-worker:latest env | findstr KEY` 输出应为空；确认 K8s 节点可拉取该镜像（kind 需 `kind load`，远程集群需先推送）。

## 验证步骤

端到端验证（页面出现入口、发送消息有回复）见 [K8s 方式安装指导](../../../3.%20安装指导/K8s方式安装/SkillHub安装指导.md) 第 9.4 节。部署侧检查项：

1. skill-runner Deployment 就绪：`kubectl -n skillhub-system rollout status deployment/skill-runner`
2. marketplace 能访问 skill-runner：检查 marketplace 日志无连接超时
3. 会话结束后 worker Pod 资源可回收：`kubectl -n skillhub-workers get pods`

## 相关文档

- [在线体验运行时](../../../5.%20开发指南/在线体验运行时.md)
- [LLM 代理与密钥配置](./LLM代理与密钥配置.md)
- [Redis 多实例配置](./Redis多实例配置.md)

