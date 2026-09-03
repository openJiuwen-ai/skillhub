# Agent 资产列表与下载接口

面向 SkillHub 前端、WorkSwarm 等集成方。完整端点说明见 [TeamSkillsHub 接口参考](./TeamSkillsHub-接口参考.md)。

**Base URL：** `/api/v1`

## 列表 `GET /plugins`

**必传：** `plugin_type=agent-plugin` | `agent-template` | `agent-mcp`（可逗号多值）

| 参数 | 说明 |
|------|------|
| `page` / `page_size` | 分页，默认 1 / 20 |
| `search_keyword` | 名称、描述、标签关键词（DB 匹配，非语义） |
| `moderation_status` | 公开市场用 `APPROVED` |
| `asset_id` | 精确查单条 |

**列表项主要字段：** `asset_id`、`name`、`display_name`、`short_desc`、`plugin_type`、`asset_type`、`tags`、`icon_uri`（MCP 有 manifest 图标时非空）、`latest_version`

## 版本详情 `GET /plugins/{asset_id}/versions/{version}`

**鉴权：** 公开资产可不传 Token；待审版本需 Bearer 或 System Token。

**市场字段（外层）：**

| 字段 | 说明 |
|------|------|
| `display_name` / `short_desc` | 列表与详情标题、摘要 |
| `detail_desc` | 内层 `README.md` 正文（无则为空） |
| `icon_uri` | MCP 市场图标预签名 URL；plugin/template 通常为 null |
| `tags` | 市场标签 |

**内层摘要 `agent_package_profile`（只读，来自内层 `manifest.json`）：**

| 字段 | plugin | template | mcp |
|------|:------:|:--------:|:---:|
| `package_type` | `plugin` | `agent_template` | `mcp` |
| `category` / `source` | ✓ | ✓ | ✓ |
| `integration_type` | — | — | ✓ |
| `credentials_type` | — | — | ✓ |
| `persona_markdown` | — | ✓ | — |
| `quick_inputs` | ✓ | ✓ | ✓（来自 manifest `examples`） |
| `capabilities[]` | skill/tool/rail/mcp/subagent | 同左 | skill/integration |
| `manifest_tags` | ✓ | ✓ | ✓ |

`capabilities[]` 每项：`kind`、`id`、`name`、`description`。

## 下载 `GET /artifacts/{asset_id}`

| 参数 | 说明 |
|------|------|
| `version` | 版本号，默认最新已发布版 |
| `is_cli_download` | 见下表 |

| `is_cli_download` | 文件 | 内容 |
|-------------------|------|------|
| `false`（默认） | `*_raw.zip` | 仅内层原生包，根目录为 `manifest.json` 及关联文件 |
| `true` | `*.zip` | 完整市场包装（含外层 `plugin.yaml`） |

响应含 `download_url`（预签名）、`asset_type`、`plugin_type`。

## 发布 `POST /plugins`

与 Skill 共用 multipart 上传。Agent 相关表单字段：

| 字段 | 说明 |
|------|------|
| `asset_name` | 内层包 ID / 目录名 |
| `plugin_version` | 版本，须与 manifest 一致 |
| `display_name` / `description` / `tags` | 市场展示（可覆盖包内值） |
| `force` | `true` 覆盖同版本 |

请求头：`X-Checksum-SHA256`（包 SHA256）。

## 示例

```bash
# 列表：Agent 连接器
curl "http://127.0.0.1:8100/api/v1/plugins?plugin_type=agent-mcp&page=1&page_size=20"

# 详情
curl "http://127.0.0.1:8100/api/v1/plugins/{asset_id}/versions/1.0.0"

# 下载内层包（WorkSwarm 加载）
curl "http://127.0.0.1:8100/api/v1/artifacts/{asset_id}?version=1.0.0&is_cli_download=false"
```

## 相关文档

- [多资产设计方案](../5.%20开发指南/多资产设计方案.md)
- [Agent 资产](../5.%20开发指南/Agent资产.md)
