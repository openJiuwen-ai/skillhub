# 在线体验 API

在线体验 API 由 marketplace 对外暴露，并转发到独立的 skill-runner 服务。

> **前置条件**：服务端需配置 `PLAYGROUND_ENABLED=true`，否则 `/api/v1/playground/*` 路由不注册，返回 404。

## 端点速查

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/v1/playground/sessions` | 创建体验会话 | Bearer |
| POST | `/api/v1/playground/sessions/{session_id}/messages` | 发送消息 | Bearer |
| GET | `/api/v1/playground/sessions/{session_id}/stream?pt={proxy_token}` | SSE 流式接收输出 | 会话能力令牌 `pt` |
| POST | `/api/v1/playground/sessions/{session_id}/files` | 上传临时文件 | Bearer |
| DELETE | `/api/v1/playground/sessions/{session_id}` | 结束会话 | Bearer |

> 具体请求/响应字段以部署版本的路由实现为准。以下为能力概览。
> `proxy_token` 由创建会话接口返回。浏览器 `EventSource` 无法设置 `Authorization` 请求头，因此 SSE 连接必须通过 `pt` 查询参数携带该令牌。

## 能力说明

| 能力 | 说明 |
|---|---|
| 创建会话 | 创建一次在线体验会话，并注入审核通过的 Skill 内容 |
| 发送消息 | 向已有会话发送用户输入 |
| SSE 流 | 接收模型输出、推理过程、工具调用和最终答复 |
| 上传文件 | 向当前会话上传临时输入文件 |
| 结束会话 | 主动结束会话并释放运行资源 |

## SSE 事件类型

| 事件 | 何时触发 |
|------|----------|
| `ready` | 会话创建成功 |
| `text` / `reasoning` | LLM 流式增量输出 |
| `tool_call` / `tool_result` | 沙箱内工具调用 |
| `answer` | 一轮最终答复 |
| `done` | 一轮结束（连接不关闭，支持多轮） |
| `error` | 单轮错误 |
| `session_ended` | 会话被删除，连接关闭 |

## 安全约束

- 客户端不应直接提交 `skill_md`、`system_prompt` 等执行内容。
- marketplace 负责鉴权、配额、审核状态检查和内容注入。
- skill-runner 负责会话编排和运行时隔离。
- 每用户每自然日有配额限制（`PLAYGROUND_DAILY_LIMIT`，默认 20），管理员不受限。

## 相关文档

- [在线体验使用指南](../4.%20用户指南/在线体验使用指南.md)
- [在线体验运行时](../5.%20开发指南/在线体验运行时.md)
- [skill-runner 部署](../6.%20运维指南/可选能力/在线体验/skill-runner部署.md)

