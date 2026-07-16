# TeamSkillsHub API（OpenAPI）

> **推荐阅读：[TeamSkillsHub 接口参考](./TeamSkillsHub-接口参考.md)** — 按模块组织的对外 API 说明，含端点总览、curl 示例、可见性/审核状态表。
> 本文档保留 **错误码速查** 与 **OpenAPI 3.1 YAML**（Swagger / codegen）。

## 范围说明

| 类别 | 路径前缀 | 关键业务价值 |
|------|----------|-------------|
| 核心资源 | `/api/v1/plugins`、`/api/v1/artifacts` 等 | **管理 Skill 生命周期**<br>• 决定用户在市场看到的内容<br>• 高频调用（发布/列表/下载） |
| 审核管理 | `/api/v1/plugins/{asset_id}/moderation`、`/api/v1/plugins/audit/skill-moderation` | **内容合规**<br>• Skill 审核通过/驳回<br>• 审核员操作历史追溯<br>• 仅审核管理员可调用 |
| 用户互动 | `/api/v1/plugins/my/stars`、`/api/v1/plugins/{asset_id}/interact` 等 | **提升用户粘性**<br>• 收藏/点赞影响推荐排序<br>• 每页面加载触发 3-5 次 |
| 通知中心 | `/api/v1/notifications` | **消息触达**<br>• 审核结果、版本更新等关键事件推送<br>• 驱动用户回访 |
| 站点元数据 | `/api/v1/site` | **合规与透明**<br>• 隐私声明等法定披露信息<br>• 无需鉴权，公开可访问 |
| 认证授权 | `/api/v1/auth` | **身份基石**<br>• 所有需鉴权接口的前置依赖<br>• 供客户端（Web / CLI 等）统一使用 |

### 全局约束

- **核心资源**（发布/删除/模板）：鉴权 `Bearer`（OAuth 用户令牌）**或** `X-System-Token`（系统令牌，二选一）；文件上传必须携带 `X-Checksum-SHA256` 头
- **审核管理**（审核/审计）：需 `Bearer` 鉴权且当前用户为审核管理员；批量导入仅 `X-System-Token`
- **用户互动**（点赞/收藏）：需 `Bearer` 鉴权；浏览量 +1 无需鉴权；批量查询可选 `Bearer`
- **通知中心**：全部需 `Bearer` 鉴权
- **站点元数据**：无需鉴权
- **认证授权**：OAuth session 兑换无需鉴权；`/auth/me` 需 `Bearer`

> ⚠️ **安全警告**：`X-System-Token` 为系统级令牌，权限高于普通用户 Token，**严禁**存储于客户端（浏览器 LocalStorage / Cookie / CLI 配置文件等）或写入客户端代码。该 Token 仅适用于服务端间调用。

### OAuth Provider

当前支持 `gitcode`、`github` 两个提供商（路径参数 `{provider}`）。提供商列表可通过代码配置扩展，无需变更接口结构。


---

## 接口规范文档

**详细接口参考（按模块、含请求示例）** → [TeamSkillsHub-接口参考.md](./TeamSkillsHub-接口参考.md)

下面保留 **模块速览表** 便于检索；字段级定义与 codegen 仍以文末 **OpenAPI YAML** 为准。

### 模块速览

