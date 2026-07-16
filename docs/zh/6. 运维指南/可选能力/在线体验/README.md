# 在线体验

在线体验是 SkillHub 的可选能力，用于在浏览器中直接试用已通过审核的 Skill。基础 SkillHub 部署不需要启用在线体验。

## 组件组成

| 组件 | 说明 |
|---|---|
| marketplace playground proxy | 对外暴露 `/api/v1/playground/*`，负责鉴权、配额和内容注入 |
| skill-runner | 独立运行时控制面，负责会话编排和流式输出 |
| worker sandbox | 执行 Skill 的隔离环境 |
| LLM proxy | 真实 Key 留在控制面，worker 使用会话 token 调用代理 |
| Redis | 多实例时用于会话状态、限流和 token 预算共享 |

## 配置文件边界

| 文件 | 说明 |
|---|---|
| [marketplace 在线体验配置示例](./marketplace.env.example.md) | marketplace 侧在线体验开关、runner 地址和配额配置 |
| `skill-runner.env.example` | skill-runner 专属配置，如 LLM、executor、K8s、worker pod、Redis |

## 文档列表

| 文档 | 说明 |
|---|---|
| [skill-runner 部署](./skill-runner部署.md) | 运行时控制面部署与 worker 镜像构建 |
| [LLM 代理与密钥配置](./LLM代理与密钥配置.md) | LLM Key 和代理边界 |
| [Redis 多实例配置](./Redis多实例配置.md) | 多实例在线体验状态共享 |
| [K8s ConfigMap](../../../../../docker/k8s/skill-runner-config.yaml) | skill-runner K8s 部署的可选配置示例 |

## 启用顺序

启用步骤（镜像构建、LLM 配置、Secret、开关与验证）见 [K8s 方式安装指导](../../../3.%20安装指导/K8s方式安装/SkillHub安装指导.md) 第 9.4 节。
