# Agent资产审核机制

六类 Agent 资产（Skill、SwarmSkill、插件、连接器、专家、专家团）都要审核通过后，才会出现在公开市场并允许下载。审查只发生在 Skill 和 SwarmSkill 上。

## 两条路径

Skill / SwarmSkill，且 `MARKET_SKILL_REVIEW_ENABLED=true`：

```text
发布校验 -> 审查 -> 审核 -> 市场展示
```

插件、连接器、专家、专家团，以及审查关闭时的 Skill / SwarmSkill：

```text
发布校验 -> 审核 -> 市场展示
```

| 阶段 | 谁会遇到 | 说明 |
|------|----------|------|
| 发布校验 | 六类资产 | 包结构、版本、图标、路径安全。失败则直接拒绝，不进后续环节 |
| 审查 | 仅 Skill、SwarmSkill | 规则或 AI 检查风险。开关 `MARKET_SKILL_REVIEW_ENABLED`，默认关闭 |
| 审核 | 六类资产 | 审核员通过或驳回。驳回必填原因 |
| 市场展示 | 六类资产 | 只展示审核通过的版本 |

插件、连接器、专家、专家团没有审查记录，发布成功后就是 `pending_moderation`。

## 审核状态

版本级状态：

| 状态 | 代码 | 公开市场可见 | 说明 |
|------|------|:------------:|------|
| 待审核 | `PENDING` | 否 | 等待审核员处理 |
| 已通过 | `APPROVED` | 是 | 可列表、可下载 |
| 已驳回 | `REJECTED` | 否 | 发布者修改后发新版本 |

## 发布结果

审查开启时，普通用户发布 Skill / SwarmSkill：

```text
审查中（reviewing）
  -> 审查通过 -> 审核中（pending_moderation）
  -> 审查不通过 -> 发布失败（publish_failed），不进入审核
审核中
  -> 通过 -> 发布成功（publish_success）
  -> 驳回 -> 发布失败，可发新版本
```

审查关闭，或发布的是插件、连接器、专家、专家团：

```text
审核中（pending_moderation）
  -> 通过 -> 发布成功（publish_success）
  -> 驳回 -> 发布失败
```

审查中的 Skill / SwarmSkill 不能点审核通过。接口会返回 `invalid_moderation_state`。

系统 Token（`X-System-Token`，对应系统管理员）发布六类资产都直接 `APPROVED`，跳过审查和审核。

## 可见性

- 访客只能看到已通过的对外版本。待审新版本不会换掉已经公开的版本。
- 从未通过审核的资产不出现在公开列表，只在发布者个人中心和审核待办里。
- 发布者能看到自己的待审和驳回版本。
- 审核员能看待审队列和审核记录。

## 审核管理员

`.env` 里 `MARKET_REVIEW_ADMIN_USERNAMES` 与 GitCode / GitHub 登录名精确匹配，区分大小写。改完重启 marketplace。

```env
MARKET_REVIEW_ADMIN_USERNAMES=alice,bob
```

`GET /api/v1/auth/me` 返回 `is_market_moderation_admin: true` 时，前端显示审核菜单。可审核的类型是 Skill、SwarmSkill、插件、连接器、专家、专家团。

默认禁止审核自己发布的资产。内部单人维护可打开：

```env
MARKET_ALLOW_SELF_MODERATION=true
```

- 打开后仍要本人在审核管理员名单里，不会因此获得审核资格。
- 只放行 `self_moderation_forbidden`。状态、阶段和可见性不变。
- 默认 `false`。改完重启 marketplace。

## 相关文档

- [Agent资产](./Agent资产.md)
- [角色与权限](../4.%20用户指南/角色与权限.md)
- [openJiuwen Agentic Hub 接口参考](../7.%20API参考/openJiuwen-Agentic-Hub-接口参考.md)
- [环境配置说明](../4.%20用户指南/环境配置说明.md)
