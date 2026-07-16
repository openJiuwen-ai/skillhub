# Skill 与 SwarmSkill 规范

SkillHub 当前重点支持 Skill 和 SwarmSkill 两类 skill-like 资产。两者都以 `SKILL.md` 为核心，SwarmSkill 在此基础上增加多角色协作定义。

SwarmSkill 将多智能体协作中的角色分工、任务流程和协作规则沉淀为可复用的团队级技能。概念背景参见[百度百科：SwarmSkills](https://baike.baidu.com/item/SwarmSkills/67869397)。

## 类型选择

| 类型 | 适用场景 | 类型识别 |
|---|---|---|
| Skill | 单 Agent 的任务指令、脚本和参考资源 | `SKILL.md` 未声明 Swarm 类型 |
| SwarmSkill | 多角色分工、任务拆解与协作执行 | `kind: swarm-skill` |

## 工作区结构

最简单的工作区：

```text
example-skill/
├── SKILL.md
├── scripts/            # 可选
├── references/         # 可选
└── assets/             # 可选
```

也支持外层发布目录只包含一个 Skill 子目录：

```text
publish-root/
└── example-skill/
    ├── SKILL.md
    ├── scripts/
    ├── references/
    └── assets/
```

外层目录下只能有一个包含 `SKILL.md` 的非隐藏子目录。目录名符合 Skill slug 规则时，必须与 frontmatter 的 `name` 一致。

## `SKILL.md` 公共字段

```markdown
---
name: example-skill
description: Describe when and how an Agent should use this Skill.
---

# Example Skill

Instructions for the Agent.
```

公共约束：

- 文件必须包含 YAML frontmatter。
- `name` 必填，最长 64 个字符，只允许小写字母、数字和单个连字符分隔的片段。
- `name` 不得以连字符开头或结尾，也不得包含连续连字符。
- `description` 必填且非空，当前 CLI 上限为 4096 个字符。
- `display_name`、`author` 和 `tags` 可选；CLI 在发布时会将其转换为市场元数据。

## SwarmSkill 多角色字段

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

SwarmSkill 约束：

- `kind` 使用 `swarm-skill`。
- `roles` 必须是非空数组。
- 每个角色必须是对象，并包含非空字符串 `id`。
- 至少需要两个有效角色。
- 角色 `id` 不得重复。

## 创建与校验

```bash
jiuwen-teamskills init example-skill --type skill
jiuwen-teamskills init example-swarm --type swarmskill
jiuwen-teamskills validate example-skill
jiuwen-teamskills validate example-swarm
```

也可以使用 `openjiuwen-plugin init --type skill|swarmskill`，两条 CLI 共享相同的核心校验与打包逻辑。

## 打包与发布

```bash
jiuwen-teamskills pack example-skill --output out
jiuwen-teamskills publish example-skill \
  --version 0.0.1 \
  --market-url http://localhost:8100 \
  --token <token>
```

Skill-like 工作区可以不包含 `plugin.yaml`。发布时 CLI 会根据 `SKILL.md` 构造市场元数据：

- `name` 来自 frontmatter。
- `version` 来自发布命令的 `--version`。
- `display_name` 缺省时使用 `name`。
- `description` 来自 frontmatter。
- `author` 缺省时使用 `unknown`。
- `tags` 缺省时使用推导出的类型。

服务端接收的规范化发布包会包含生成的 `plugin.yaml`，其中 `runtime.type` 统一使用 `skill`；Skill 与 SwarmSkill 的区别仍由 `SKILL.md` 的 `kind` 和角色信息决定。

## 发布 ZIP

CLI `pack` 生成的本地 ZIP 以 Skill 内容为主：

```text
example-skill/
└── example-skill/
    ├── SKILL.md
    ├── scripts/
    ├── references/
    └── assets/
```

如果工作区根目录存在 `README.md`，CLI 会一并打包。不要手工依赖外层目录名称；发布时 CLI 会再次规范化包结构。

## 版本与审核

- 常规发布使用 `x.y.z` 三段式版本，如 `1.0.0`。
- 同一资产下版本持续演进，发布新版本时需要提供已有资产 ID。
- Skill 和 SwarmSkill 均需通过实例配置的审核链路后才能公开展示或在线体验。
- 待审核版本不会覆盖已公开的稳定版本。

## 常见校验问题

- 缺少 `SKILL.md` 或 YAML frontmatter。
- `name` 与 Skill 目录名不一致。
- 发布根目录下存在多个包含 `SKILL.md` 的子目录。
- SwarmSkill 缺少 `kind` 或有效的 `roles`。
- 角色少于两个或 `id` 重复。
- 发布不含 `plugin.yaml` 的 Skill-like 工作区时未提供版本号。

## 相关文档

- [插件包格式](./插件包格式.md)
- [发布 Skill](../4.%20用户指南/发布Skill.md)
- [发布 SwarmSkill](../4.%20用户指南/发布SwarmSkill.md)
- [Skill 审核机制](./Skill审核机制.md)
