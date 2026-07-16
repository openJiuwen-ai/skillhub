# Skill 审核机制

SkillHub 通过审核流程控制 Skill 的市场可见性和在线体验准入。

## 审核阶段

```text
发布校验 -> 系统审查（可选）-> 人工审核 -> 市场展示
```

| 阶段 | 说明 |
|---|---|
| 发布校验 | 校验包结构、版本、图标、元数据等基础约束。失败则发布直接拒绝。 |
| 系统审查 | 可选阶段。使用规则或 AI 模型辅助检查 Skill 风险。需 `MARKET_SKILL_REVIEW_ENABLED=true` |
| 人工审核 | 审核员决定通过或驳回。驳回必填原因。 |
| 市场展示 | 仅审核通过的版本对外展示。 |

## 审核状态

版本级审核状态码：

| 状态 | 代码 | 公开市场可见 | 说明 |
|------|------|:------------:|------|
| 待审核 | `PENDING` | 否 | 等待人工审核 |
| 已通过 | `APPROVED` | 是 | 对外展示，可下载 |
| 已驳回 | `REJECTED` | 否 | 发布者需修改后发新版本 |

## 发布结果流转

当 `MARKET_SKILL_REVIEW_ENABLED=true` 时：

```text
发布 -> 系统审查中（reviewing）
     -> 系统审查通过 -> 人工审核中（pending_moderation）
     -> 人工审核通过 -> 发布成功（publish_success）
     -> 人工审核驳回 -> 发布失败（可发新版本重试）
```

当 `MARKET_SKILL_REVIEW_ENABLED=false`（默认）时：

```text
发布 -> 人工审核中（pending_moderation）
     -> 审核通过 -> 发布成功（publish_success）
     -> 审核驳回 -> 发布失败
```

### 例外：系统 Token 发布

携带有效 `X-System-Token` 的发布请求直接 `APPROVED`，跳过系统审查与人工审核。

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

## 相关文档

- [角色与权限](../4.%20用户指南/角色与权限.md)
- [TeamSkillsHub 接口参考](../7.%20API参考/TeamSkillsHub-接口参考.md)
- [环境配置说明](../4.%20用户指南/环境配置说明.md)
