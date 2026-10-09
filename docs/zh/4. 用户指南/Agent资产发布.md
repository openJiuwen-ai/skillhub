# Agent资产发布

市场可以发布六类 Agent 资产：Skill、SwarmSkill、插件、连接器、专家、专家团。包格式见 [Agent资产](../5.%20开发指南/Agent资产.md)。

## 共同步骤

1. 登录后点击右上角「+ 发布」。
2. 选择发布类型。Skill / SwarmSkill 选目录；插件、连接器、专家、专家团上传 zip。所选类型必须和包内识别结果一致。
3. 首次发布选「新 xxx」。给已有资产发新版本时，在关联下拉里选该项，包名和显示名锁定，不可改。
4. 填写版本号，三段式，如 `1.0.0`，不含 `v`。新版本须高于已有版本，或勾选强制覆盖同版本。
5. 提交后到个人中心查看状态。审核通过前，市场仍展示上一版已通过的内容。

| 状态 | 含义 |
|------|------|
| 审查中 | 已启用审查，规则或 AI 检查进行中 |
| 审核中 | 等待审核管理员处理 |
| 发布成功 | 已通过，对外可见 |
| 发布失败 | 看审查详情或驳回原因，改完后发新版本 |

插件、连接器、专家、专家团的包装包版本必须与 `manifest.json` 的 `version` 一致。zip 须是完整压缩包，不要把文件夹改名为 `.zip`。

## Skill

准备一个目录，根上有 `SKILL.md`，目录名与 frontmatter 的 `name` 一致。

```text
skill-name/
└── SKILL.md
```

| 字段 | 要求 |
|------|------|
| 技能名 | 小写字母开头，仅含小写字母、数字、连字符，最长 64 字符 |
| 显示名 | 1～128 字符 |
| 描述 | 可空；留空则用 `SKILL.md` 里的 `description` |
| 标签 | 中文或英文逗号分隔 |
| 图标 | PNG，不超过 5MB |
| 目录 | 须含根级 `SKILL.md` |

发布抽屉里的「下载 Skill 模板」依赖 `MARKET_SKILL_TEMPLATE_OBJECT_KEY`。没配时联系部署管理员。

## SwarmSkill

仍选 Skill 类型，上传带 `SKILL.md` 的目录。frontmatter 必须声明 `kind: swarm-skill`，以及至少两个角色。

```markdown
---
name: my-swarm-skill
description: 多角色协作完成研究和审查任务
kind: swarm-skill
roles:
  - id: researcher
    description: 负责收集和分析信息
  - id: reviewer
    description: 负责审查和验证结果
---
```

`roles[].id` 不能重复。也可以用 CLI：

```bash
jiuwen-teamskills init my-swarm-skill --type swarmskill
jiuwen-teamskills validate my-swarm-skill
jiuwen-teamskills pack my-swarm-skill --output out
jiuwen-teamskills publish my-swarm-skill \
  --version 1.0.0 \
  --market-url http://localhost:8100 \
  --token <token>
```

常见拒发：缺少 `kind`、角色少于 2 个、`id` 重复、`name` 与目录名不一致、YAML 缩进错误。

## 插件

发布类型选「插件」。裸包至少是一个含 `manifest.json` 的目录，`package_type` 为 `plugin`。也可以上传已经包了 `plugin.yaml` 的 zip。

`skills`、`tools`、`mcps` 可以声明，声明的文件暂时缺失也不会拒发。根上不要写 `persona`、`agent_card`、`model`、`subagents`，那些属于专家包。字段见 [插件](../5.%20开发指南/Agent资产.md#插件)。通过后出现在「插件」页签。

## 连接器

发布类型选「连接器」。裸包必须有 `manifest.json`（`package_type` 为 `mcp`），只有 `mcp.json` 的旧包不能发。裸包要在表单里填版本号。

| 接入方式 | 还需要 |
|----------|--------|
| 本地 MCP（`stdio-mcp`） | `mcp.json`，含 `command` |
| 远程 MCP（`remote-mcp`） | `mcp.json`，含 `url` |
| 命令行（`cli`） | `cli.json` |
| 仅 Skill（`skill-only`） | 不需要上述文件 |

图标用 PNG，写在 `manifest.icon`，不要用 `icon.svg`。`mcp.json` / `cli.json` 里的 `${VAR}` 要在 `token-schema.json` 有对应项。字段见 [连接器](../5.%20开发指南/Agent资产.md#连接器)。通过后出现在「连接器」页签。

## 专家

发布类型选「专家」。`package_type` 为 `agent_template`，并填写 `description`。`persona`、`skills`、`model` 可以声明，文件暂时缺失不拒发。路径不要用 `..` 或绝对路径。字段见 [专家](../5.%20开发指南/Agent资产.md#专家)。通过后出现在「专家」页签。

若被识别成插件，检查 `package_type` 是不是 `agent_template`。

## 专家团

发布类型选「专家团」。`package_type` 为 `agent_group`，并填写 `description`。`agents` 里写成员名即可，缺少 `agents/` 目录或 `leader` 不拒发。名字不要写成路径。字段见 [专家团](../5.%20开发指南/Agent资产.md#专家团)。通过后出现在「专家团」页签。

若被识别成专家，检查 `package_type` 是不是 `agent_group`。

## 相关文档

- [场景化指引与 FAQ](./场景化指引与FAQ.md)
- [角色与权限](./角色与权限.md)
