# Agent 资产

openJiuwen Agentic Hub 市场除 Skill / SwarmSkill 外，支持四类 JiuwenSwarm Agent 资产。复用同一套发布、列表、详情与下载 API，通过 `plugin_type` / `asset_type` 区分。

## 类型对照

| 产品名 | `plugin_type` | 内层入口 | 用途 |
|--------|---------------|----------|------|
| 插件 | `agent-plugin` | `manifest.json`（`package_type: plugin`） | 挂载能力，无独立 Agent 身份 |
| 连接器 | `agent-mcp` | `manifest.json`（`package_type: mcp`） | MCP / CLI / Skill-only 集成包 |
| 专家 | `agent-template` | `manifest.json`（`package_type: agent_template`） | 完整 Agent 角色包 |
| 专家团 | `agent-group` | `manifest.json`（`package_type: agent_group`） | 含 leader 与成员的 AgentGroup 包 |

`asset_type` 与 `plugin_type` 同值。对象存储前缀分别为 `agent-plugins/`、`agent-templates/`、`agent-groups/`、`agent-mcps/`。

## 包结构

```text
<outer>/plugin.yaml          # 市场外层，全包唯一
<outer>/<name>/              # 内层目录名 = plugin.yaml.name
    manifest.json            # 运行时入口（四类均有）
    ...
```

- 可上传**裸原生包**（仅内层目录内容），服务端按 `asset_name` 等表单字段自动包装。
- 可上传**已包装包**；表单 `display_name` / `description` / `tags` 等可覆盖外层展示字段。
- Agent 包装包：`plugin_version` 须与内层 `manifest.json.version` 一致，否则 `400 invalid_version`。
- 路径均相对内层包根，不得含 `..` 或绝对路径。
- 静态安全扫描：manifest 引用的 `mcp.json` 与包内脚本不得含危险命令。
- 市场图标：专家、专家团与插件优先内层 `manifest.avatar`（PNG，如 `avatars/avatar.png`），否则外层 `<outer>/icon.png`；连接器见下文 `manifest.icon`。Hub 上传为版本目录 `icon.png`，列表与详情返回 `icon_uri`。无图标时字段为空，不写占位图、不塞 ZIP 下载链接。

完整字段规范见产品侧《Agent资产组成文件说明》；下文为 **openJiuwen Agentic Hub 发布校验**要点。

## Hub 校验原则

Hub 只做**包结构与安全**校验，运行时文件是否齐留给 JiuwenSwarm：

| 项 | Hub 行为 |
|----|----------|
| `README.md` | 可选；有则作为详情 `detail_desc` |
| `persona` / `skills[]` / `tools[]` / `rails[]` / `memories[]` / `rubrics[]` / `mcps[]` / `model` / `subagents[]` | 声明时只检查形态与路径安全（禁止 `..`、绝对路径）；**不因文件缺失拒发**，不校验 SKILL frontmatter / JSON 内容 |
| 插件能力 | **不要求**至少一种能力组件 |
| 连接器 | **必须**有 `manifest.json`；**拒绝**无 manifest 的旧包（仅 `mcp.json` 等） |
| 连接器 `integration.file` | `stdio-mcp` / `remote-mcp` / `cli` 仍须存在，且 type 与 `mcp.json` / `cli.json` 内容一致 |
| 图标 | 声明但文件不存在则跳过；文件存在且作为市场图标时须为合法 PNG |
| 仍拦截 | 危险命令/脚本；插件禁止根字段 `persona` / `agent_card` / `model` / `subagents` / `memories` / `rubrics`；token 凭据 schema 与占位符 |

## 发布与审核

- 接口：`POST /api/v1/plugins`（Bearer 或 System Token）。
- 普通用户：`publish_result: pending_moderation`；系统管理员可跳过审核。
- 响应含 `asset_id`（同 `plugin_id`）、`asset_type`、`plugin_type`。
- 批量导入：`POST /api/v1/plugins/import`，裸目录须含 `manifest.json`，`package_type` 识别类型。

