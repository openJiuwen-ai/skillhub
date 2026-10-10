# Redis 多实例配置

Redis 用于多实例场景下共享会话状态、创建锁、限流计数或索引热加载通知。

## 适用场景

- marketplace 多副本部署。
- skill-runner 多副本部署。
- 需要跨进程共享在线体验会话状态。
- 检索索引热加载需要广播。

## 配置

### marketplace 侧（`.env`）

```env
PLAYGROUND_MULTI_INSTANCE=true
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
MARKET_REDIS_PASSWORD=your-redis-password
```

### skill-runner 侧（`skill-runner.env`）

```env
SKILL_RUNNER_MULTI_INSTANCE=true
SKILL_RUNNER_REDIS_HOST=127.0.0.1
SKILL_RUNNER_REDIS_PORT=6379
SKILL_RUNNER_REDIS_DB=0
SKILL_RUNNER_REDIS_PASSWORD=your-redis-password
```

> 两侧 Redis 配置必须指向同一实例，否则会话状态无法共享。

## 配置要点

1. Redis 地址应对所有 marketplace / skill-runner 实例可达。
2. 生产环境应配置认证和网络隔离。
3. 超时策略应避免阻塞核心请求链路。
4. 多实例部署时，应确认会话路由和状态 TTL。

## 验证项

- 多副本下创建会话不会重复扣配额。
- 后续消息能路由到正确实例。
- 限流和 token 预算能跨实例生效。
- Redis 故障时系统有降级或告警策略。
