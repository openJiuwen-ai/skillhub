# LLM 代理与密钥配置

在线体验执行 Skill 时需要调用 LLM。安全边界是：真实 LLM Key 留在 skill-runner 控制面，worker 只拿会话 token。

## 配置边界

| 位置 | 存放内容 |
|---|---|
| `skill-runner.env.example` 派生配置 | `SKILL_RUNNER_LLM_*`、模型、超时、executor 等 |
| worker pod 环境变量 | 会话 token、代理地址、模型名等运行时注入信息 |
| marketplace `.env` | 只保存是否启用在线体验、skill-runner 地址和配额等代理配置 |

## 安全要求

- 不要把真实 LLM Key 注入 worker pod。
- 不要把 LLM Key 写进前端配置。
- K8s 生产环境建议使用 Secret 管理 LLM Key。
- 日志应避免输出 token、API Key、Authorization 等敏感字段。

## 验证项

1. skill-runner 能访问上游 LLM。
2. worker 能访问 skill-runner 内部 LLM 代理。
3. worker 环境变量中没有真实 LLM Key。
4. token 失效后 worker 无法继续调用代理。
