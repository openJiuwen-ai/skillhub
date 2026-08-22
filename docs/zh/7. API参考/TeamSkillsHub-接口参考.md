# TeamSkillsHub API 接口参考

面向 **Web / CLI / 服务端集成** 的 HTTP API 说明。文首 **端点速查表** 汇总方法、路径、主要参数与鉴权；下文按模块展开请求示例与错误规则。

> **OpenAPI YAML**（Swagger / codegen）：见 [TeamSkillsHub.md](./TeamSkillsHub.md) 文末。
> **ClawHub 兼容层**详述见 [ClawHub兼容层.md](./ClawHub兼容层.md)。

---

## 端点速查表（Quick reference）

路径均相对于 `{base}/api/v1`。`{provider}` = `gitcode` | `github`；`{asset_id}` = Skill 资产 ID（ClawHub 兼容层中亦称 `{slug}`，二者等价）；`{version}` = 语义化版本如 `1.0.0`。

### 原生 API

| 方法 | 路径 | 主要参数 | 鉴权 | 最低角色 |
|------|------|----------|------|----------|
| **认证** | | | | |
| GET | `/auth/oauth/{provider}/start` | 路径：`provider` | — | — |
| GET | `/auth/oauth/{provider}/callback` | Query：`code`✱、`state`✱ | — | — |
| POST | `/auth/oauth/{provider}/session` | Body：`oauth_session`✱ | — | — |
| GET | `/auth/me` | Header：`Authorization`✱ | Bearer | 登录用户 |
| **Skill 核心** | | | | |
| GET | `/plugins` | Query：`page`、`page_size`、`asset_id`、`publisher_id`、`publisher_name`、`category_id`、`plugin_type`、`plugin_type_exclude`、`search_keyword`、`moderation_status`、`tags`、`tags_match`、`order_by`、`desc` | 可选 Bearer | — |
| GET | `/plugins/tags` | Query：`plugin_type`、`limit`（1–100，默认 20） | — | — |
| GET | `/plugins/{asset_id}/versions/{version}` | 路径：`asset_id`、`version` | 可选 Bearer | — |
| GET | `/plugins/{asset_id}/versions/{version}/files` | 路径：`asset_id`、`version`；Query：`with_content` | 可选 Bearer | — |
| GET | `/artifacts/{id}` | 路径：`id`；Query：`version`、`is_cli_download` | 可选 Bearer | — |
| POST | `/plugins` | Header：`X-Checksum-SHA256`✱；Form：`file`✱、`plugin_id`、`plugin_version`、`version_desc`、`force` | Bearer **或** System Token（二选一） | 登录用户 / 系统 |
| GET | `/plugins/publish-template` | Query：`kind`（`plugin` \| `skill` \| `swarmskill`） | Bearer **或** System Token | 登录用户 / 系统 |
| DELETE | `/plugins/{asset_id}/versions/{version}` | 路径：`asset_id`、`version`（`all`=删全部） | Bearer **或** System Token | 发布者 / 系统 |
| POST | `/plugins/skill-import` | Header：`X-Checksum-SHA256`✱；Form：`file`✱、`force`、`fail_fast` | 仅 System Token | 系统 |
| **Git 源** | | | | |
| GET | `/plugins/git-sources` | — | Bearer **或** System Token | 登录用户 / 系统 |
| POST | `/plugins/git-sources` | Body：`repo_url`✱、`ref`、`skills_subpath`、`name` | Bearer **或** System Token | 登录用户 / 系统 |
| POST | `/plugins/git-sources/{source_id}/sync` | 路径：`source_id` | Bearer **或** System Token | 源属主 / 系统 |
| DELETE | `/plugins/git-sources/{source_id}` | 路径：`source_id` | Bearer **或** System Token | 源属主 / 系统 |
| **群组** | | | | |
| POST | `/groups` | Body：`name`✱、`description`、`visibility` | Bearer **或** System Token | 登录用户 / 系统 |
| GET | `/groups/my` | Query：`page`、`page_size`、`keyword`、`role`、`sort` | Bearer **或** System Token | 登录用户 / 系统 |
| GET | `/groups/my/skills` | Query：`page`、`page_size`、`keyword` | Bearer **或** System Token | 登录用户 / 系统 |
| GET | `/groups/discover` | Query：`page`、`page_size`、`keyword`、`filter_by`、`sort` | Bearer **或** System Token | 登录用户 / 系统 |
| GET | `/groups/grantable-skills` | Query：`page`、`page_size`、`keyword`、`group_id` | Bearer **或** System Token | Skill 发布者 / 系统 |
| GET | `/groups/{group_id}` | 路径：`group_id` | Bearer **或** System Token | 可查看群组的用户 / 系统 |
| PATCH | `/groups/{group_id}` | 路径：`group_id`；Body：`name`、`description`、`visibility` | Bearer **或** System Token | 群主 / 系统 |
| DELETE | `/groups/{group_id}` | 路径：`group_id` | Bearer **或** System Token | 群主 / 系统 |
| GET | `/groups/{group_id}/members` | 路径：`group_id`；Query：`page`、`page_size` | Bearer **或** System Token | 群成员 / 系统 |
| PUT | `/groups/{group_id}/members` | 路径：`group_id`；Body：`user_id`✱、`user_name`、`role` | Bearer **或** System Token | 群主 / 系统 |
| DELETE | `/groups/{group_id}/members/{user_id}` | 路径：`group_id`、`user_id` | Bearer **或** System Token | 本人或群主 / 系统 |
| POST | `/groups/{group_id}/join-requests` | 路径：`group_id`；Body：`message` | Bearer **或** System Token | 登录用户 / 系统 |
| GET | `/groups/{group_id}/join-requests` | 路径：`group_id`；Query：`page`、`page_size`、`status` | Bearer **或** System Token | 群主 / 系统 |
| POST | `/groups/{group_id}/join-requests/{request_id}/decision` | 路径：`group_id`、`request_id`；Body：`status`✱ | Bearer **或** System Token | 群主 / 系统 |
| GET | `/groups/{group_id}/grants` | 路径：`group_id`；Query：`page`、`page_size`、`status` | Bearer **或** System Token | 可查看群组的用户 / 系统 |
| POST | `/groups/{group_id}/grants` | 路径：`group_id`；Body：`asset_id`✱ | Bearer **或** System Token | Skill 发布者 / 系统 |
| POST | `/groups/{group_id}/grants/{asset_id}/decision` | 路径：`group_id`、`asset_id`；Body：`status`✱ | Bearer **或** System Token | 群主 / 系统 |
| DELETE | `/groups/{group_id}/grants/{asset_id}` | 路径：`group_id`、`asset_id` | Bearer **或** System Token | 群主或 Skill 发布者 / 系统 |
| **审核** | | | | |
| POST | `/plugins/{asset_id}/moderation` | 路径：`asset_id`；Body：`action`✱（`approve`\|`reject`）、`version`、`reason`（reject 时✱） | Bearer | 审核管理员 |
| GET | `/plugins/audit/skill-moderation` | Query：`page`、`page_size` | Bearer | 审核管理员 |
| **互动** | | | | |
| POST | `/plugins/{asset_id}/view` | 路径：`asset_id` | — | — |
| POST | `/plugins/{asset_id}/interact` | 路径：`asset_id`；Body：`action_type`✱（`like`\|`star`） | Bearer | 登录用户 |
| GET | `/plugins/my/stars` | Query：`page`、`page_size` | Bearer | 登录用户 |
| GET | `/plugins/my/likes` | Query：`page`、`page_size` | Bearer | 登录用户 |
| GET | `/plugins/interactions/batch` | Query：`asset_ids`（重复传参，≤50） | 可选 Bearer | — |
| GET | `/plugins/{asset_id}/interactions` | 路径：`asset_id` | 可选 Bearer | — |
| **通知** | | | | |
| GET | `/notifications` | — | Bearer | 登录用户 |
| POST | `/notifications/read-all` | — | Bearer | 登录用户 |
| **审计** | | | | |
| GET | `/audit/logs` | Query：`date_from_ms`✱、`date_to_ms`✱、`keyword`、`resource_type`、`action`、`result`、`asset_plugin_type`、`source_channel`、`page`、`page_size` | Bearer **或** System Token | 审核管理员 / 系统 |
| GET | `/audit/logs/{event_id}` | 路径：`event_id` | Bearer **或** System Token | 审核管理员 / 系统 |
| GET | `/audit/stats` | Query：同 `/audit/logs` 过滤项 | Bearer **或** System Token | 审核管理员 / 系统 |
| GET | `/audit/logs/export` | Query：同 `/audit/logs` 过滤项 | Bearer **或** System Token | 审核管理员 / 系统 |
| **站点** | | | | |
| GET | `/site/config` | — | — | — |
| GET | `/site/privacy-statement` | — | — | — |
| **GitHub 标星** | | | | |
| POST | `/github/watch` | Body：`repos`（空数组=一键标星核心仓库） | Bearer | GitHub 用户 |
| GET | `/github/watch/status` | - | Bearer | GitHub 用户 |

