# Agent资产

openJiuwen Agentic Hub 的 Agent 资产一共六类：Skill、SwarmSkill、插件、连接器、专家、专家团。发布、列表、详情和下载走同一套 API，用 `plugin_type` 区分。

## 类型对照

| 产品名 | `plugin_type` | 入口 | 说明 |
|--------|---------------|------|------|
| [Skill](#skill) | `skill` | 根目录 `SKILL.md` | 单 Agent 技能包 |
| [SwarmSkill](#swarmskill) | `swarmskill` | `SKILL.md` 且 `kind: swarm-skill` | 多角色协作技能 |
| [插件](#插件) | `agent-plugin` | `manifest.json`（`package_type: plugin`） | 挂载能力，无独立 Agent 身份 |
| [连接器](#连接器) | `agent-mcp` | `manifest.json`（`package_type: mcp`） | MCP / CLI / Skill-only 集成包 |
| [专家](#专家) | `agent-template` | `manifest.json`（`package_type: agent_template`） | 完整 Agent 角色包 |
| [专家团](#专家团) | `agent-group` | `manifest.json`（`package_type: agent_group`） | 含成员的 AgentGroup 包 |

插件、连接器、专家、专家团的 `asset_type` 与 `plugin_type` 同值。对象存储前缀分别为 `agent-plugins/`、`agent-templates/`、`agent-groups/`、`agent-mcps/`。

不传 `plugin_type` 时，列表默认只有 Skill、SwarmSkill。查插件、连接器、专家、专家团必须显式传入对应 `plugin_type`。Skill / SwarmSkill 的关键词走语义检索；后四类固定走数据库关键词匹配。

## 版本与审核

- 版本号为 `x.y.z`，如 `1.0.0`，不含 `v` 前缀。
- 六类都要审核通过后才对外展示。审查只覆盖 Skill / SwarmSkill，且默认关闭。系统管理员发布可跳过审查和审核。
- 待审版本不会替换已经对外的版本。
- 接口：`POST /api/v1/plugins`（Bearer 或 System Token）。响应含 `asset_id`（同 `plugin_id`）、`asset_type`、`plugin_type`。

## Skill

单 Agent 的任务指令、脚本和参考资源。未声明 Swarm 类型时按 Skill 识别。

```text
example-skill/
├── SKILL.md
├── scripts/        # 可选
├── references/     # 可选
└── assets/         # 可选
```

发布根目录下也可以只放一个 Skill 子目录，且只能有一个含 `SKILL.md` 的非隐藏子目录。目录名符合 slug 规则时，必须与 frontmatter 的 `name` 一致。

```markdown
---
name: example-skill
description: Describe when and how an Agent should use this Skill.
---

# Example Skill
```

- 必须有 YAML frontmatter。
- `name` 必填，最长 64 字符，小写字母、数字和单个连字符；不能以连字符开头或结尾，也不能连续连字符。
- `description` 必填且非空。
- `display_name`、`author`、`tags` 可选。

工作区可以没有 `plugin.yaml`。CLI 发布时会按 `SKILL.md` 生成市场元数据，服务端包里的 `runtime.type` 为 `skill`。Skill 与 SwarmSkill 的差别仍看 `kind` 和 `roles`。

```bash
jiuwen-teamskills init example-skill --type skill
jiuwen-teamskills validate example-skill
jiuwen-teamskills pack example-skill --output out
```

`openjiuwen-plugin init --type skill` 与上面共用同一套校验。

## SwarmSkill

多角色分工和协作执行。在 Skill 的 frontmatter 上增加 `kind` 和 `roles`。

```markdown
---
name: example-swarm
description: Coordinate research and review roles.
kind: swarm-skill
roles:
  - id: researcher
    description: Collect evidence.
  - id: reviewer
    description: Review the result.
---
```

- `kind` 必须是 `swarm-skill`。
- `roles` 为非空数组，至少两个角色。
- 每个角色是对象，`id` 为非空字符串且不得重复。

```bash
jiuwen-teamskills init example-swarm --type swarmskill
jiuwen-teamskills validate example-swarm
```

## 插件、连接器、专家、专家团的公共包装

```text
<outer>/plugin.yaml          # 市场外层，全包唯一
<outer>/<name>/              # 内层目录名 = plugin.yaml.name
    manifest.json
```

- 可上传裸原生包，服务端按表单字段自动包装。
- 已包装包里，表单 `display_name` / `description` / `tags` 可覆盖外层展示字段。
- 包装包的 `plugin_version` 必须与 `manifest.json.version` 一致，否则 `400 invalid_version`。
- 路径相对内层包根，不得含 `..` 或绝对路径。
- Hub 只做包结构和安全校验。`persona`、`skills[]`、`tools[]` 等声明了但文件缺失时不拒发，也不校验 SKILL frontmatter 或 JSON 内容。
- 内层 `README.md` 可选；有则作为详情 `detail_desc`。
- 图标声明了但文件不存在则跳过；作为市场图标时必须是合法 PNG。无图标时 `icon_uri` 为空。
- 仍会拒绝危险命令或脚本。

批量导入 `POST /api/v1/plugins/import` 时，裸目录靠 `manifest.json` 的 `package_type` 识别类型。

## 插件

挂载能力，没有独立 Agent 身份。对象存储前缀 `agent-plugins/`。

**必填：** `version`、`package_type`（`plugin`）、`id`（等于 `plugin.yaml.name`）

**可选：** `skills[]`、`tools[]`、`rails[]`、`mcps[]`、`avatar`（PNG）

**禁止根字段：** `persona`、`agent_card`、`model`、`subagents`、`memories`、`rubrics`

不要求至少一种能力组件。`mcps[]` 可以是宿主 `connector`，或包内 `file` / `dir`。市场图标优先 `manifest.avatar`，否则外层 `icon.png`。

```json
{
  "version": "1.0.0",
  "package_type": "plugin",
  "id": "my-plugin",
  "name": "展示名",
  "description": "描述",
  "avatar": "avatars/avatar.png",
  "skills": [{ "dir": "skills/foo", "mode": "all" }],
  "tools": [{ "file": "tools/t.py", "class": "MyTool" }]
}
```

## 连接器

MCP、命令行或仅 Skill 的集成包。对象存储前缀 `agent-mcps/`。必须有 `manifest.json`；只有 `mcp.json` 的旧包会被拒绝。

**必填：** `version`、`package_type`（`mcp`）、`id`（等于 `plugin.yaml.name`）、`name`、`description`、`integration.type`

| `integration.type` | 条件文件 |
|--------------------|----------|
| `stdio-mcp` | `integration.file` → `mcp.json`（含 `command`） |
| `remote-mcp` | `integration.file` → `mcp.json`（含 `url`） |
| `cli` | `integration.file` → `cli.json` |
| `skill-only` | 无 `integration.file`；不强制包内有 `SKILL.md` |

`credentials.type=token` 时，`token-schema.json` 必须存在。`mcp.json` / `cli.json` 里的 `${VAR}` 要在 schema 中有对应项（`cli-oauth` 除外）。图标由 `manifest.icon` 引用 PNG，不支持 `icon.svg`。

```json
{
  "version": "1.0.0",
  "package_type": "mcp",
  "id": "github",
  "name": "GitHub",
  "description": "描述",
  "integration": { "type": "remote-mcp", "file": "mcp.json" },
  "icon": "icon.png"
}
```

## 专家

完整 Agent 角色包。对象存储前缀 `agent-templates/`。

**必填：** `version`、`package_type`（`agent_template`）、`name`（等于 `plugin.yaml.name`）、`description`

**可选：** `persona`、`skills[]`、`tools[]`、`rails[]`、`memories[]`、`rubrics[]`、`model`、`subagents[]`、`mcps[]`

`persona.dir`、`model.file`、`subagents[].dir` 只检查路径安全。Hub 不要求目录里有 `.md`，也不读 JSON。市场图标优先 `manifest.avatar`，否则外层 `icon.png`。

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

## 专家团

含成员的 AgentGroup 包。对象存储前缀 `agent-groups/`。外层 `runtime.type` 为 `agent-group`。缺少 `agents/` 目录或 `leader` 不拒发。

**必填：** `version`、`package_type`（`agent_group`）、`name`（等于 `plugin.yaml.name`）、`description`

**可选：** `agents[]`、`skills[]`、`instruction`、`avatar`

`agents[]` 每项是不含路径分隔符的名字。不要求包含 `leader`，也不检查 `agents/<name>/` 是否存在。`skills[]` 可以是技能名，或带 `dir` 的对象。

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

## 常见校验问题

| 现象 | 处理 |
|------|------|
| 缺少 `SKILL.md` 或 frontmatter | Skill / SwarmSkill 补上，且 `name` 与目录名一致 |
| 发布根下有多个 `SKILL.md` | 只保留一个技能目录 |
| SwarmSkill 角色不合法 | `kind: swarm-skill`，至少两个不重复的 `roles[].id` |
| 插件、连接器、专家、专家团的裸包无法识别 | 补 `manifest.json`，且全包只有一个外层 `plugin.yaml` |
| 版本不一致 | 表单版本与 `manifest.version` 相同 |
| 连接器缺少 manifest | 不要只传 `mcp.json` |
| 插件写了 persona / model | 这些字段属于专家，插件根上禁止 |

## 常见错误码

| error | 含义 |
|-------|------|
| `invalid_agent_mcp` | 连接器 manifest 或关联文件不合法 |
| `invalid_agent_plugin_manifest` / `invalid_agent_plugin_capability` | 插件 manifest 或能力引用不合法 |
| `invalid_manifest_json` / `missing_persona` | 专家 manifest 或 persona 不合法 |
| `invalid_plugin_structure` | 裸包无法识别，或有多个外层 `plugin.yaml` |
| `invalid_version` | 请求版本与包装包内版本不一致 |
| `dangerous_content` | 包内脚本或 MCP 配置含危险命令 |

## 相关文档

- 发布步骤：[Agent资产发布](../4.%20用户指南/Agent资产发布.md)
- [Agent 资产审核机制](./Agent资产审核机制.md)
- [接口参考](../7.%20API参考/openJiuwen-Agentic-Hub-接口参考.md)