## 检索

- `GET /api/v1/plugins` 须显式传 `plugin_type=agent-plugin` / `agent-template` / `agent-group` / `agent-mcp`（可逗号多值）。
- 不传类型时默认仅 `skill,swarmskill`，**不会**混入 Agent 资产。
- `search_keyword` 走数据库关键词匹配，不走语义检索。

## 插件（`package_type: plugin`）

**必填：** `version`、`package_type`、`id`（须等于 `plugin.yaml.name`）

**可选：** `skills[]`、`tools[]`、`rails[]`、`mcps[]`（Hub 不因声明文件缺失拒发）、`avatar`（PNG；文件不存在则跳过）

**禁止根字段：** `persona`、`agent_card`、`model`、`subagents`、`memories`、`rubrics`

```json
{
  "version": "1.0.0",
  "package_type": "plugin",
  "id": "my-plugin",
  "name": "展示名",
  "description": "描述",
  "avatar": "avatars/avatar.png",
  "skills": [{ "dir": "skills/foo", "mode": "all" }],
  "tools": [{ "file": "tools/t.py", "class": "MyTool" }],
  "mcps": [{ "connector": "amap" }]
}
```

`mcps[]` 支持 `connector`（宿主 connector）或 `file` / `dir`（包内 MCP 配置）。市场图标优先 `manifest.avatar`（PNG），否则外层 `icon.png`。

## 专家（`package_type: agent_template`）

**必填：** `version`、`package_type`、`name`（须等于 `plugin.yaml.name`）、`description`

**可选：** `persona`、`skills[]`、`tools[]`、`rails[]`、`memories[]`、`rubrics[]`、`model`、`subagents[]`、`mcps[]`（Hub 不因声明文件缺失拒发）

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

- `persona`：可选；声明时只检查 `dir` 路径安全，不要求目录内有 `.md`。
- `model.file` / `subagents[].dir`：有则检查路径安全；Hub 不读 JSON、不要求 `.subagent.json`。

## 专家团（`package_type: agent_group`）

**必填：** `version`、`package_type: agent_group`、`name`（须等于 `plugin.yaml.name`）、`description`

外层 `runtime.type` 为 `agent-group`。Hub 不因缺少 `agents/` 成员目录或 `leader` 拒发。

**可选：** `agents[]`（成员名字符串）、`skills[]`（技能名或 `{dir}`）、`instruction`、`avatar`

```json
{
  "version": "1.0.0",
  "package_type": "agent_group",
  "name": "my-team",
  "description": "一句话描述",
  "instruction": "协调成员完成目标",
  "agents": ["leader", "analyst"]
}
```

- `agents[]`：声明时须为非空字符串且不得含路径分隔符；**不要求**包含 `leader`，也不检查 `agents/<name>/` 是否存在。
- `skills[]`：可为技能名字符串，或带 `dir` 的对象（只查路径安全）。

## 连接器（`package_type: mcp`）

**必填：** `version`、`package_type`、`id`（须等于 `plugin.yaml.name`）、`name`、`description`、`integration.type`

**`integration.type` 取值：**

| 值 | 条件文件 |
|----|----------|
| `stdio-mcp` | `integration.file` → `mcp.json`（含 `command`） |
| `remote-mcp` | `integration.file` → `mcp.json`（含 `url`） |
| `cli` | `integration.file` → `cli.json` |
| `skill-only` | 无 `integration.file`；不强制包内存在 `SKILL.md` |

**可选：** `credentials`（`token` 须指向已存在的 `token-schema.json`）、`skills[]`（不因缺 `SKILL.md` 拒发）、`icon`（PNG；文件不存在则跳过）

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
- MCP 图标由 manifest `icon` 引用 PNG；Hub 上传后存为市场 `icon.png`，列表与详情返回 `icon_uri`。
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
- [openJiuwen Agentic Hub 接口参考](../7.%20API参考/openJiuwen-Agentic-Hub-接口参考.md)