#### Skill 市场管理（原生，`ResponseModel`）

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| POST | `/api/v1/plugins` | Bearer **`或`** `X-System-Token`（必须且仅能一种）；请求头 **`X-Checksum-SHA256`** | 发布 Skill（multipart zip） [#核心资源] |
| GET | `/api/v1/plugins` | **无需**（可选 Bearer 用于个性化展示） | Skill 分页列表 [#核心资源] |
| GET | `/api/v1/plugins/publish-template` | Bearer **`或`** `X-System-Token` | 发布页 Skill 模板 zip 预签名 GET [#核心资源] |
| GET | `/api/v1/plugins/{asset_id}/versions/{version}` | **无需**（可选 Bearer） | 指定版本详情 [#核心资源] |
| GET | `/api/v1/plugins/{asset_id}/versions/{version}/files` | **无需**（可选 Bearer） | 版本 zip 包内文件列表；`with_content=<path>` 可附带单个文本文件内容 [#核心资源] |
| DELETE | `/api/v1/plugins/{asset_id}/versions/{version}` | Bearer **`或`** `X-System-Token` | 删除指定版本 ⚠️`version=all` 将**不可逆**删除该资产全部版本及对象存储物理文件 [#核心资源] |
| GET | `/api/v1/artifacts/{id}` | **可选** `Authorization: Bearer`（用于记录拉取用户；无效 token 忽略，仍可下载） | 下载信息（预签名 URL 等，`version` 可选） [#核心资源] |
| POST | `/api/v1/plugins/skill-import` | **仅** `X-System-Token`；请求头 **`X-Checksum-SHA256`** | 批量导入 Skill（multipart zip 集合包） [#核心资源] |

#### Git 源管理（`ResponseModel`）

> Git 源为「从公有 Git 仓库自动同步 Skill」的资源；创建/同步走**后台任务**，接口立即返回 `syncing`，客户端轮询列表查看进度。每条 Git 源按 `created_by_user_id` 归属当前用户。

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/plugins/git-sources` | Bearer **`或`** `X-System-Token` | 当前用户的 Git 源列表（含同步状态） [#核心资源] |
| POST | `/api/v1/plugins/git-sources` | Bearer **`或`** `X-System-Token` | 创建 Git 源并触发首次后台同步 [#核心资源] |
| POST | `/api/v1/plugins/git-sources/{source_id}/sync` | Bearer **`或`** `X-System-Token` | 再次触发该 Git 源后台同步（仅源属主） [#核心资源] |
| DELETE | `/api/v1/plugins/git-sources/{source_id}` | Bearer **`或`** `X-System-Token` | 删除 Git 源注册（仅源属主） [#核心资源] |

#### 审核管理（`ResponseModel`）

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| POST | `/api/v1/plugins/{asset_id}/moderation` | Bearer（审核管理员） | 审核通过/驳回 Skill [#审核管理] |
| GET | `/api/v1/plugins/audit/skill-moderation` | Bearer（审核管理员） | 当前审核员操作历史 [#审核管理] |

#### 用户互动（`ResponseModel`）

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/plugins/my/stars` | Bearer | 我收藏的 Skill 列表 [#用户互动] |
| GET | `/api/v1/plugins/my/likes` | Bearer | 我点赞的 Skill 列表 [#用户互动] |
| POST | `/api/v1/plugins/{asset_id}/view` | **无需** | 浏览量 +1 [#用户互动] |
| POST | `/api/v1/plugins/{asset_id}/interact` | Bearer | 点赞/收藏切换（`like` / `star`） [#用户互动] |
| GET | `/api/v1/plugins/interactions/batch` | **可选** Bearer | 批量查询互动状态（最多 50 个） [#用户互动] |
| GET | `/api/v1/plugins/{asset_id}/interactions` | **可选** Bearer | 单个资产互动状态 [#用户互动] |

#### 通知（`ResponseModel`）

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/notifications` | Bearer | 获取通知列表 [#通知中心] |
| POST | `/api/v1/notifications/read-all` | Bearer | 全部标记已读 [#通知中心] |

#### 站点公开信息

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/site/privacy-statement` | **无需** | 隐私声明（Markdown 纯文本） [#站点元数据] |

#### 认证（`ResponseModel`）

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/auth/oauth/{provider}/start` | **无需** | 浏览器重定向到厂商授权页 [#认证授权] |
| GET | `/api/v1/auth/oauth/{provider}/callback` | **无需** | OAuth 回调，换取令牌后重定向前端 [#认证授权] |
| POST | `/api/v1/auth/oauth/{provider}/session` | **无需** | 一次性兑换 OAuth session 获取 access_token 与用户信息 [#认证授权] |
| GET | `/api/v1/auth/me` | Bearer | 校验当前 token 并返回用户信息 [#认证授权] |

### 全局错误响应

除 OAuth 回调失败场景外，所有错误响应的 HTTP body 均为 JSON，外层统一包裹在 `detail` 字段内。

标准结构如下：

```json
{
  "detail": {
    "code": 400,
    "error": "checksum_mismatch",
    "message": "文件校验和不匹配，文件可能在传输过程中损坏",
    "data": null,
    "http_status": 400,
    "error_class": "validation",
    "error_code": "SKILLHUB_IMPORT_CHECKSUM_MISMATCH"
  }
}
```

| 字段 | 含义 |
|---|---|
| `code` | 兼容字段，等同当前 HTTP 状态码 |
| `http_status` | HTTP 状态码 |
| `error` | 兼容错误名 |
| `error_class` | 错误大类，如 `validation` / `auth` / `permission` / `upstream` / `internal` |
| `error_code` | 稳定机器码，推荐客户端优先使用 |
| `message` | 面向调用方展示的安全文案 |
| `data` | 兼容扩展字段 |
| `details` | 白名单业务上下文；422 请求校验错误时为校验错误数组 |
| `meta` | 关联信息与诊断信息 |

> 客户端统一按 `detail.error_code` / `detail.error_class` / `detail.message` 解析；需要展示字段级校验信息时，再读取 `detail.details`。

#### OAuth 回调重定向错误

`GET /api/v1/auth/oauth/{provider}/callback` 失败不会返回 JSON，而是 **302 重定向** 到前端登录页，并在 query 中附带结构化错误参数：

| Query 参数 | 含义 |
|---|---|
| `oauth_error` | 前端登录页展示用错误文案 |
| `oauth_status` | 对应 HTTP 状态码 |
| `oauth_error_code` | 稳定机器码 |
| `oauth_error_class` | 错误大类 |
| `oauth_error_name` | 兼容错误名 |

示例：

```text
/login?oauth_error=状态无效或已过期，请重新登录&oauth_status=400&oauth_error_code=SKILLHUB_OAUTH_STATE_INVALID&oauth_error_class=auth&oauth_error_name=oauth_state_invalid
```

#### 通用错误码

下表 `error` 字段仅适用于结构化错误对象。

| HTTP | error | 触发场景 |
|------|-------|---------|
| 400 | `invalid_file_format` | 上传文件非 .zip 格式 |
| 400 | `checksum_mismatch` | 文件 SHA256 与请求头不匹配 |
| 400 | `checksum_required` | X-Checksum-SHA256 头缺失或格式错误（需 64 位小写十六进制） |
| 400 | `invalid_action_type` | 互动类型不在 like/star 枚举内 |
| 400 | `too_many_ids` | 批量查询超过 50 个 ID |
| 400 | `payload_too_large` | skill-import 集合包超过大小限制（该接口特例，返回 400 而非 413） |
| 401 | `auth_header_missing` / `auth_token_invalid` / `system_token_invalid` | Token 缺失、无效或过期 |
| 403 | `permission_denied` | 无权操作该资源（非所有者/非审核员） |
| 403 | `forbidden` | 通用禁止访问 |
| 403 | `self_interaction_forbidden` | 不可对自己的资产点赞/收藏 |
| 404 | `plugin_not_found` | Skill 或版本不存在 |
| 404 | `not_found` | 互动目标资产不存在 |
| 404 | `version_not_found` | 指定版本不存在 |
| 404 | `version_deleted` | 版本已被删除；当前调用点按资源不存在处理（如对象存储 head 检测到 not_found） |
| 409 | `version_deleted` | 版本已被删除；当前调用点按状态冲突处理 |
| 409 | `version_conflict` | 同名同版本已存在（应用层检测） |
| 409 | `version_exists` | 同名同版本已存在（数据库约束） |
| 409 | `plugin_name_exists` | 同名 Skill 已发布 |
| 413 | `file_too_large` | 文件超过大小限制 |
| 400 | `zip_too_large` | zip 内单文件过大或压缩包风控超限 |
| 422 | `manifest_validation_failed` | 同名多插件未指定 plugin_id 等业务校验失败（版本格式错误见 `invalid_version` / `invalid_plugin_config`，均为 400） |
| 422 | `plugin_id_mismatch` | 提交的 plugin_id 与包内信息不一致 |
| 400 | `invalid_plugin_config` | plugin.yaml / SKILL.md 配置不合法 |
| 400 | `invalid_plugin_structure` | zip 目录结构不合法 |
| 400 | `invalid_version` | `plugin_version` 表单或下载 `version` 查询参数格式错误（须 x.y.z 或 7 位 commit hex，不含 v 前缀） |
| 400 | `invalid_oauth_provider` | X-OAuth-Provider 值不在支持列表中 |
| 429 | `rate_limited` | 发布/导入接口触发限流 |
| 500 | `storage_error` | 对象存储上传/下载失败；当前调用点按服务内部失败处理 |
| 502 | `storage_error` | 对象存储上传/下载失败；当前调用点按上游依赖失败处理 |
| 500 | `internal_error` | 服务器内部错误 |
| 503 | `template_not_configured` | 未配置模板对象路径 |
| 500 | `presign_failed` | 生成预签名链接失败 |

#### 业务专用错误码

| HTTP | error | 触发场景 |
|------|-------|---------|
| 400 | `skill_not_approved` | 互动目标 Skill 尚未通过审核 |
| 400 | `not_skill` | 非 Skill 类型资产调用了 Skill 专用接口（审核等） |
| 400 | `invalid_skill_md` | SKILL.md 格式或内容不合法 |
| 400 | `invalid_moderation_state` | 当前版本审核状态不允许执行该操作 |
| 400 | `invalid_action` | 审核操作类型不在 approve/reject 枚举内 |
| 400 | `manifest_too_large` | skill-import 集合包中 manifest.json 超过大小上限 |
| 400 | `manifest_invalid` | manifest.json 不是合法 UTF-8 或 JSON 解析失败 |
| 400 | `invalid_skill_bundle` | 集合包中无有效 skill 目录结构 |
| 400 | `too_many_skill_entries` | 集合包中 skill 目录数量超过上限 |
| 500 | `import_normalize_failed` | 单个 skill 条目规范化处理失败 |
| 409 | `skill_limit_exceeded` | 发布 Skill 数量超过上限 |
| 409 | `moderation_version_locked` | 该版本已审核通过，不可驳回 |
| 409 | `already_rejected` | 该版本已被驳回，不可重复驳回 |
| 422 | `reason_required` | 审核不通过时必须填写原因 |
| 500 | `raw_zip_build_failed` | 生成或上传 raw.zip 失败 |
| 500 | `db_error` | 数据库操作失败（如更新下载统计等） |

---

### OpenAPI YAML

```yaml
openapi: 3.1.0
info:
  title: TeamSkillsHub API
  description: |
    TeamSkillsHub 市场原生 API。
    接口分类、环境变量与鉴权说明请参阅本文档「范围说明」章节。
  version: 1.0.0
servers:
  - url: http://localhost:8100
    description: 本地开发环境
  - url: https://market.example.com
    description: 生产环境
paths:
  /api/v1/plugins:
    post:
      summary: 发布 Skill
      description: 发布 Skill 到指定空间，同时完成文件上传和发布操作。鉴权需二选一：Authorization Bearer 或 X-System-Token（必须且只能提供一个）。最终资源类型由服务端根据包内 SKILL.md 判定；客户端上传包中的 plugin.yaml.runtime.type 统一为 skill，且请求体不传 plugin_type。
      operationId: publishSkill
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: X-Checksum-SHA256
          in: header
          required: true
          description: Skill 包文件的 SHA256 校验和（小写十六进制字符串）
          schema:
            type: string
            pattern: "^[a-f0-9]{64}$"
            example: "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890"
        - name: Authorization
          in: header
          required: false
          description: Bearer {token}，通过 OAuth 登录获取的访问令牌
          schema:
            type: string
            example: "Bearer {token}"
        - name: X-System-Token
          in: header
          required: false
          description: 系统令牌，由服务端配置，仅用于服务端间调用
          schema:
            type: string
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
              required:
                - file
              properties:
                file:
                  type: string
                  format: binary
                  description: Skill 包文件（.zip 格式）
                plugin_id:
                  type: string
                  description: Skill ID，为已存在 Skill 添加新版本时必填。首次发布时请勿提供此字段，系统将自动生成。
                  example: "3589119244ed45c29f98038642872858"
                plugin_version:
                  type: string
                  description: 版本号，不填则从 plugin.yaml 读取。格式：主版本号.次版本号.修订号（如 1.0.0），不接受 v 前缀
                  pattern: "^[0-9]+\\.[0-9]+\\.[0-9]+$"
                  example: "1.0.0"
                version_desc:
                  type: string
                  description: 版本说明
                  example: "首次发布，支持天气查询功能"
                force:
                  type: boolean
                  description: 是否强制覆盖同名同版本
                  default: false
            encoding:
              file:
                contentType: application/zip
      responses:
        '200':
          description: 发布成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SkillPublishResponse'
              example:
                code: 200
                message: "Publish plugin successfully"
                data:
                  plugin_id: "3589119244ed45c29f98038642872858"
                  name: "weather-skill"
                  version: "1.0.0"
                  status: "ACTIVE"
                  published_at: "2025-01-01T00:00:00Z"
                  storage_url: "plugins/xxx/xxx/1.0.0/weather-skill_1.0.0.zip"
        '400':
          description: 请求参数错误或校验和不匹配
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                invalid_file_format:
                  summary: 文件格式错误
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "invalid_file_format"
                      message: "仅支持 .zip 格式的插件包文件"
                checksum_mismatch:
                  summary: 校验和不匹配
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "checksum_mismatch"
                      message: "文件校验和不匹配，文件可能在传输过程中损坏"
                checksum_required:
                  summary: 校验和请求头缺失或格式错误
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "checksum_required"
                      message: "请求头 X-Checksum-SHA256 必填，且为 64 位小写十六进制字符串"
                invalid_version:
                  summary: 版本号格式错误（表单 plugin_version）
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "invalid_version"
                      message: "版本号格式错误：须为 x.y.z（如 1.0.0），不接受 v 前缀；或 Git commit 7 位小写十六进制，且长度不得超过 32 个字符"
                invalid_plugin_config:
                  summary: plugin.yaml 中 version 格式错误
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "invalid_plugin_config"
                      message: "plugin.yaml 中 version 必须符合 x.y.z 语义化版本格式，或为 7 位小写十六进制 Git commit"
        '401':
          description: 未授权 / token 无效
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '403':
          description: 权限不足
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 403
                  data: null
                  error: "permission_denied"
                  message: "您没有权限在该空间发布 Skill"
        '404':
          description: Skill 不存在
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 404
                  data: null
                  error: "plugin_not_found"
                  message: "Skill '3589119244ed45c29f98038642872858' 不存在，无法添加新版本"
        '409':
          description: 版本冲突
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/VersionConflictResponse'
              examples:
                version_conflict:
                  summary: 版本冲突（应用层检测）
                  value:
                    detail:
                      code: 409
                      data:
                        existing_plugin:
                          plugin_id: "3589119244ed45c29f98038642872858"
                          version: "1.0.0"
                      error: "version_conflict"
                      message: "Skill 'weather-skill' 版本 '1.0.0' 已存在，如需覆盖请设置 force=true"
                version_exists:
                  summary: 版本已存在（数据库约束触发）
                  value:
                    detail:
                      code: 409
                      data:
                        existing_version: "1.0.0"
                      error: "version_exists"
                      message: "Skill 版本 '1.0.0' 已存在，如需覆盖请设置 force=true"
                plugin_name_exists:
                  summary: Skill 名称已存在
                  value:
                    detail:
                      code: 409
                      data: null
                      error: "plugin_name_exists"
                      message: "您已发布过同名 Skill 'weather-skill'，请使用其他名称或为现有 Skill 添加新版本"
        '413':
          description: 文件过大
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 413
                  data: null
                  error: "file_too_large"
                  message: "文件大小超过限制（最大512MB）"
        '422':
          description: 请求验证失败
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                plugin_id_mismatch:
                  summary: plugin_id 与 Skill 包不一致
                  value:
                    detail:
                      code: 422
                      data:
                        expected_plugin_id: "plugin_xyz789"
                      error: "plugin_id_mismatch"
                      message: "plugin_id 与 Skill 包不匹配"
                ambiguous_plugins:
                  summary: 同名多插件未指定 plugin_id
                  value:
                    detail:
                      code: 422
                      data:
                        ambiguous_plugin_ids: ["3589119244ed45c29f98038642872858", "aabbccdd11223344"]
                      error: "manifest_validation_failed"
                      message: "存在多个同名插件 'weather-skill'，请通过 plugin_id 指定要发布版本的插件"
        '500':
          description: 服务器内部错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                internal_error:
                  summary: 服务器内部错误
                  value:
                    detail:
                      code: 500
                      data: null
                      error: "internal_error"
                      message: "服务器内部错误，请稍后重试"
                storage_error:
                  summary: 存储上传失败
                  value:
                    detail:
                      code: 500
                      data: null
                      error: "storage_error"
                      message: "Skill 包上传失败"
    get:
      summary: 获取 Skill 列表
      description: |
        支持分页、筛选与排序。传 `search_keyword` 时走检索引擎语义搜索，未传时按 `order_by` 字段排序。
        列表项 `items[]` 中除 `latest_version` 外，还提供 **`all_versions`**：该资产在 `market_asset_versions` 中的全部版本号，
        按 `create_time`、`version` **升序**（发布时间线从早到晚）；无版本记录时为 `[]`。
      operationId: listSkills
      tags:
        - Skill 管理
      parameters:
        - name: page
          in: query
          required: false
          description: 页码
          schema:
            type: integer
            minimum: 1
            example: 1
        - name: page_size
          in: query
          required: false
          description: 每页条数
          schema:
            type: integer
            minimum: 1
            maximum: 200
            example: 20
        - name: asset_id
          in: query
          required: false
          description: 资产 ID
          schema:
            type: string
        - name: asset_type
          in: query
          required: false
          description: 资产类型
          schema:
            type: string
        - name: publisher_id
          in: query
          required: false
          description: 发布者 ID
          schema:
            type: string
        - name: publisher_name
          in: query
          required: false
          description: 发布者名称（模糊）
          schema:
            type: string
        - name: category_id
          in: query
          required: false
          description: 分类 ID（精确匹配）
          schema:
            type: string
        - name: plugin_type
          in: query
          required: false
          description: 插件类型（精确匹配，如 skill、swarmskill；兼容旧别名 teamskills）。当 plugin_type 与 plugin_type_exclude 都不传时，服务端默认按 skill,swarmskill 过滤。
          schema:
            type: string
        - name: plugin_type_exclude
          in: query
          required: false
          description: 排除某 plugin_type（如 "skill"）
          schema:
            type: string
        - name: search_keyword
          in: query
          required: false
          description: 搜索关键词，传入时走检索引擎语义搜索
          schema:
            type: string
        - name: moderation_status
          in: query
          required: false
          description: 按 Skill 人工审核状态筛选：PENDING | APPROVED | REJECTED（新链路中 PENDING 对应待人工审核）
          schema:
            type: string
            enum: [PENDING, APPROVED, REJECTED]
        - name: order_by
          in: query
          required: false
          description: "排序字段: install_count, like_count, view_count, create_time, update_time, review_count"
          schema:
            type: string
            enum: [install_count, like_count, view_count, create_time, update_time, review_count]
            example: install_count
        - name: desc
          in: query
          required: false
          description: "排序方向: true=降序, false=升序"
          schema:
            type: boolean
            example: true
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillListResponse'
        '422':
          description: 请求验证失败
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '500':
          description: 服务器内部错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/publish-template:
    get:
      summary: 获取发布页 Skill 模板 zip 预签名下载链接
      description: |
        为客户端「发布 Skill」功能提供模板包下载：服务端根据配置的桶内对象 Key 生成 **预签名 GET URL**，对象可放在 **私有** MinIO/OBS 桶中。
        鉴权：**Authorization Bearer** 与 **X-System-Token** 二选一（必须且只能提供一个），与发布/删除接口一致。
        预签名 TTL 与对象下载等一致，统一使用环境变量 `MARKET_S3_PRESIGNED_EXPIRES`（见存储客户端配置）。
        客户端应在用户点击下载时再请求本接口，避免预签名过早过期。
        模板实际只有两套：`kind=skill` / `swarmskill` / `teamskills` 均返回**同一个 Skill 模板**（服务端按 skill 类归并处理）；不传或 `kind=plugin` 返回插件模板。
      operationId: getPublishTemplatePresigned
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: kind
          in: query
          required: false
          description: '模板种类：不传或 "plugin" 为插件模板；"skill" / "swarmskill" / "teamskills" 均返回同一个 Skill 模板'
          schema:
            type: string
            enum: [plugin, skill, swarmskill, teamskills]
        - name: Authorization
          in: header
          required: false
          description: Bearer {token}，通过 OAuth 登录获取的访问令牌
          schema:
            type: string
            example: "Bearer {token}"
        - name: X-System-Token
          in: header
          required: false
          description: 系统令牌，由服务端配置，仅用于服务端间调用
          schema:
            type: string
      responses:
        '200':
          description: 成功返回预签名 URL 及建议文件名
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/PluginTemplatePresignData'
              example:
                code: 200
                message: ok
                data:
                  download_url: "https://obs.example.com/bucket/static/skill-templates/demo-skill.zip?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=..."
                  expires_in: 1800
                  filename: "demo-skill.zip"
        '401':
          description: 未授权 / token 无效（未提供鉴权头、同时提供两种鉴权、或 Bearer/X-System-Token 无效）
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '503':
          description: 未配置模板对象 Key 或服务暂不可用
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 503
                  data: null
                  error: template_not_configured
                  message: 未配置 Skill 发布模板对象路径（MARKET_SKILL_TEMPLATE_OBJECT_KEY）
        '500':
          description: 生成预签名链接失败等服务器错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 500
                  data: null
                  error: presign_failed
                  message: 生成模板下载链接失败：...

  /api/v1/plugins/skill-import:
    post:
      summary: 批量导入 Skill
      description: |
        上传包含多个 Skill 的集合包（ZIP），逐个解析并发布。仅支持 X-System-Token 鉴权。
        集合包为标准 ZIP，顶层包含多个 Skill 目录，每个目录结构同单个 Skill 发布包。
        - `force=true`：同名同版本时覆盖
        - `fail_fast=true`：任一 Skill 导入失败即中止，已成功的保留
      operationId: skillImport
      tags:
        - Skill 管理
      security:
        - systemTokenAuth: []
      parameters:
        - name: X-Checksum-SHA256
          in: header
          required: true
          description: 集合包文件的 SHA256 校验和（小写十六进制字符串）
          schema:
            type: string
            pattern: "^[a-f0-9]{64}$"
        - name: X-System-Token
          in: header
          required: true
          description: 系统令牌，批量导入仅支持系统令牌鉴权
          schema:
            type: string
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
              required:
                - file
              properties:
                file:
                  type: string
                  format: binary
                  description: Skill 集合包文件（.zip 格式，顶层为多个 Skill 目录）
                force:
                  type: boolean
                  description: 同名同版本时是否覆盖
                  default: false
                fail_fast:
                  type: boolean
                  description: 任一 Skill 导入失败时是否立即中止
                  default: false
            encoding:
              file:
                contentType: application/zip
      responses:
        '200':
          description: 导入完成（含部分失败）
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: "Import skills finished"
                  data:
                    $ref: '#/components/schemas/SkillImportResponse'
        '400':
          description: 校验和不匹配或文件格式错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                checksum_mismatch:
                  summary: 校验和不匹配
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "checksum_mismatch"
                      message: "技能集合包 X-Checksum-SHA256 与实际上传内容不一致"
                payload_too_large:
                  summary: 集合包过大
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "payload_too_large"
                      message: "技能集合包原始大小超过 512MB 上限"
        '401':
          description: 未授权 / X-System-Token 无效
        '403':
          description: 非 X-System-Token 鉴权（批量导入不支持 Bearer）
        '429':
          description: 请求过于频繁（限流）
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 429
                  data: null
                  error: "rate_limited"
                  message: "skill-import 请求过于频繁，请稍后再试"

  /api/v1/plugins/git-sources:
    get:
      summary: 当前用户的 Git 源列表
      description: |
        返回当前登录用户注册的所有 Git 仓库源及其最近同步状态（`last_index_status` / `last_index_error` / `last_indexed_at_ms`）。
        访问时顺带回收该用户长时间无心跳的僵死同步任务状态。
      operationId: listMyGitSources
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/GitSourceListResponse'
        '401':
          description: 未授权 / token 无效
    post:
      summary: 创建 Git 源并触发首次同步
      description: |
        创建一条 Git 源记录并立即在后台启动首次同步（克隆 → 解析 skills → 逐条发布）。
        接口同步返回 `syncing`，实际进度通过 `GET /git-sources` 列表的 `last_index_status` 轮询。
        `repo_url` + `ref` + `skills_subpath` 共同决定全站唯一一条 Git 源（去重）。
        受同步频率限流（超限 429）。
      operationId: createGitSource
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: fail_fast
          in: query
          required: false
          description: 遇首条 skill 发布失败即停止；`true`/`1`/`on` 开启，其它（含未传）视为关闭
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/GitSourceCreateRequest'
      responses:
        '200':
          description: 已接受，后台同步中
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/GitSyncAcceptedResponse'
        '400':
          description: 仓库 URL / skills_subpath 非法
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '401':
          description: 未授权 / token 无效
        '409':
          description: 同一仓库+分支/tag+技能根路径已被全局注册，或该源正在同步中
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '429':
          description: 同步请求过于频繁
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/git-sources/{source_id}/sync:
    post:
      summary: 再次同步指定 Git 源
      description: |
        对已存在的 Git 源再次触发后台同步。**仅 Git 源属主**可调用。
        接口同步返回 `syncing`，进度通过列表轮询。受同步频率限流。
      operationId: syncGitSource
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: source_id
          in: path
          required: true
          schema:
            type: string
          description: Git 源 ID
        - name: fail_fast
          in: query
          required: false
          description: 遇首条 skill 发布失败即停止；`true`/`1`/`on` 开启，其它视为关闭
          schema:
            type: string
      responses:
        '200':
          description: 已接受，后台同步中
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/GitSyncAcceptedResponse'
        '401':
          description: 未授权 / token 无效
        '403':
          description: 无权同步该 Git 源或资源不存在
        '409':
          description: 该 Git 源正在同步中
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '429':
          description: 同步请求过于频繁
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/git-sources/{source_id}:
    delete:
      summary: 删除 Git 源注册
      description: 删除当前用户的一条 Git 源注册。**仅源属主**可调用。删除注册不回收已发布的 skill。
      operationId: deleteGitSource
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: source_id
          in: path
          required: true
          schema:
            type: string
          description: Git 源 ID
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    properties:
                      deleted:
                        type: boolean
                        example: true
        '401':
          description: 未授权 / token 无效
        '403':
          description: 无权删除该 Git 源或资源不存在
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/{asset_id}/versions/{version}:
    get:
      summary: 获取某个版本的 Skill 详情
      description: 不进行 token 校验。可选携带 Authorization 头，用于判断当前用户是否为审核管理员。
      operationId: getSkillVersionDetail
      tags:
        - Skill 管理
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
        - name: version
          in: path
          required: true
          schema:
            type: string
          description: 版本号
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillVersionDetail'
        '404':
          description: Skill 或版本不存在
        '500':
          description: 服务器内部错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
    delete:
      summary: 删除某个版本的 Skill
      description: |
        鉴权：Authorization Bearer 或 X-System-Token 二选一（必须且只能提供一个）。
        ⚠️ `version=all` 将不可逆删除该资产全部版本及对象存储（OSS/S3）物理文件，请谨慎调用。
      operationId: deleteSkillVersion
      tags:
        - Skill 管理
      security:
        - bearerAuth: []
        - systemTokenAuth: []
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
        - name: version
          in: path
          required: true
          schema:
            type: string
          description: 具体版本号，传 `all` 删除该资产全部版本
        - name: Authorization
          in: header
          required: false
          description: Bearer {token}，通过 OAuth 登录获取的访问令牌
          schema:
            type: string
            example: "Bearer {token}"
        - name: X-System-Token
          in: header
          required: false
          description: 系统令牌，由服务端配置，仅用于服务端间调用
          schema:
            type: string
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [asset_id, version]
                    properties:
                      asset_id:
                        type: string
                      version:
                        type: string
                      plugin_type:
                        type: string
                        nullable: true
        '401':
          description: 未授权 / token 无效
        '403':
          description: 权限不足
        '404':
          description: Skill 或版本不存在
        '502':
          description: 对象存储删除失败
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/{asset_id}/versions/{version}/files:
    get:
      summary: 版本 zip 包内文件列表
      description: |
        返回指定版本 zip 包内的文件清单（路径 + 大小）。不进行 token 校验，可选携带 Authorization 用于可见性判定。
        传 `with_content=<文件路径>` 时，在同一响应里附带该文件的文本内容（二进制文件或路径不存在则 `content` 为 null）。
      operationId: listSkillVersionFiles
      tags:
        - Skill 管理
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
        - name: version
          in: path
          required: true
          schema:
            type: string
          description: 版本号
        - name: with_content
          in: query
          required: false
          description: 需要附带文本内容的文件路径（zip 包内相对路径）
          schema:
            type: string
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/VersionFilesData'
        '404':
          description: Skill 或版本不存在
        '500':
          description: 服务器内部错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/{asset_id}/moderation:
    post:
      summary: 审核 Skill
      description: |
        对指定 Skill 执行审核通过或驳回操作。仅审核管理员可调用。
        - `action=approve`：通过审核，Skill 将对外可见
        - `action=reject`：驳回审核，需填写 `reason`
        - 仅已进入人工审核阶段的版本可执行该操作；系统审查中的版本不可直接人工审核
        - `version` 不填时默认审核资产当前 `latest_version`
      operationId: moderateSkill
      tags:
        - 审核管理
      security:
        - bearerAuth: []
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - action
              properties:
                action:
                  type: string
                  enum: [approve, reject]
                  description: 审核操作
                reason:
                  type: string
                  nullable: true
                  description: 驳回原因（action=reject 时必填）
                version:
                  type: string
                  nullable: true
                  description: 要审核的版本号，不填则审核当前 latest_version
            example:
              action: "approve"
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillModerationResult'
        '400':
          description: 非 Skill 类型资产或审核操作无效
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                not_skill:
                  summary: 非 Skill 类型资产
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "not_skill"
                      message: "仅支持对 Skill 类型资源进行审核"
                invalid_moderation_state:
                  summary: 当前审核状态不允许该操作
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "invalid_moderation_state"
                      message: "当前版本审核状态不允许执行通过操作"
                invalid_action:
                  summary: 操作类型无效
                  value:
                    detail:
                      code: 400
                      data: null
                      error: "invalid_action"
                      message: "action 必须为 approve 或 reject"
        '401':
          description: 未授权 / token 无效
        '403':
          description: 非审核管理员
        '409':
          description: 审核状态冲突
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              examples:
                moderation_version_locked:
                  summary: 已通过不可驳回
                  value:
                    detail:
                      code: 409
                      data: null
                      error: "moderation_version_locked"
                      message: "该版本已审核通过，不可驳回。"
                already_rejected:
                  summary: 已驳回不可重复驳回
                  value:
                    detail:
                      code: 409
                      data: null
                      error: "already_rejected"
                      message: "该版本已被驳回，请勿重复驳回；可先「审核通过」或等待发布者更新版本。"
        '422':
          description: 驳回时未填写原因
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 422
                  data: null
                  error: "reason_required"
                  message: "审核不通过时必须填写原因"
        '404':
          description: Skill 或版本不存在

  /api/v1/plugins/audit/skill-moderation:
    get:
      summary: 审核员操作历史
      description: 返回当前审核管理员的 Skill 审核操作记录，按时间倒序。仅审核管理员可调用。
      operationId: listSkillModerationAudits
      tags:
        - 审核管理
      security:
        - bearerAuth: []
      parameters:
        - name: page
          in: query
          required: false
          description: 页码
          schema:
            type: integer
            minimum: 1
            example: 1
        - name: page_size
          in: query
          required: false
          description: 每页条数；服务端硬上限 100，传入更大值会被静默截断为 100（不报错）
          schema:
            type: integer
            minimum: 1
            maximum: 100
            example: 20
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillModerationAuditListResponse'
        '401':
          description: 未授权 / token 无效
        '403':
          description: 非审核管理员

  /api/v1/artifacts/{id}:
    get:
      summary: 获取 Skill 下载链接
      description: 根据市场资产 ID 获取下载链接，支持指定版本下载。不指定版本时返回最新版本的下载地址。可选携带 Authorization 头识别用户身份。
      operationId: downloadSkill
      tags:
        - Skill 管理
      parameters:
        - name: id
          in: path
          required: true
          description: 市场资产 ID
          schema:
            type: string
            example: "11112222333344445555666677778888"
        - name: version
          in: query
          required: false
          description: 版本号（如 1.0.0），不指定则返回最新版本
          schema:
            type: string
            pattern: "^[0-9]+\\.[0-9]+\\.[0-9]+$"
            example: "1.0.0"
        - name: is_cli_download
          in: query
          required: false
          description: 是否 CLI 下载；CLI=true 下载原始 zip，其他下载 raw.zip
          schema:
            type: boolean
            default: false
        - name: Authorization
          in: header
          required: false
          description: 可选，Bearer Token 用于识别用户身份
          schema:
            type: string
            example: "Bearer {token}"
      responses:
        '200':
          description: 获取下载链接成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DownloadResponse'
              example:
                code: 200
                data:
                  download_url: "https://xxx/plugins/xxx/11112222333344445555666677778888/1.0.0/weather-skill_1.0.0.zip"
                  asset_id: "11112222333344445555666677778888"
                  name: "weather-skill"
                  version: "1.0.0"
                  file_size: 102400
                  checksum_sha256: "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"
                message: "ok"
        '404':
          description: Skill 或版本不存在
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '422':
          description: 参数校验失败
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '500':
          description: 服务器内部错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /api/v1/plugins/my/stars:
    get:
      summary: 获取我收藏的 Skill 列表
      description: 返回当前用户收藏（star）的 Skill 分页列表。
      operationId: getMyStars
      tags:
        - 用户互动
      security:
        - bearerAuth: []
      parameters:
        - name: page
          in: query
          required: false
          description: 页码
          schema:
            type: integer
            minimum: 1
            example: 1
        - name: page_size
          in: query
          required: false
          description: 每页条数
          schema:
            type: integer
            minimum: 1
            maximum: 100
            example: 20
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillListResponse'
        '401':
          description: 未授权 / token 无效

  /api/v1/plugins/my/likes:
    get:
      summary: 获取我点赞的 Skill 列表
      description: 返回当前用户点赞（like）的 Skill 分页列表。
      operationId: getMyLikes
      tags:
        - 用户互动
      security:
        - bearerAuth: []
      parameters:
        - name: page
          in: query
          required: false
          description: 页码
          schema:
            type: integer
            minimum: 1
            example: 1
        - name: page_size
          in: query
          required: false
          description: 每页条数
          schema:
            type: integer
            minimum: 1
            maximum: 100
            example: 20
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SkillListResponse'
        '401':
          description: 未授权 / token 无效

  /api/v1/plugins/{asset_id}/view:
    post:
      summary: 浏览量 +1
      description: 对指定资产增加一次浏览计数，无需鉴权。
      operationId: postView
      tags:
        - 用户互动
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [view_count]
                    properties:
                      view_count:
                        type: integer
                        description: 更新后的浏览数
        '404':
          description: 资产不存在

  /api/v1/plugins/{asset_id}/interact:
    post:
      summary: 点赞/收藏切换
      description: |
        对指定资产执行点赞（like）或收藏（star）切换操作。已存在则取消，不存在则添加。
        - 不能对自己的 Skill 执行互动操作。
        - Skill 未通过审核时不可互动。
      operationId: postInteract
      tags:
        - 用户互动
      security:
        - bearerAuth: []
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - action_type
              properties:
                action_type:
                  type: string
                  enum: [like, star]
                  description: 互动类型
            example:
              action_type: "like"
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [action_type, active]
                    properties:
                      action_type:
                        type: string
                        enum: [like, star]
                        description: 互动类型
                      active:
                        type: boolean
                        description: 当前状态（true=已点赞/收藏，false=已取消）
                      like_count:
                        type: integer
                        nullable: true
                        description: 更新后的点赞数（action_type=like 时返回）
                      star_count:
                        type: integer
                        nullable: true
                        description: 更新后的收藏数（action_type=star 时返回）
        '400':
          description: 无效的 action_type
        '401':
          description: 未授权 / token 无效
        '403':
          description: 权限不足（不能对自己的 Skill 互动，或 Skill 未通过审核）
        '404':
          description: 资产不存在

  /api/v1/plugins/interactions/batch:
    get:
      summary: 批量查询互动状态
      description: 批量查询多个资产的点赞/收藏计数及当前用户互动状态，最多 50 个。
      operationId: getInteractionsBatch
      tags:
        - 用户互动
      parameters:
        - name: asset_ids
          in: query
          required: false
          description: 资产 ID 列表（最多 50 个）
          schema:
            type: array
            items:
              type: string
            maxItems: 50
          style: form
          explode: true
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [items]
                    properties:
                      items:
                        type: array
                        items:
                          $ref: '#/components/schemas/AssetInteractionState'
        '400':
          description: asset_ids 超过 50 个

  /api/v1/plugins/{asset_id}/interactions:
    get:
      summary: 查询单个资产互动状态
      description: 查询指定资产的点赞/收藏计数及当前用户互动状态。未登录时 liked/starred 均为 false。
      operationId: getInteractions
      tags:
        - 用户互动
      parameters:
        - name: asset_id
          in: path
          required: true
          schema:
            type: string
          description: 资产 ID
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/UserInteractionState'

  /api/v1/notifications:
    get:
      summary: 获取通知列表
      description: 返回当前用户的通知列表及未读数。
      operationId: getNotifications
      tags:
        - 通知
      security:
        - bearerAuth: []
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    $ref: '#/components/schemas/SiteNotificationListData'
        '401':
          description: 未授权 / token 无效

  /api/v1/notifications/read-all:
    post:
      summary: 全部标记已读
      description: 将当前用户所有通知标记为已读，返回更新的条数。
      operationId: postNotificationsReadAll
      tags:
        - 通知
      security:
        - bearerAuth: []
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    properties:
                      updated:
                        type: integer
                        description: 标记已读的条数
        '401':
          description: 未授权 / token 无效

  /api/v1/site/privacy-statement:
    get:
      summary: 获取隐私声明
      description: 直接返回 Markdown 正文（非 JSON）；浏览器新标签打开即可查看。
      operationId: getPrivacyStatement
      tags:
        - 站点公开信息
      responses:
        '200':
          description: 隐私声明 Markdown 内容
          content:
            text/markdown:
              schema:
                type: string

  /api/v1/auth/oauth/{provider}/start:
    get:
      summary: 发起 OAuth 授权
      description: |
        浏览器直接访问此 URL，服务端将生成 `state` 并 302 重定向到对应厂商（GitCode / GitHub）的授权页。
        用户在厂商页面完成授权后，厂商会回调 `callback` 接口。
        此接口返回 **302 重定向**，非 JSON 响应。
      operationId: oauthStart
      tags:
        - 认证
      parameters:
        - name: provider
          in: path
          required: true
          description: OAuth 提供商
          schema:
            type: string
            enum: [gitcode, github]
      responses:
        '302':
          description: 重定向到厂商授权页（Location 头包含授权 URL 与 state 参数）
        '404':
          description: 该提供商 OAuth 未启用
        '503':
          description: 该提供商 OAuth 未正确配置（缺少 client_id / redirect_uri）

  /api/v1/auth/oauth/{provider}/callback:
    get:
      summary: OAuth 授权回调
      description: |
        厂商授权页完成授权后回调此接口。服务端用 `code` 换取 `access_token`，拉取用户信息，
        将结果写入一次性 session 存储，然后 302 重定向前端 `/login?oauth_session=...&oauth_provider=...`。
        此接口返回 **302 重定向**，非 JSON 响应。客户端不应直接调用，而是由厂商授权页自动跳转。
      operationId: oauthCallback
      tags:
        - 认证
      parameters:
        - name: provider
          in: path
          required: true
          description: OAuth 提供商
          schema:
            type: string
            enum: [gitcode, github]
        - name: code
          in: query
          required: false
          description: 厂商返回的授权码
          schema:
            type: string
        - name: state
          in: query
          required: false
          description: 发起授权时生成的 state，用于防 CSRF
          schema:
            type: string
        - name: error
          in: query
          required: false
          description: 厂商返回的错误标识（用户拒绝授权等）
          schema:
            type: string
        - name: error_description
          in: query
          required: false
          description: 厂商返回的错误描述
          schema:
            type: string
      responses:
        '302':
          description: |
            成功时重定向前端 `/login?oauth_session=...&oauth_provider=...`；
            失败时重定向前端 `/login?oauth_error=...&oauth_status=...&oauth_error_code=...&oauth_error_class=...&oauth_error_name=...`
        '404':
          description: 该提供商 OAuth 未启用

  /api/v1/auth/oauth/{provider}/session:
    post:
      summary: 一次性兑换 OAuth session
      description: |
        客户端通过 OAuth 回调重定向中获得的 `oauth_session` 一次性兑换 access_token 与展示用用户信息（不返回 refresh_token）。
        使用 POST + JSON body，避免 session 出现在 GET query（反代日志、Referer）。
        session 有效期 120 秒，仅可消费一次。
      operationId: oauthSessionExchange
      tags:
        - 认证
      parameters:
        - name: provider
          in: path
          required: true
          description: OAuth 提供商
          schema:
            type: string
            enum: [gitcode, github]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - session
              properties:
                session:
                  type: string
                  minLength: 8
                  maxLength: 256
                  description: OAuth 回调重定向中获得的 oauth_session 值
      responses:
        '200':
          description: 兑换成功
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [provider, access_token, token_type, user]
                    properties:
                      provider:
                        type: string
                        enum: [gitcode, github]
                        description: OAuth 提供商
                      access_token:
                        type: string
                        description: 访问令牌
                      token_type:
                        type: string
                        description: 令牌类型（通常为 bearer）
                      user:
                        type: object
                        required: [id, name, login]
                        properties:
                          id:
                            type: string
                            description: 用户 ID
                          name:
                            type: string
                            description: 显示名称
                          login:
                            type: string
                            description: 登录名
                          avatar_url:
                            type: string
                            nullable: true
                            description: 头像 URL
        '400':
          description: 会话已过期或无效（结构化错误对象，`detail.error_code` 可稳定识别）
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
              example:
                detail:
                  code: 400
                  error: "oauth_session_expired"
                  message: "会话已过期或无效"
                  data: null
                  http_status: 400
                  error_class: "auth"
                  error_code: "SKILLHUB_OAUTH_SESSION_EXPIRED"

  /api/v1/auth/me:
    get:
      summary: 获取当前用户信息
      description: 校验当前 Bearer Token，按 X-OAuth-Provider 选择厂商用户接口（缺省 gitcode），返回用户信息。
      operationId: authMe
      tags:
        - 认证
      security:
        - bearerAuth: []
      parameters:
        - name: X-OAuth-Provider
          in: header
          required: false
          description: OAuth 提供商（缺省 gitcode）
          schema:
            type: string
            enum: [gitcode, github]
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [code, message, data]
                properties:
                  code:
                    type: integer
                    example: 200
                  message:
                    type: string
                    example: ok
                  data:
                    type: object
                    required: [id, name, login]
                    properties:
                      id:
                        type: string
                        description: 用户 ID
                      name:
                        type: string
                        description: 显示名称
                      login:
                        type: string
                        description: 登录名
                      avatar_url:
                        type: string
                        nullable: true
                        description: 头像 URL
                      is_market_moderation_admin:
                        type: boolean
                        description: 是否为市场审核管理员
        '401':
          description: 未授权 / token 无效

components:
  schemas:
    SkillPublishResponse:
      type: object
      required:
        - code
        - data
        - message
      properties:
        code:
          type: integer
          example: 200
        data:
          type: object
          required:
            - plugin_id
            - name
            - version
            - status
            - published_at
            - storage_url
          properties:
            plugin_id:
              type: string
              description: Skill ID
            name:
              type: string
              description: Skill 名称
            version:
              type: string
              description: 版本号
            status:
              type: string
              description: "版本记录的存储态（market_asset_versions.status，如 ACTIVE），表示「版本行是否有效」，与审核/发布阶段无关；判断 Skill 是否已上架请用 publish_result / moderation_status，勿用此字段"
            published_at:
              type: string
              format: date-time
              description: 发布时间
            storage_url:
              type: string
              description: Skill 包存储 URL
            plugin_type:
              type: string
              nullable: true
              description: 插件类型
            publish_result:
              type: string
              nullable: true
              description: "Skill 发布/审核阶段（与 status 不同轴）：reviewing 系统审查中 | pending_moderation 待人工审核 | publish_success 已上架 | publish_failed 失败"
        message:
          type: string
          description: 响应消息

    VersionConflictResponse:
      description: 版本冲突错误响应（结构化错误对象，外层 detail 包裹）
      type: object
      required:
        - detail
      properties:
        detail:
          type: object
          required:
            - code
            - error
            - message
          properties:
            code:
              type: integer
              example: 409
            data:
              type: object
              nullable: true
            error:
              type: string
            message:
              type: string

    SkillListItem:
      type: object
      required:
        - asset_id
        - asset_type
        - name
        - display_name
        - publisher_id
        - publisher_name
      properties:
        asset_id:
          type: string
        asset_type:
          type: string
        name:
          type: string
        display_name:
          type: string
          nullable: true
        short_desc:
          type: string
          nullable: true
        detail_desc:
          type: string
          nullable: true
        icon_uri:
          type: string
          nullable: true
        publisher_id:
          type: string
        publisher_name:
          type: string
        tags:
          type: array
          items:
            type: string
          nullable: true
        category_id:
          type: string
          nullable: true
        category_name:
          type: string
          nullable: true
        certification:
          type: string
          nullable: true
        plugin_type:
          type: string
          nullable: true
        publish_result:
          type: string
          nullable: true
          description: "Skill 发布结果：reviewing | pending_moderation | publish_success | publish_failed"
        moderation_status:
          type: string
          nullable: true
          description: "Skill 人工审核聚合状态：PENDING | APPROVED | REJECTED"
        moderation_reject_reason:
          type: string
          nullable: true
        latest_version:
          type: string
          nullable: true
        public_latest_version:
          type: string
          nullable: true
          description: 当前对外可下载/展示的已通过审核最新版本
        all_versions:
          type: array
          description: 对当前用户可见的版本号（发布时间线升序）
          items:
            type: string
          example: ["0.1.0", "1.0.0"]
        has_pending_skill_version:
          type: boolean
          description: "Skill：作者或审核员可见；仍有任一版本在审核中时为 true"
        skill_version_moderation:
          type: object
          nullable: true
          description: "Skill：仅发布者或审核员；version -> PENDING|APPROVED|REJECTED"
          additionalProperties:
            type: string
        skill_version_publish_result:
          type: object
          nullable: true
          description: "Skill：仅发布者或审核员；version -> reviewing|pending_moderation|publish_success|publish_failed"
          additionalProperties:
            type: string
        view_count:
          type: integer
          nullable: true
        install_count:
          type: integer
          nullable: true
        like_count:
          type: integer
          nullable: true
        star_count:
          type: integer
          nullable: true
        review_count:
          type: integer
          nullable: true
        average_rating:
          type: number
          nullable: true
        create_time:
          type: integer
          nullable: true
        update_time:
          type: integer
          nullable: true
        pin_order:
          type: integer
          nullable: true
          description: 置顶顺序，非空表示置顶，数字越小越靠前
        viewer_is_market_moderation_admin:
          type: boolean
          description: 当前请求者是否为市场审核管理员
        storage_mode:
          type: string
          nullable: true
          description: 存储模式，如 `git`（来自 Git 源同步）；与 declared / commit 共同决定版本展示
        resolved_commit_sha:
          type: string
          nullable: true
          description: Git 同步解析到的 commit 全串
        declared_skill_version:
          type: string
          nullable: true
          description: SKILL 声明的版本；为空且 storage_mode=git 时可用 commit 短码展示
        git_version_display_as_commit:
          type: boolean
          description: 为 true 时前端将 latest_version 文案显示为 commit 短码

    SkillListResponse:
      type: object
      required:
        - page
        - page_size
        - total
        - items
      properties:
        page:
          type: integer
        page_size:
          type: integer
        total:
          type: integer
        items:
          type: array
          items:
            $ref: '#/components/schemas/SkillListItem'

    SkillVersionDetail:
      type: object
      required:
        - asset_id
        - version
        - asset_type
        - name
        - display_name
        - publisher_id
        - publisher_name
      properties:
        asset_id:
          type: string
        version:
          type: string
        asset_type:
          type: string
        plugin_type:
          type: string
          nullable: true
        moderation_status:
          type: string
          nullable: true
          description: "Skill 人工审核聚合状态：PENDING | APPROVED | REJECTED；非 skill 多为 APPROVED"
        moderation_reject_reason:
          type: string
          nullable: true
          description: 审核不通过原因
        publish_result:
          type: string
          nullable: true
          description: "Skill 发布结果：reviewing | pending_moderation | publish_success | publish_failed"
        publish_failed_reason:
          type: string
          nullable: true
          description: 发布失败原因；系统审查失败时为系统审查原因，人工审核驳回时仍以 moderation_reject_reason / version_moderation_reject_reason 为准
        version_moderation_status:
          type: string
          nullable: true
          description: "当前版本的人工审核状态；Skill：PENDING | APPROVED | REJECTED"
        version_moderation_reject_reason:
          type: string
          nullable: true
          description: 当前版本审核驳回原因
        name:
          type: string
        display_name:
          type: string
        short_desc:
          type: string
          nullable: true
        detail_desc:
          type: string
          nullable: true
        publisher_id:
          type: string
        publisher_name:
          type: string
        tags:
          type: array
          items:
            type: string
          nullable: true
        category_id:
          type: string
          nullable: true
        category_name:
          type: string
          nullable: true
        certification:
          type: string
          nullable: true
        changelog:
          type: string
          nullable: true
        file_path:
          type: string
          nullable: true
        icon_uri:
          type: string
          nullable: true
        review_summary:
          type: object
          nullable: true
          description: 系统审查摘要，包括审查状态、分数、风险等级、失败项数量与 AI 语义补充摘要
        review_sections:
          type: array
          nullable: true
          description: 系统审查结构化明细，按审查维度返回检查项、命中证据与结论
          items:
            type: object
        install_count:
          type: integer
          description: 资产累计下载次数
        view_count:
          type: integer
          description: 资产累计浏览次数
        update_time:
          type: integer
          nullable: true
          description: 当前版本记录上传时间（毫秒）
        viewer_is_market_moderation_admin:
          type: boolean
          description: 当前请求者是否为市场审核管理员
        storage_mode:
          type: string
          nullable: true
          description: 存储模式，如 `git`（来自 Git 源同步）
        resolved_commit_sha:
          type: string
          nullable: true
          description: Git 同步解析到的 commit
        declared_skill_version:
          type: string
          nullable: true
          description: SKILL 声明的版本
        git_version_display_as_commit:
          type: boolean
          description: 为 true 时本行 version 显示为 commit 短码（仅当 version 等于资产 latest_version）

    DownloadResponse:
      type: object
      required:
        - code
        - data
        - message
      properties:
        code:
          type: integer
          example: 200
        data:
          $ref: '#/components/schemas/SkillDownloadData'
        message:
          type: string
          example: ok

    SkillDownloadData:
      type: object
      required:
        - download_url
        - asset_id
        - name
        - version
        - file_size
        - checksum_sha256
      properties:
        download_url:
          type: string
          description: 下载链接
        asset_id:
          type: string
          description: 资产 ID
        name:
          type: string
          description: Skill 名称
        version:
          type: string
          description: 版本号
        file_size:
          type: integer
          description: 文件大小（字节）
        checksum_sha256:
          type: string
          description: 文件 SHA256 校验和

    PluginTemplatePresignData:
      type: object
      required:
        - download_url
        - expires_in
        - filename
      properties:
        download_url:
          type: string
          description: 对象存储预签名 GET URL，短期有效；可直接跳转或新开页下载
        expires_in:
          type: integer
          description: 该 URL 的有效期（秒）
          example: 1800
        filename:
          type: string
          description: 建议本地保存的文件名
          example: "demo-skill.zip"

    AssetInteractionState:
      type: object
      required:
        - asset_id
        - liked
        - starred
        - like_count
        - star_count
      properties:
        asset_id:
          type: string
        liked:
          type: boolean
          description: 当前用户是否已点赞
        starred:
          type: boolean
          description: 当前用户是否已收藏
        like_count:
          type: integer
          description: 点赞总数
        star_count:
          type: integer
          description: 收藏总数

    UserInteractionState:
      type: object
      required:
        - liked
        - starred
      properties:
        liked:
          type: boolean
          description: 当前用户是否已点赞
        starred:
          type: boolean
          description: 当前用户是否已收藏
        like_count:
          type: integer
          nullable: true
          description: 点赞总数
        star_count:
          type: integer
          nullable: true
          description: 收藏总数

    SiteNotificationListData:
      type: object
      required:
        - items
        - unread_count
      properties:
        items:
          type: array
          items:
            $ref: '#/components/schemas/SiteNotificationItem'
        unread_count:
          type: integer
          description: 未读通知数

    SiteNotificationItem:
      type: object
      required:
        - id
        - template
        - message
        - created_at_ms
        - read
      properties:
        id:
          type: integer
        template:
          type: string
        message:
          type: string
        created_at_ms:
          type: integer
          description: 创建时间（毫秒时间戳）
        read:
          type: boolean
          description: 是否已读


    SkillImportResponse:
      type: object
      required:
        - summary
        - results
      properties:
        summary:
          $ref: '#/components/schemas/SkillImportSummary'
        results:
          type: array
          items:
            $ref: '#/components/schemas/SkillImportItemResult'

    SkillImportSummary:
      type: object
      required:
        - total
        - ok
        - failed
      properties:
        total:
          type: integer
          description: 集合包内顶层 Skill 目录总数
        ok:
          type: integer
          description: 成功导入条数
        failed:
          type: integer
          description: 失败条数（仅含已尝试并记入 results 的条目；fail_fast 提前结束时 ok + failed 可能小于 total）
        skipped:
          type: integer
          default: 0
          description: 跳过条数（如同步时内容 MD5 未变而无需重复导入）

    SkillImportItemResult:
      type: object
      required:
        - entry
        - status
      properties:
        entry:
          type: string
          description: 集合包内 Skill 目录名
        status:
          type: string
          enum: [ok, error, skipped]
          description: 导入结果；`skipped` 表示内容未变化被跳过（不计入 failed）
        plugin_id:
          type: string
          nullable: true
          description: 成功时返回的 Skill ID
        name:
          type: string
          nullable: true
          description: 成功时返回的 Skill 名称
        version:
          type: string
          nullable: true
          description: 成功时返回的版本号
        error:
          type: string
          nullable: true
          description: 失败时的错误标识
        message:
          type: string
          nullable: true
          description: 失败时的错误描述

    SkillModerationResult:
      type: object
      required:
        - asset_id
        - moderation_status
      properties:
        asset_id:
          type: string
          description: 资产 ID
        moderation_status:
          type: string
          description: 人工审核后状态（PENDING / APPROVED / REJECTED）
        moderation_reject_reason:
          type: string
          nullable: true
          description: 驳回原因
        publish_result:
          type: string
          nullable: true
          description: "本次操作后对应版本的发布结果：pending_moderation | publish_success | publish_failed"
        version:
          type: string
          nullable: true
          description: 本次操作针对的版本号

    SkillModerationAuditListResponse:
      type: object
      required:
        - page
        - page_size
        - total
        - items
      properties:
        page:
          type: integer
        page_size:
          type: integer
        total:
          type: integer
        items:
          type: array
          items:
            $ref: '#/components/schemas/SkillModerationAuditListItem'

    SkillModerationAuditListItem:
      type: object
      required:
        - event_id
        - asset_id
        - skill_name
        - version
        - moderation_action
        - created_at_ms
      properties:
        event_id:
          type: string
          description: 审计记录 ID
        asset_id:
          type: string
          description: 资产 ID
        skill_name:
          type: string
          description: Skill 标识 name
        skill_display_name:
          type: string
          nullable: true
          description: Skill 显示名称
        version:
          type: string
          description: 审核操作的版本号
        moderation_action:
          type: string
          enum: [APPROVE, REJECT]
          description: 本次审核操作
        reject_reason:
          type: string
          nullable: true
          description: 驳回原因；通过时为空
        created_at_ms:
          type: integer
          description: 操作时间（毫秒时间戳）

    GitSourceCreateRequest:
      type: object
      required:
        - repo_url
      properties:
        name:
          type: string
          maxLength: 128
          default: ""
          description: 兼容旧客户端；服务端以 repo_url 为准展示，可留空（传 null 视为空串）
        repo_url:
          type: string
          maxLength: 512
          description: https:// 或 http:// 公有克隆地址
        ref:
          type: string
          maxLength: 256
          default: main
          description: 分支名或 tag；不支持 commit SHA 作为拉取目标
        skills_subpath:
          type: string
          nullable: true
          maxLength: 512
          description: 仓库内技能根目录相对路径，缺省为仓库根

    GitSourceItem:
      type: object
      required:
        - id
        - name
        - repo_url
        - ref
        - created_by_user_id
        - create_time_ms
        - update_time_ms
      properties:
        id:
          type: string
        name:
          type: string
        repo_url:
          type: string
        ref:
          type: string
        skills_subpath:
          type: string
          nullable: true
        git_source_dedup_key:
          type: string
          nullable: true
          description: repo + ref + skills_subpath 的全局去重键
        created_by_user_id:
          type: string
        create_time_ms:
          type: integer
        update_time_ms:
          type: integer
        last_index_status:
          type: string
          nullable: true
          description: 最近一次同步状态，如 syncing / success / failed
        last_index_error:
          type: string
          nullable: true
          description: 最近一次同步失败原因
        last_indexed_at_ms:
          type: integer
          nullable: true
          description: 最近一次成功同步时间戳（ms）

    GitSourceListResponse:
      type: object
      required:
        - items
      properties:
        items:
          type: array
          items:
            $ref: '#/components/schemas/GitSourceItem'

    GitSyncAcceptedResponse:
      description: 创建 / 再次同步返回；后台执行，客户端轮询 git-sources 列表查看进度。
      type: object
      required:
        - source_id
      properties:
        source_id:
          type: string
        status:
          type: string
          default: syncing
        message:
          type: string
          example: Git 同步已在后台执行，请在列表中查看进度与结果

    VersionFileEntry:
      type: object
      required:
        - path
        - size
      properties:
        path:
          type: string
          description: zip 包内相对路径
        size:
          type: integer
          description: 文件字节数

    VersionFilesData:
      type: object
      required:
        - files
      properties:
        files:
          type: array
          items:
            $ref: '#/components/schemas/VersionFileEntry'
        content:
          type: string
          nullable: true
          description: with_content 请求的文件文本内容；二进制或未请求时为 null
        content_path:
          type: string
          nullable: true
          description: 实际返回内容的文件路径

    ErrorResponse:
      description: |
        结构化错误响应。外层固定包裹 `detail`，内含 `code` / `http_status` / `error` / `error_class` / `error_code` / `message` / `data`；
        422 请求校验错误会把 Pydantic 错误数组放入 `detail.details`。
      type: object
      required:
        - detail
      properties:
        detail:
          description: 业务错误详情对象
          type: object
          required:
            - code
            - error
            - message
          properties:
            code:
              type: integer
              description: 兼容字段，等同 HTTP 状态码
            http_status:
              type: integer
              nullable: true
              description: HTTP 状态码
            error:
              type: string
              description: 兼容错误名
            error_class:
              type: string
              nullable: true
              description: 错误大类，如 validation / auth / permission / upstream / internal
            error_code:
              type: string
              nullable: true
              description: 稳定机器码
            message:
              type: string
              description: 人类可读错误描述
            data:
              nullable: true
              description: 兼容扩展字段
            details:
              nullable: true
              description: 白名单业务上下文或 422 校验错误数组
            meta:
              nullable: true
              description: 关联信息与诊断信息

  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      description: 用户通过 OAuth 登录获取的访问令牌
    systemTokenAuth:
      type: apiKey
      in: header
      name: X-System-Token
      description: 系统令牌，由服务端配置，仅用于服务端间调用
```
