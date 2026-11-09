# Agent资产审核机制

七类 Agent 资产（Skill、SwarmSkill、插件、连接器、专家、专家团、系统插件）都要审核通过后，才会出现在公开市场并允许下载。审查只发生在 Skill 和 SwarmSkill 上。

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
| 发布校验 | 七类资产 | 包结构、版本、图标、路径安全。失败则直接拒绝，不进后续环节 |
| 审查 | 仅 Skill、SwarmSkill | 规则或 AI 检查风险。开关 `MARKET_SKILL_REVIEW_ENABLED`，默认关闭 |
| 审核 | 七类资产 | 审核员通过或驳回。驳回必填原因 |
| 市场展示 | 七类资产 | 只展示审核通过的版本 |

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

系统 Token（`X-System-Token`，对应系统管理员）发布七类资产都直接 `APPROVED`，跳过审查和审核。

## SkillPack 兼容发布

SkillPack 首版仍使用普通 `skill` 分类，沿用现有审查、审核、展示、检索和下载流程。
Hub 读取包内容根目录 `SKILL.md` 的 YAML 头；遇到 `kind: skillpack`，额外校验同级
`skillpack.lock.json`，不依赖标签判断成员是否合法。

- `SKILL.md.skills` 与 lock 的成员名称、数量和顺序必须一致；lock 使用
  `openjiuwen.skillpack.lock/v1`，Pack 名称、版本须与本次发布一致。
- 每个成员通过 `name`、`asset_id`、精确 `version` 和 `artifact_sha256` 锁定。
  成员必须是当前 Hub 中公开、可下载的普通单 Agent Skill；不允许 SwarmSkill 或嵌套 SkillPack。
  审核状态按锁定版本检查，不以其他版本已通过代替。
- `artifact_sha256` 对照成员原始制品接口（`is_cli_download=true`）返回的存储 ZIP 校验，
  不是解包内容或下载时生成的 `.raw.zip` 的摘要。
  Pack ZIP 只携带执行说明和锁文件，不携带成员源码。
- 上传时校验失败不写入 Pack 制品；审核员点击通过前重新检查成员，失败时不改变待审状态。
  系统管理员免审发布、同制品重试、Git 或批量导入也不能绕过发布校验。

例如，Pack 引用 `report-skill@1.0.0`，即使 `2.0.0` 已通过审核，`1.0.0` 未通过、
被删除或原始 ZIP 摘要不一致，仍不能发布该 Pack。WorkSwarm 可先发布缺失成员，
等成员审核通过后再提交 Pack。

上传校验和仍校验接收到的 ZIP；若发布表单覆盖元数据或自动包装改变了 ZIP，版本记录和
存储对象摘要使用最终保存、可通过原始制品接口下载的 ZIP。旧版本若曾被包装但仍记录
包装前摘要，不自动修改历史数据，需以新版本重新发布后引用。

这不是持续依赖监控：已上架 Pack 的成员后续变化仍需消费端在安装时重新校验。
本期不增加原生 `skillpack` 分类、独立页面或成员删除的反向引用保护。

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

`GET /api/v1/auth/me` 返回 `is_market_moderation_admin: true` 时，前端显示审核菜单。可审核的类型是 Skill、SwarmSkill、插件、连接器、专家、专家团、系统插件。

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
