# Skill 审核机制

SkillHub 通过审核流程控制 Skill 的市场可见性和在线体验准入。

## 审核阶段

```text
发布校验 -> 审查（可选）-> 审核 -> 市场展示
```

| 阶段 | 说明 |
|---|---|
| 发布校验 | 校验包结构、版本、图标、元数据等基础约束。失败则发布直接拒绝。 |
| 审查 | 可选阶段。使用规则或 AI 模型辅助检查 Skill 风险。需 `MARKET_SKILL_REVIEW_ENABLED=true` |
| 审核 | 审核员决定通过或驳回。驳回必填原因。 |
| 市场展示 | 仅审核通过的版本对外展示。 |

## 审核状态

版本级审核状态码：

| 状态 | 代码 | 公开市场可见 | 说明 |
|------|------|:------------:|------|
| 待审核 | `PENDING` | 否 | 等待审核 |
| 已通过 | `APPROVED` | 是 | 对外展示，可下载 |
| 已驳回 | `REJECTED` | 否 | 发布者需修改后发新版本 |

## 发布结果流转

当 `MARKET_SKILL_REVIEW_ENABLED=true` 时：

```text
发布 -> 审查中（reviewing）
     -> 审查通过 -> 审核中（pending_moderation）
     -> 审核通过 -> 发布成功（publish_success）
     -> 审核驳回 -> 发布失败（可发新版本重试）
```

当 `MARKET_SKILL_REVIEW_ENABLED=false`（默认）时：

```text
发布 -> 审核中（pending_moderation）
     -> 审核通过 -> 发布成功（publish_success）
     -> 审核驳回 -> 发布失败
```

### 例外：系统 Token 发布

携带有效 `X-System-Token` 的发布请求直接 `APPROVED`，跳过审查与审核。

## 可见性原则

- 普通用户只能看到已通过审核的对外版本。
- 发布者可在个人中心查看自己的全部版本状态（含待审/驳回）。
- 审核员可查看所有待审内容和审核详情。
- 在线体验通常只允许审核通过的版本。
- 待审新版本不会覆盖已公开的稳定版本。

## 审核管理员配置

审核管理员由 `.env` 中 `MARKET_REVIEW_ADMIN_USERNAMES` 配置，与 GitCode/GitHub **登录名精确匹配**（区分大小写）。修改后须重启 marketplace 服务。

```env
MARKET_REVIEW_ADMIN_USERNAMES=alice,bob
```

登录后调用 `/api/v1/auth/me`，若返回 `is_market_moderation_admin: true`，前端侧栏即显示审核菜单。

## 允许审核员审核自己发布的 Skill

默认禁止审核员审核自己发布的 Skill，避免自审自批。如需在内部测试、单人维护仓库等场景放行此限制，在 `.env` 中设置：

```env
MARKET_ALLOW_SELF_MODERATION=true
```

注意事项：

- **不绕过权限校验**：发布者仍须本身是审核管理员（已配置在 `MARKET_REVIEW_ADMIN_USERNAMES` 中），系统不会隐式授予审核资格；未配置者置 `true` 也不会获得审核能力。
- **安全优先**：默认值为 `false`，生产环境不建议开启，避免利益冲突与合规风险。
- **仅影响 `self_moderation_forbidden` 单一拦截**：其余审核状态、阶段、可见性规则保持不变。
- 修改后须 **重启 marketplace 服务** 才能生效。

## 相关文档

- [角色与权限](../4.%20用户指南/角色与权限.md)
- [TeamSkillsHub 接口参考](../7.%20API参考/TeamSkillsHub-接口参考.md)
- [环境配置说明](../4.%20用户指南/环境配置说明.md)