✱ = 必填。审计时间范围单次跨度 ≤ 90 天；导出 ≤ 5 万条。

### ClawHub 兼容层

须 `MARKET_CLAWHUB_COMPAT_ENABLED=true`。响应为 **裸 JSON**（无 `ResponseModel` 包装）；路由层不校验 Bearer。

| 方法 | 路径 | 主要参数 | 鉴权 | 最低角色 |
|------|------|----------|------|----------|
| GET | `/search` | Query：`q`✱、`limit` | — | — |
| GET | `/resolve` | Query：`slug`✱、`hash`✱（64 位 hex 指纹） | — | — |
| GET | `/skills` | Query：`limit`、`sort`（`updated` / `downloads` / `stars` 等） | — | — |
| GET | `/skills/{slug}` | 路径：`slug` | — | — |
| GET | `/skills/{slug}/versions` | 路径：`slug`；Query：`limit` | — | — |
| GET | `/skills/{slug}/versions/{version}` | 路径：`slug`、`version` | — | — |
| GET | `/skills/{slug}/file` | 路径：`slug`；Query：`path`✱、`version` | — | — |
| GET | `/download` | Query：`slug`✱、`version` | — | — |

详述见 [ClawHub兼容层.md](./ClawHub兼容层.md) 与下文 [附录](#附录clawhub-兼容层)。

---

## 快速开始

### Base URL

| 环境 | 示例 |
|------|------|
| 本地开发 | `http://127.0.0.1:8100` |
| 官方托管 | `https://swarmskills.openjiuwen.com` |
| 自建 | 由运维提供；须与前端反代目标（`BACKEND_URL` / `BACKEND_PORT`）一致 |

所有路径均相对于 `{base}/api/v1`。

### 统一响应（JSON API）

除 `GET /site/privacy-statement` 与 OAuth 302 重定向外，成功响应格式为：

```json
{
  "code": 200,
  "message": "ok",
  "data": { }
}
```

错误响应见 [TeamSkillsHub.md — 全局错误响应](./TeamSkillsHub.md#全局错误响应)。

### 鉴权请求头

| 头 | 用途 |
|----|------|
| `Authorization: Bearer <access_token>` | OAuth 用户令牌（GitCode / GitHub） |
| `X-System-Token: <token>` | 系统级令牌，**仅服务端**；与 Bearer **二选一** |
| `X-OAuth-Provider: gitcode` \| `github` | Bearer 校验厂商；缺省 `gitcode` |
| `X-Checksum-SHA256: <64位小写hex>` | 上传 zip 时 **必填** |

---

## Skill 可见性速查

| 调用者 | 公开市场列表 | 下载已通过版本 | 查看待审/驳回版本 | 人工审核 |
|--------|:------------:|:--------------:|:-----------------:|:--------:|
| 匿名 / 普通用户 | 仅已通过且存在对外版本 | ✓ | ✗（404） | ✗ |
| 发布者本人 | 个人中心可见全部自己的 Skill | 含待审版本 | ✓ | ✗ |
| 审核管理员 | 待办可见 PENDING/REJECTED | ✓ | ✓ | ✓ |
| System Token | 按 API 权限 | ✓ | ✓ | ✓ |

版本级审核状态：`PENDING`（人工审核中）、`APPROVED`（已通过）、`REJECTED`（已驳回）。
待审新版本在通过前，公开市场仍展示上一已通过版本（`public_latest_version`）。

---

## 认证授权

前缀：`/api/v1/auth`

---

### `GET /auth/oauth/{provider}/start`

启动浏览器 OAuth 流程，返回 **302** 重定向到 GitCode / GitHub 授权页。`state` 有效期约 10 分钟。

| 项 | 说明 |
|----|------|
| **鉴权** | 无需 |
| **路径参数** | `provider`：`gitcode` \| `github` |

```bash
# 浏览器访问
open "https://swarmskills.openjiuwen.com/api/v1/auth/oauth/gitcode/start"
```

---

### `GET /auth/oauth/{provider}/callback`

OAuth 厂商回调。用 `code` 换取 token，拉取用户信息，写入一次性 `oauth_session`，**302** 重定向到前端 `/login?oauth_session=...`。

| Query | 必填 | 说明 |
|-------|:----:|------|
| `code` | ✓ | 授权码 |
| `state` | ✓ | 与 start 时一致 |
| `error` | | 用户拒绝授权等 |

**失败时** 不返回 JSON，而是 302 到登录页并在 query 中附带 `oauth_error`、`oauth_error_code` 等，详见 [OAuth 回调错误](./TeamSkillsHub.md#oauth-回调重定向错误)。

---

### `POST /auth/oauth/{provider}/session`

前端用一次性 session 兑换长期 `access_token` 与用户信息。

**请求体**

```json
{
  "oauth_session": "从 /login?oauth_session= 取得"
}
```

**响应 `200` — `data` 示例**

```json
{
  "access_token": "xxx",
  "token_type": "bearer",
  "user": {
    "id": "692f9cb43e517c5e152974a0",
    "login": "alice",
    "name": "Alice",
    "avatar_url": "https://...",
    "is_market_moderation_admin": false
  }
}
```

---

### `GET /auth/me`

校验当前 Bearer，返回用户信息与是否为审核管理员。

**请求头**

```http
Authorization: Bearer <access_token>
X-OAuth-Provider: gitcode
```

**响应 `data` 字段**

| 字段 | 说明 |
|------|------|
| `id` | 厂商用户 ID |
| `login` | 登录名 |
| `name` | 显示名 |
| `avatar_url` | 头像 URL |
| `is_market_moderation_admin` | 是否为审核管理员 |

---

## Skill 核心资源

前缀：`/api/v1/plugins`、`/api/v1/artifacts`

---

### `GET /plugins`

返回 Skill / Swarm Skill 分页列表。传入 `search_keyword` 时走语义检索（检索文本已拼接标签，输入标签文字即可命中）；未传关键词且 `order_by=recommend`、**不带** `category_id`、并已启用推荐时走「推荐精选」个性化排序（一次最多 `MARKET_REC_LIST_TOP_K` 条，再按 `page` 切片；`total` 为过滤 `OFFLINE` 后的条数）。带 `category_id` 时即使 `order_by=recommend` 也按 `install_count` 查表。市场前端侧边栏精选数量不调用本参数，用已上架数与 `GET /site/config` 的 `rec_list_top_k` 的较小值。可选 Bearer 用于发布者/审核员个性化字段。

**Query 参数**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | `1` | 页码 |
| `page_size` | int | `20` | 每页条数（1–200） |
| `asset_id` | string | — | 精确资产 ID |
| `publisher_id` | string | — | 发布者 ID（查「我的 Skills」时传当前用户 `id`） |
| `publisher_name` | string | — | 发布者名称模糊匹配 |
| `category_id` | string | — | 分类 ID（精确匹配；与 `order_by=recommend` 同时出现时回退下载量排序） |
| `plugin_type` | string | — | `skill`、`swarmskill`；可逗号多值 |
| `plugin_type_exclude` | string | — | 排除某类型 |
| `search_keyword` | string | — | 语义搜索关键词 |
| `moderation_status` | string | — | `PENDING` \| `APPROVED` \| `REJECTED` |
| `tags` | string | - | 按标签过滤，逗号分隔（如 `python,cli`）；标签内不能含逗号（发布校验同口径）。长度上限 512 字符（超出 422），超过 20 个标签静默截断 |
| `tags_match` | string | `all` | 标签匹配模式：`all`=同时包含全部标签（子集）；`any`=包含任一标签（交集） |
| `order_by` | string | `install_count` | 排序字段；`recommend` 仅无 `category_id`、无搜索词且 `MARKET_RECOMMENDER_ENABLED=true` 时生效，否则回退 `install_count` |
| `desc` | bool | `true` | 是否降序 |

**示例 — 公开市场**

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins?plugin_type=skill,swarmskill&page=1&page_size=20"
```

**示例 — 我的 Skills**

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins?publisher_id=YOUR_USER_ID&plugin_type=skill" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**示例 - 按标签过滤（同时含 python 和 cli）**

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins?plugin_type=skill&tags=python,cli&tags_match=all"
```

**响应 `200` — `data` 结构**

```json
{
  "page": 1,
  "page_size": 20,
  "total": 100,
  "items": [
    {
      "asset_id": "482becff9f044ba9bad9caef2e43b539",
      "name": "my-demo-skill",
      "display_name": "演示 Skill",
      "plugin_type": "skill",
      "moderation_status": "APPROVED",
      "latest_version": "1.1.0",
      "public_latest_version": "1.0.0",
      "view_count": 42,
      "install_count": 10,
      "like_count": 3,
      "star_count": 1
    }
  ]
}
```

发布者/审核员在 `items` 中可能额外看到 `all_versions`、`version_moderation_map` 等字段；匿名用户不会暴露待审新版本号。

---

### `GET /plugins/tags`

市场标签筛选 chips 数据源：按使用次数返回热门标签 `(tag, count)` 列表。可见性与市场列表同口径（排除 OFFLINE，匿名访客仅公开可见资产），`count=0` 的标签不返回，避免点击后空结果。

运营可通过 `MARKET_FEATURED_TAGS`（逗号分隔）配置优先展示的标签：配置中的标签按配置顺序排前，其余按使用次数降序补齐至 `limit`。

**Query 参数**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `plugin_type` | string | - | 限定插件类型（如 `skill` / `swarmskill`），缺省为全部类型 |
| `limit` | int | `20` | 返回的标签数量上限（1–100） |

**示例**

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins/tags?plugin_type=skill&limit=20"
```

**响应 `200` - `data` 结构**

```json
[
  { "tag": "python", "count": 12 },
  { "tag": "cli", "count": 7 }
]
```

---

### `GET /plugins/{asset_id}/versions/{version}`

返回指定版本的元数据、changelog、系统审查摘要（若启用）等。

| 项 | 说明 |
|----|------|
| **鉴权** | 可选 Bearer |
| **路径参数** | `asset_id`：资产 ID；`version`：如 `1.0.0` |

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins/{asset_id}/versions/1.0.0"
```

#### 特殊可见性规则

| 条件 | 状态码 | 说明 |
|------|--------|------|
| 资产不存在 | `404` | 不返回详情 |
| Skill 未对外可见，且调用者非发布者/审核员 | `404` | 不泄露待审 Skill 存在性 |
| 版本不存在 | `404` | — |
| 版本待审/驳回，且调用者非发布者/审核员 | `404` | — |
| 发布者或审核管理员 | `200` | 可见含 PENDING/REJECTED 的版本 |

---

### `GET /plugins/{asset_id}/versions/{version}/files`

返回版本 zip 包内文件树；可通过 `with_content` 附带单个文本文件内容。

| Query | 说明 |
|-------|------|
| `with_content` | 包内相对路径，如 `SKILL.md`（仅文本文件） |

可见性规则同 [版本详情](#get-pluginsasset_idversionsversion)。

---

### `GET /artifacts/{id}`

返回下载信息：预签名 URL、checksum、文件大小等。可选 Bearer 用于记录下载用户（无效 token 会被忽略，仍可下载）。

| Query | 默认 | 说明 |
|-------|------|------|
| `version` | 最新对外版本 | 如 `1.0.0` |
| `is_cli_download` | `false` | `true` 时返回 CLI 原始 zip |

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/artifacts/{asset_id}?version=1.0.0"
```

**响应 `data` 关键字段：** `download_url`、`version`、`checksum_sha256`、`file_size`、`name`

| 条件 | 状态码 | 说明 |
|------|--------|------|
| 目标版本未通过审核（非发布者/审核员） | `404` | — |

---

### `POST /plugins`

发布 Skill（multipart zip）。须携带 `X-Checksum-SHA256`；鉴权为 Bearer **或** X-System-Token **二选一**。

**请求头**

```http
Authorization: Bearer <token>
X-Checksum-SHA256: a1b2c3d4e5f6...（64 位小写十六进制）
Content-Type: multipart/form-data
```

**Form 字段**

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `file` | ✓ | `.zip` Skill 包 |
| `plugin_id` | | 已有 Skill 发新版时填 `asset_id`；首次发布不传 |
| `plugin_version` | | 如 `1.0.0`（不含 `v` 前缀）；缺省从包内 `plugin.yaml` 读取 |
| `version_desc` | | 版本更新说明 |
| `force` | | `true` 强制覆盖同版本 |

**示例**

```bash
curl -X POST "https://swarmskills.openjiuwen.com/api/v1/plugins" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "X-Checksum-SHA256: $(sha256sum skill.zip | awk '{print $1}')" \
  -F "file=@skill.zip" \
  -F "plugin_version=1.0.0" \
  -F "version_desc=首次发布"
```

**响应 `200` — `data` 示例**

```json
{
  "plugin_id": "482becff9f044ba9bad9caef2e43b539",
  "name": "my-demo-skill",
  "version": "1.0.0",
  "plugin_type": "skill",
  "publish_result": "pending_moderation",
  "moderation_status": "PENDING"
}
```

`publish_result` 典型流转：`reviewing`（系统审查）→ `pending_moderation`（人工审核）→ `success` / `publish_failed`。

**常见错误**

| 状态码 | error | 说明 |
|--------|-------|------|
| `400` | `checksum_required` | 缺少 `X-Checksum-SHA256` 或格式非法（须 64 位小写 hex） |
| `400` | `checksum_mismatch` | 校验和不匹配 |
| `400` | `invalid_version` | `plugin_version` 或下载 `version` 参数格式错误（须 x.y.z，不含 v 前缀） |
| `422` | `manifest_validation_failed` | 同名多插件未指定 plugin_id；或 plugin_id 与包内信息不一致等业务校验失败 |
| `409` | `version_conflict` | 同版本已存在（可 `force=true`） |
| `409` | `skill_limit_exceeded` | 发布数量超限 |

---

### `GET /plugins/publish-template`

返回发布页「下载模板」的预签名 GET URL。

| Query | 说明 |
|-------|------|
| `kind` | 不传或 `plugin` → 插件模板；`skill` / `swarmskill` → Skill 模板 |

**响应 `data`：** `download_url`、`expires_in`、`filename`

未配置模板对象时返回 `503`（`template_not_configured`）。

---

### `DELETE /plugins/{asset_id}/versions/{version}`

删除指定版本。仅 **发布者本人** 或 **System Token** 可调用。

⚠️ `version=all` 将 **不可逆** 删除该资产全部版本及对象存储文件。

```bash
curl -X DELETE "https://swarmskills.openjiuwen.com/api/v1/plugins/{asset_id}/versions/1.0.0" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

### `POST /plugins/skill-import`

批量导入 Skill 集合包。**仅 X-System-Token**，且须 `X-Checksum-SHA256`。

| Form | 说明 |
|------|------|
| `file` | zip 集合包 |
| `force` | 是否强制覆盖 |
| `fail_fast` | 遇错是否立即停止 |

---

## Git 源

前缀：`/api/v1/plugins/git-sources`

创建与同步为 **后台任务**：POST 立即返回 `status: syncing`，客户端应轮询 `GET /plugins/git-sources` 查看 `last_index_status`（`syncing` / `success` / `partial_failure` / `failed`）。

---

### `GET /plugins/git-sources`

返回当前用户注册的 Git 源列表及最近同步状态。

| 项 | 说明 |
|----|------|
| **鉴权** | Bearer **或** System Token |

---

### `POST /plugins/git-sources`

创建 Git 源并触发首次后台同步。

**请求体**

```json
{
  "name": "",
  "repo_url": "https://github.com/org/skills-repo.git",
  "ref": "main",
  "skills_subpath": "skills/"
}
```

| 字段 | 说明 |
|------|------|
| `repo_url` | 公开 HTTPS 克隆地址（私仓不支持） |
| `ref` | 分支或 tag，默认 `main` |
| `skills_subpath` | 仓库内 Skill 根目录（服务端归一化），缺省为仓库根；同仓库不同路径可由不同用户分别注册 |

**响应 `200` — `data`**

```json
{
  "source_id": "abc123",
  "status": "syncing",
  "message": "Git 同步已在后台执行，请在列表中查看进度与结果"
}
```

**常见错误**

| 状态码 | 说明 |
|--------|------|
| `400` | URL 不安全、路径穿越等 |
| `409` | 同一仓库+分支+子路径已被全局注册 |
| `429` | 触发按用户限流 |

---

### `POST /plugins/git-sources/{source_id}/sync`

对已有 Git 源再次触发后台同步。仅 **源属主** 可调用。

再次同步时，**Skill 目录内容未变**的条目会跳过发布（即使仓库 HEAD 因其它路径前进）。

**响应 `data`：** 同创建，含 `source_id` 与 `status: syncing`。

---

### `DELETE /plugins/git-sources/{source_id}`

删除 Git 源注册，并 **级联删除** 该源已导入的全部 Skill（版本、审核、对象存储）。仅 **源属主** 可调用。删除后可再次以相同配置重新注册。

**响应 `200` — `data`**

```json
{
  "deleted": true,
  "deleted_skill_count": 3
}
```

| 字段 | 说明 |
|------|------|
| `deleted` | 固定 `true` |
| `deleted_skill_count` | 级联删除的 Skill 数量 |

**常见错误**：`403` 非属主；`409` 同步进行中。

---

## 群组管理

前缀：`/api/v1/groups`

群组是用户自治的协作空间，承接成员管理与 Skill 聚合。Skill 仍归原发布者所有，群组通过授权关系（grant）承接访问授权。特权用户（系统管理员/审核管理员）享有群主级操作权，即使未加入也可操作。

> 数量上限：每用户最多创建 20 个群组，每群组最多 500 个成员，特权用户豁免，超限 `409`。

---

### `POST /groups`

创建群组。创建者自动成为 owner。

**请求体**

```json
{
  "name": "研发组",
  "description": "研发团队 Skill 共享空间",
  "visibility": "listed"
}
```

| 字段 | 说明 |
|------|------|
| `name`✱ | 群组名称（1–128 字符） |
| `description` | 群组描述（≤4096 字符） |
| `visibility` | `private`（默认）\| `listed` |

**响应 `data`** - `GroupItem`：

```json
{
  "group_id": "grp_abc123",
  "name": "研发组",
  "description": "研发团队 Skill 共享空间",
  "owner_id": "692f9cb43e517c5e152974a0",
  "owner_name": "Alice",
  "visibility": "listed",
  "member_count": 1,
  "skill_count": 0,
  "viewer_role": "owner",
  "viewer_can_manage": true,
  "join_request_status": null,
  "create_time": 1722412800000,
  "update_time": 1722412800000
}
```

`viewer_can_manage`：当前用户能否管理该群组（群主或特权用户）。`viewer_role`：`owner` \| `member` \| `null`（未加入）。

| 状态码 | error | 说明 |
|--------|-------|------|
| `409` | `group_limit_exceeded` | 已达创建上限 |

---

### `GET /groups/my`

当前用户加入的群组列表。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `keyword` | - | 名称/描述模糊匹配 |
| `role` | - | `owner` \| `member` |
| `sort` | - | `updated` \| `members` \| `skills` \| `name` |

**响应 `data`**：`{ page, page_size, total, items: [GroupItem] }`

---

### `GET /groups/my/skills`

当前用户通过群组能用的 Skill（含成员可用 + 自己授权出去的）。仅返回已生效授权、未下架、有可用已通过审核版本的 Skill。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `keyword` | - | Skill 名称模糊匹配 |

**响应 `data` 示例**

```json
{
  "page": 1, "page_size": 20, "total": 5,
  "items": [{
    "group_id": "grp_abc123",
    "group_name": "研发组",
    "skill": { "asset_id": "482becff...", "name": "my-demo-skill", "display_name": "演示 Skill", "plugin_type": "skill" },
    "viewer_access_source": "owner"
  }]
}
```

`viewer_access_source`：`owner`（我授权的）\| `admin` \| `group`（组内可见）\| `public`。`owner` 来源的记录前端显示撤销入口。

---

### `GET /groups/discover`

发现可加入的群组。普通用户仅可见 `listed`；特权用户可发现全部（含 private）。关键字支持名称/描述模糊或 `group_id` 精确命中。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `keyword` | - | 名称/描述模糊或 group_id 精确匹配 |
| `filter_by` | - | `joined` \| `pending` \| `available` |
| `sort` | - | `updated` \| `members` \| `skills` \| `name` |

**响应 `data`**：`{ page, page_size, total, items: [GroupItem] }`

---

### `GET /groups/grantable-skills`

当前用户可授权给群组的 Skill（须为发布者或特权用户）。指定 `group_id` 时返回每个 Skill 对该群组的授权状态。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `keyword` | - | Skill 名称模糊匹配 |
| `group_id` | - | 目标群组，用于返回授权状态 |

**响应 `data` 关键字段**：`asset_id`、`name`、`grantable`、`not_grantable_reason`（未通过审核时）、`group_grant_status`（`pending` \| `active` \| `rejected` \| `revoked` \| `null`）

---

### `GET /groups/{group_id}`

群组详情。非成员访问 private 群组返回 403/404。

**响应 `data`**：同 [`POST /groups`](#post-groups)

---

### `PATCH /groups/{group_id}`

更新群组信息。仅群主（含特权用户）可操作。

**请求体**（所有字段可选）

```json
{ "name": "研发组（更新）", "description": "新描述", "visibility": "private" }
```

**响应 `data`**：更新后的 `GroupItem`

---

### `DELETE /groups/{group_id}`

删除群组。成员、申请、授权全部清除。仅群主（含特权用户）可操作。接入操作日志。

**响应 `data`**：`{ "group_id": "grp_abc123" }`

---

### `GET /groups/{group_id}/members`

群组成员列表。仅成员（含特权用户）可见。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |

**响应 `data` 示例**

```json
{
  "page": 1, "page_size": 20, "total": 5,
  "items": [{ "user_id": "692f9cb4...", "user_name": "Alice", "role": "owner", "create_time": 1722412800000, "update_time": 1722412800000 }]
}
```

---

### `PUT /groups/{group_id}/members`

新增/更新成员。仅群主（含特权用户）可操作。

**请求体**

```json
{ "user_id": "6937ee22...", "user_name": "Bob", "role": "member" }
```

`role` 固定 `member`（不可设为 owner）。

| 状态码 | error | 说明 |
|--------|-------|------|
| `400` | `cannot_demote_owner` | 不可将 owner 降级 |
| `409` | `group_member_limit_exceeded` | 成员达上限（特权豁免） |

---

### `DELETE /groups/{group_id}/members/{user_id}`

移除成员。本人可退出，移除他人须群主（含特权用户）。移除后失去组内 Skill 访问权，并清理历史申请记录。接入操作日志。

| 状态码 | error | 说明 |
|--------|-------|------|
| `400` | `cannot_remove_owner` | 不可移除 owner |
| `404` | `member_not_found` | 目标不存在 |

---

### `POST /groups/{group_id}/join-requests`

提交加入申请。仅 `listed` 群组可申请；特权用户可直接加入（含 private），跳过审批。

**请求体**

```json
{ "message": "希望加入研发组" }
```

**响应 `data`** - `GroupJoinRequestItem`：

```json
{
  "request_id": "req_xyz789",
  "group_id": "grp_abc123",
  "user_id": "6937ee22...",
  "user_name": "Bob",
  "message": "希望加入研发组",
  "status": "pending",
  "create_time": 1722412800000,
  "update_time": 1722412800000
}
```

特权用户直接加入时 `status` 为 `approved`，`request_id` 为空。

| 状态码 | error | 说明 |
|--------|-------|------|
| `404` | `group_not_found` | private 群组对非成员不可见 |
| `409` | `already_member` | 已是成员 |

---

### `GET /groups/{group_id}/join-requests`

加入申请列表。仅群主（含特权用户）可查看。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `status` | - | `pending` \| `approved` \| `rejected`（不传返回全部） |

**响应 `data`**：`{ page, page_size, total, items: [GroupJoinRequestItem] }`

---

### `POST /groups/{group_id}/join-requests/{request_id}/decision`

审批加入申请。仅群主（含特权用户）。接入操作日志。

**请求体**

```json
{ "status": "approved" }
```

`status`：`approved`（成为成员）\| `rejected`。**响应 `data`**：更新后的 `GroupJoinRequestItem`

| 状态码 | error | 说明 |
|--------|-------|------|
| `404` | `join_request_not_found` | 申请不存在 |
| `409` | `group_member_limit_exceeded` | 成员达上限（特权豁免） |

---

### `GET /groups/{group_id}/grants`

群组 Skill 授权列表。能查看群组即可调用；非群主仅可见 `active`，群主（含特权用户）可按状态过滤查看申请记录。

| Query | 默认 | 说明 |
|-------|------|------|
| `page` | `1` | 页码 |
| `page_size` | `20` | 每页条数（1–100） |
| `status` | - | `pending` \| `active` \| `rejected` \| `revoked`（不传返回全部；非群主强制 `active`） |

**响应 `data` 示例**

```json
{
  "page": 1, "page_size": 20, "total": 3,
  "items": [{
    "group_id": "grp_abc123",
    "asset_id": "482becff...",
    "skill_name": "my-demo-skill",
    "skill_display_name": "演示 Skill",
    "latest_version": "1.0.0",
    "public_latest_version": "1.0.0",
    "status": "active",
    "viewer_access_source": "owner",
    "create_time": 1722412800000,
    "update_time": 1722412800000
  }]
}
```

---

### `POST /groups/{group_id}/grants`

提交 Skill 授权。须为 Skill 发布者或特权用户。群主/特权用户提交直接生效（`active`）；非群主提交进入待审批（`pending`），群主收到站内通知。接入操作日志。

**请求体**

```json
{ "asset_id": "482becff9f044ba9bad9caef2e43b539" }
```

**响应 `data`**：`GroupSkillGrantItem`（`status` 为 `active` 或 `pending`）

| 状态码 | error | 说明 |
|--------|-------|------|
| `403` | `permission_denied` | 非发布者且非特权用户 |
| `409` | `skill_not_approved` | Skill 未通过审核 |

---

### `POST /groups/{group_id}/grants/{asset_id}/decision`

审批 Skill 授权。仅群主（含特权用户）可审批 `pending` 状态的授权。审批结果通过站内通知告知发布者。接入操作日志。

**请求体**

```json
{ "status": "active" }
```

`status`：`active`（生效，组内可访问）\| `rejected`。**响应 `data`**：更新后的 `GroupSkillGrantItem`

| 状态码 | error | 说明 |
|--------|-------|------|
| `404` | `grant_not_found` | 授权不存在或已撤回 |
| `409` | - | 授权非待处理状态 |

---

### `DELETE /groups/{group_id}/grants/{asset_id}`

撤回/移除 Skill 授权。群主、Skill 发布者、特权用户任一即可操作。记录标记为 `revoked`（不删除）。接入操作日志。

```bash
curl -X DELETE "https://swarmskills.openjiuwen.com/api/v1/groups/grp_abc123/grants/482becff..." \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**响应 `data`**：`{ "group_id": "...", "asset_id": "..." }`

| 状态码 | error | 说明 |
|--------|-------|------|
| `403` | `permission_denied` | 非群主、非发布者、非特权用户 |
| `404` | `grant_not_found` | 授权不存在或已撤回 |

---

### 授权状态流转

| 当前状态 | 操作 | 目标状态 |
|----------|------|----------|
| （无） | 群主/特权用户提交 | `active` |
| （无） | 非群主提交 | `pending` |
| `pending` | 群主通过 | `active` |
| `pending` | 群主拒绝 | `rejected` |
| `pending`/`active` | 撤回 | `revoked` |
| `rejected`/`revoked` | 群主/特权用户重提 | `active` |
| `rejected`/`revoked` | 非群主重提 | `pending` |

---

## 审核管理

---

### `POST /plugins/{asset_id}/moderation`

对 Skill 指定版本执行 **审核通过** 或 **驳回**。须 **审核管理员** Bearer。

**请求体**

```json
{
  "action": "approve",
  "version": "1.0.0",
  "reason": null
}
```

| `action` | `reason` | 效果 |
|----------|----------|------|
| `approve` | 可选 | 版本对外可见 |
| `reject` | **必填** | 驳回；发布者需发新版本 |

**示例 — 驳回**

```bash
curl -X POST "https://swarmskills.openjiuwen.com/api/v1/plugins/{asset_id}/moderation" \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"reject","version":"1.0.0","reason":"描述不清晰，请补充使用示例"}'
```

#### 特殊审核状态

| 条件 | 状态码 | error | 说明 |
|------|--------|-------|------|
| 非 Skill 类型 | `400` | `not_skill` | — |
| 仍在系统审查中 | `400` | — | 暂不可人工审核 |
| 版本已通过且 action=reject | `409` | `moderation_version_locked` | 不可驳回 |
| 版本已驳回且再次 reject | `409` | `already_rejected` | 不可重复驳回 |
| reject 未填 reason | `422` | `reason_required` | — |
| 非审核管理员 | `403` | `permission_denied` | — |

---

### `GET /plugins/audit/skill-moderation`

返回当前审核员 **本人** 的历史审核记录（通过/驳回）。

| Query | 默认 |
|-------|------|
| `page` | `1` |
| `page_size` | `20`（最大 200） |

---

## 用户互动

前缀：`/api/v1/plugins`

---

### `POST /plugins/{asset_id}/view`

浏览量 +1。**无需鉴权**。对隐藏、下架或未通过审核的 Skill 返回 `404`（防止探测）。

---

### `POST /plugins/{asset_id}/interact`

点赞或收藏 **切换**（再次调用则取消）。

**请求体**

```json
{
  "action_type": "like"
}
```

`action_type`：`like` | `star`

| 条件 | 状态码 | error |
|------|--------|-------|
| 对自己的 Skill | `403` | `self_interaction_forbidden` |
| Skill 未通过审核 | `400` | `skill_not_approved` |
| 资产不存在或不可见 | `404` | `not_found` |

---

### `GET /plugins/my/stars` · `GET /plugins/my/likes`

当前用户收藏 / 点赞列表。

| Query | 默认 | 上限 |
|-------|------|------|
| `page` | `1` | — |
| `page_size` | `20` | 100 |

---

### `GET /plugins/interactions/batch`

批量查询多个资产的互动状态。登录后可返回当前用户是否已点赞/收藏。

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/plugins/interactions/batch?asset_ids=id1&asset_ids=id2"
```

最多 **50** 个 `asset_id`；超出返回 `400`（`too_many_ids`）。

---

### `GET /plugins/{asset_id}/interactions`

单个资产的点赞数、收藏数及当前用户互动状态。

---

## 通知中心

前缀：`/api/v1/notifications`

---

### `GET /notifications`

返回通知列表与 `unread_count`。须 Bearer。

---

### `POST /notifications/read-all`

将全部通知标记为已读。响应 `data.updated` 为更新条数。

---

## 审计日志

前缀：`/api/v1/audit`
**权限：** 审核管理员 Bearer **或** System Token

列表、统计、导出共用以下 Query 过滤（`date_from_ms` / `date_to_ms` **必填**，跨度 ≤ 90 天）：

| 参数 | 说明 |
|------|------|
| `date_from_ms` | 起始时间（毫秒时间戳） |
| `date_to_ms` | 结束时间 |
| `keyword` | 关键词 |
| `resource_type` | 如 `skill`、`git_source` |
| `action` | 如 `PUBLISH`、`DELETE` |
| `result` | `SUCCESS` / `FAILED` |
| `asset_plugin_type` | 资产类型 |
| `source_channel` | 来源渠道 |

---

### `GET /audit/logs`

分页查询审计记录。`page_size` 默认 50，最大 100。

---

### `GET /audit/logs/{event_id}`

单条审计详情，含 `detail`、`user_agent`、`extra`。

---

### `GET /audit/stats`

统计概览：操作数、失败数、慢操作数（口径跟随过滤条件）。

---

### `GET /audit/logs/export`

流式导出 CSV，单次上限 **5 万** 条。

---

## 站点公开

---

### `GET /site/config`

返回前端运行时功能开关，无需鉴权。前端据此控制功能按钮的显示/隐藏，无需重新构建。

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/site/config"
```

**响应示例：**

```json
{
  "playground_enabled": false,
  "github_star_enabled": false,
  "rec_list_top_k": 50
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `playground_enabled` | boolean | 在线体验功能开关 |
| `github_star_enabled` | boolean | 一键标星功能开关（`MARKET_GITHUB_STAR_ENABLED`，默认 `false`） |
| `rec_list_top_k` | int | 「推荐精选」一次召回上限（`MARKET_REC_LIST_TOP_K`） |

---

### `GET /site/privacy-statement`

返回隐私声明 **Markdown 纯文本**（非 JSON）。无需鉴权。

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/site/privacy-statement"
```

---

## GitHub 标星

一键标星 openjiuwen-ai 组织核心 GitHub 仓库。需 GitHub OAuth 登录（scope 含 `public_repo`）。
功能开关 `MARKET_GITHUB_STAR_ENABLED=false` 时返回 404。

固定标星仓库清单（`repos` 为空时使用，共 10 个）：

| # | 仓库名 |
|---|--------|
| 1 | jiuwenswarm |
| 2 | agent-studio |
| 3 | agent-core |
| 4 | jiuwensymbiosis |
| 5 | deepsearch |
| 6 | agent-memory |
| 7 | agent-protocol |
| 8 | agent-core-java |
| 9 | agent-runtime-java |
| 10 | skillhub |

---

### `POST /github/watch`

批量标星选中的仓库。`repos` 为空数组时使用固定核心仓库清单（上表 10 个）逐个标星。

**鉴权：** Bearer token（GitHub OAuth access_token）

**请求头：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `Authorization` | 是 | `Bearer {github_access_token}` |
| `X-OAuth-Provider` | 否 | token 归属厂商（`github`/`gitcode`），用于标星成功后按用户隔离写入 Redis 状态；缺失时按 `github` 处理 |

**请求体：**

```json
{
  "repos": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repos` | array | 否 | 待标星仓库列表；空数组 = 一键标星固定 10 个核心仓库 |
| `repos[].owner` | string | 是 | 仓库所有者（仅允许 `[A-Za-z0-9_.-]`，须为 `openJiuwen-ai`） |
| `repos[].repo` | string | 是 | 仓库名（仅允许 `[A-Za-z0-9_.-]`） |

```bash
# 一键标星核心仓库
curl -X POST "https://swarmskills.openjiuwen.com/api/v1/github/watch" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"repos":[]}'

# 标星指定仓库
curl -X POST "https://swarmskills.openjiuwen.com/api/v1/github/watch" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"repos":[{"owner":"openJiuwen-ai","repo":"skillhub"}]}'
```

**响应示例：**

```json
{
  "code": 200,
  "message": "ok",
  "data": {
    "results": [
      { "owner": "openJiuwen-ai", "repo": "skillhub", "status": "success" },
      { "owner": "openJiuwen-ai", "repo": "xxx", "status": "failed", "error": "GitHub 权限不足", "code": 403 }
    ]
  }
}
```

| results[].status | 说明 |
|------------------|------|
| `success` | 标星成功（PUT 幂等，已标星的再标也返回 success） |
| `failed` | 标星失败，附带 `error` 和 `code` 字段 |

> 标星采用**串行 + 1.25s 间隔**（遵循 GitHub 写请求间隔 ≥1s 最佳实践），10 个仓库约 13s 完成。至少一个仓库成功后，后端写入 Redis 标星状态（永久 key `github_star_user:{provider}:{login}`），供 `GET /github/watch/status` 查询。

**错误码：**

| HTTP | error_code | 说明 |
|------|------------|------|
| 401 | `SKILLHUB_AUTH_HEADER_MISSING` | 缺少 Authorization 头 |
| 401 | `SKILLHUB_AUTH_TOKEN_INVALID` | GitHub token 无效或已过期 |
| 403 | `SKILLHUB_GITHUB_FORBIDDEN` | GitHub 权限不足（可能缺少 public_repo 授权）或非白名单组织 |
| 404 | `SKILLHUB_FEATURE_DISABLED` | 标星功能已关闭 |
| 429 | `SKILLHUB_GITHUB_PROXY_RATE_LIMITED` | 服务繁忙，请稍后重试 |
| 502 | `SKILLHUB_GITHUB_UPSTREAM_ERROR` | GitHub 返回异常 |

---

### `GET /github/watch/status`

查询当前用户是否已标星 openjiuwen-ai 组织核心仓库。标星状态存 Redis（按 `provider:login` 隔离，永久 key），跨设备同步。

**鉴权：** Bearer token（GitHub OAuth access_token）

**请求头：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `Authorization` | 是 | `Bearer {github_access_token}` |
| `X-OAuth-Provider` | 否 | token 归属厂商（`github`/`gitcode`）；缺失时按 `github` 处理 |

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/github/watch/status" \
  -H "Authorization: Bearer {token}"
```

**响应示例：**

```json
{
  "code": 200,
  "message": "ok",
  "data": {
    "starred": true
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `starred` | boolean | 是否已标星。`true` = 至少一次标星请求有仓库成功（后端已写 Redis）；`false` = 未标星或 Redis 不可用（降级，用户可重新点，PUT 幂等无害） |

**错误码：**

| HTTP | error_code | 说明 |
|------|------------|------|
| 401 | `SKILLHUB_AUTH_HEADER_MISSING` | 缺少 Authorization 头 |
| 401 | `SKILLHUB_AUTH_TOKEN_INVALID` | GitHub token 无效或已过期 |
| 404 | `SKILLHUB_FEATURE_DISABLED` | 标星功能已关闭 |

---

## 附录：ClawHub 兼容层

完整说明见 [ClawHub兼容层.md](./ClawHub兼容层.md)。速查表见本文 [端点速查表 — ClawHub 兼容层](#clawhub-兼容层)。

> 兼容层响应 **不使用** `ResponseModel` 包装；`{slug}` 即市场 `asset_id`。

### `GET /skills/{slug}`

返回 Skill 元数据、**当前对外最新版本**详情与发布者信息。

| 项 | 说明 |
|----|------|
| **鉴权** | 无需 |

```bash
curl "https://swarmskills.openjiuwen.com/api/v1/skills/{asset_id}"
```

#### 特殊可见性规则

兼容层仅按 **公开市场** 规则返回；未通过审核或无对外版本的 Skill 统一 **404**（不区分是否为 owner）：

| 条件 | 状态码 | error | 说明 |
|------|--------|-------|------|
| slug 不存在 | `404` | `skill_not_found` | — |
| 无可对外版本 | `404` | `skill_version_not_found` | 从未审核通过 |
| 正常 | `200` | — | 内容为已通过审核的对外版本 |

发布者查看待审版本、审核员操作请使用原生 API [`GET /plugins/{asset_id}/versions/{version}`](#get-pluginsasset_idversionsversion)。

---

### `GET /skills/{slug}/versions`

返回 **对外可见** 的版本列表（过滤待审/驳回）。

| Query | 默认 |
|-------|------|
| `limit` | `50` |

---

### `GET /skills/{slug}/versions/{version}`

返回版本详情及 zip 内各文件的 SHA256 列表。

---

## 相关文档

- [TeamSkillsHub.md — 错误码与 OpenAPI YAML](./TeamSkillsHub.md)
- [ClawHub 兼容层](./ClawHub兼容层.md)
- [角色与权限（用户视角）](../4.%20用户指南/角色与权限.md)

