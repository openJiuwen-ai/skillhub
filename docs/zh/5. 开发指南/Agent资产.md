# Agent 资产

SkillHub 市场除 Skill / SwarmSkill 外，支持三类 JiuwenSwarm Agent 资产。复用同一套发布、列表、详情与下载 API，通过 `plugin_type` / `asset_type` 区分。

## 类型对照

| 产品名 | `plugin_type` | 内层入口 | 用途 |
|--------|---------------|----------|------|
| Agent 插件 | `agent-plugin` | `manifest.json`（`package_type: plugin`） | 挂载能力，无独立 Agent 身份 |
| Agent 模板 | `agent-template` | `manifest.json`（`package_type: agent_template`） | 完整 Agent 角色包 |
| Agent 连接器 | `agent-mcp` | `manifest.json`（`package_type: mcp`） | MCP / CLI / Skill-only 集成包 |

`asset_type` 与 `plugin_type` 同值。对象存储前缀分别为 `agent-plugins/`、`agent-templates/`、`agent-mcps/`。

## 包结构

```text
<outer>/plugin.yaml          # 市场外层，全包唯一
<outer>/<name>/              # 内层目录名 = plugin.yaml.name
    manifest.json            # 运行时入口（三类均有）
    ...
```

- 可上传**裸原生包**（仅内层目录内容），服务端按 `asset_name` 等表单字段自动包装。
- 可上传**已包装包**；表单 `display_name` / `description` / `tags` 等可覆盖外层展示字段。
- Agent 包装包：`plugin_version` 须与内层 `manifest.json.version` 一致，否则 `400 invalid_version`。
- 路径均相对内层包根，不得含 `..` 或绝对路径。
- 静态安全扫描：manifest 引用的 `mcp.json` 与包内脚本不得含危险命令。
- 市场外层图标：`<outer>/icon.png`（PNG，可选），用于 plugin/template 列表展示。

完整字段规范见产品侧《Agent资产组成文件说明》；下文为 **SkillHub 发布校验**要点。

## Hub 校验原则

Hub 只做**包结构与安全**校验，**不比 JiuwenSwarm 运行时更严**：

| 项 | Hub 行为 |
|----|----------|
| `README.md` | 可选；有则作为详情 `detail_desc` |
| Agent 模板 `persona` | 可选；声明则校验目录内存在 `.md` |
| 内层 `skills/` | 仅校验 manifest 声明路径存在 `SKILL.md`，**不校验** SKILL frontmatter |
| Agent 插件能力 | **不要求**至少一种能力组件 |
| Agent 连接器 | **必须**有 `manifest.json`；**拒绝**无 manifest 的旧包（仅 `mcp.json` 等） |

## 发布与审核

- 接口：`POST /api/v1/plugins`（Bearer 或 System Token）。
- 普通用户：`publish_result: pending_moderation`；系统管理员可跳过人工审核。
- 响应含 `asset_id`（同 `plugin_id`）、`asset_type`、`plugin_type`。
- 批量导入：`POST /api/v1/plugins/import`，裸目录须含 `manifest.json`，`package_type` 识别类型。

## 检索

- `GET /api/v1/plugins` 须显式传 `plugin_type=agent-plugin` / `agent-template` / `agent-mcp`（可逗号多值）。
- 不传类型时默认仅 `skill,swarmskill`，**不会**混入 Agent 资产。
- `search_keyword` 走数据库关键词匹配，不走语义检索。

## Agent 插件（`package_type: plugin`）

**必填：** `version`、`package_type`、`id`（须等于 `plugin.yaml.name`）

**可选能力（声明则文件须存在）：** `skills[]`、`tools[]`、`rails[]`、`mcps[]`

**禁止根字段：** `persona`、`agent_card`、`model`、`subagents`、`memories`、`rubrics`

```json
{
  "version": "1.0.0",
  "package_type": "plugin",
  "id": "my-plugin",
  "name": "展示名",
  "description": "描述",
  "skills": [{ "dir": "skills/foo", "mode": "all" }],
  "tools": [{ "file": "tools/t.py", "class": "MyTool" }],
  "mcps": [{ "connector": "amap" }]
}
```

`mcps[]` 支持 `connector`（宿主 connector）或 `file` / `dir`（包内 MCP 配置）。

## Agent 模板（`package_type: agent_template`）

**必填：** `version`、`package_type`、`name`（须等于 `plugin.yaml.name`）、`description`

**可选（声明则文件须存在）：** `persona`、`skills[]`、`tools[]`、`rails[]`、`memories[]`、`rubrics[]`、`model`、`subagents[]`、`mcps[]`

```json
{
  "version": "1.0.0",
  "package_type": "agent_template",
  "name": "my-template",
  "description": "一句话描述",
  "persona": { "dir": "persona" },
  "skills": [{ "dir": "skills/foo", "mode": "all" }]
}
```

- `persona`：可选；声明时 `persona/` 下至少一个 `.md`。
- `model.file`：指向的 JSON 顶层须含 `model` 字段。
- `subagents[].dir`：目录内至少一个 `.subagent.json`（合法 JSON 对象）。

## Agent 连接器（`package_type: mcp`）

**必填：** `version`、`package_type`、`id`（须等于 `plugin.yaml.name`）、`name`、`description`、`integration.type`

**`integration.type` 取值：**

| 值 | 条件文件 |
|----|----------|
| `stdio-mcp` | `integration.file` → `mcp.json`（含 `command`） |
| `remote-mcp` | `integration.file` → `mcp.json`（含 `url`） |
| `cli` | `integration.file` → `cli.json` |
| `skill-only` | 至少一个 `skills/**/SKILL.md`（可通过 `skills[]` 或包内平铺声明） |

**可选：** `credentials`（`token` 须指向 `token-schema.json`）、`skills[]`、`icon`（PNG，如 `icon.png`）

```json
{
  "version": "1.0.0",
  "package_type": "mcp",
  "id": "github",
  "name": "GitHub",
  "description": "描述",
  "integration": { "type": "remote-mcp", "file": "mcp.json" },
  "credentials": { "type": "token", "file": "token-schema.json" },
  "icon": "icon.png",
  "skills": [{ "dir": "skills", "mode": "all" }]
}
```

- `integration.type` 须与 `mcp.json` 内容一致（stdio / remote）。
- `mcp.json` / `cli.json` 中 `${VAR}` 占位符须在 `token-schema.json` 有对应项（`cli-oauth` 除外）。
- MCP 图标由 manifest `icon` 引用 PNG；Hub 上传后存为市场 `icon.png`，详情返回 `icon_uri`。
- **不支持** `icon.svg`；**不支持**无 `manifest.json` 的旧包。

## 常见错误码

| error | 含义 |
|-------|------|
| `invalid_agent_mcp` | MCP manifest 或关联文件不合法 |
| `invalid_agent_plugin_manifest` / `invalid_agent_plugin_capability` | 插件 manifest 或能力引用不合法 |
| `invalid_manifest_json` / `missing_persona` | 模板 manifest 或 persona 不合法 |
| `invalid_plugin_structure` | 裸包无法识别或多外层 `plugin.yaml` |
| `invalid_version` | 请求版本与包装包内版本不一致 |
| `dangerous_content` | 包内脚本或 MCP 配置含危险命令 |

## 相关文档

- [Agent 资产列表与下载接口](../7.%20API参考/Agent资产列表与下载接口.md)
- [TeamSkillsHub 接口参考](../7.%20API参考/TeamSkillsHub-接口参考.md)
