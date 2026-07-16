# 发布 SwarmSkill

SwarmSkill 是多角色协同类 Skill，适合需要角色分工、任务拆解和协作执行的场景。

## 与普通 Skill 的区别

| 项目 | Skill | SwarmSkill |
|---|---|---|
| 执行形态 | 单 Agent 技能 | 多角色协同技能 |
| 关键配置 | `SKILL.md` | `SKILL.md` 中声明 `kind` 和 `roles` |
| 典型场景 | 单一任务工具化 | 多步骤、多角色协作任务 |

## 目录结构

```text
my-swarm-skill/
├── SKILL.md          # 必需，包含 frontmatter 声明
├── roles/            # 可选，角色详细定义
│   ├── researcher.md
│   └── reviewer.md
├── workflow.md       # 可选，工作流详细说明
├── scripts/          # 可选
└── assets/           # 可选
```

## SKILL.md 完整示例

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

# My Swarm Skill

## 概述
这是一个两角色协作 Skill，researcher 负责调研，reviewer 负责审查。

## 工作流
1. researcher 收集信息并初步整理
2. reviewer 审查调研结果，标注问题
3. researcher 根据反馈补充修正
4. 输出最终报告
```

### frontmatter 字段说明

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `name` | 是 | 小写字母、数字、连字符，最长 64 字符 |
| `description` | 是 | 一句话描述 Skill 用途 |
| `kind` | 是 | 必须为 `swarm-skill` |
| `roles` | 是 | 非空数组，至少 2 个角色 |
| `roles[].id` | 是 | 角色唯一标识，不可重复 |
| `roles[].description` | 是 | 角色职责描述 |

## 发布步骤

1. 准备 SwarmSkill 目录和 `SKILL.md` 角色声明
2. 登录 SkillHub 并点击「+ 发布」
3. 选择 Skill 类型，填写名称、显示名、版本
4. 上传包含 `SKILL.md` 的目录
5. 提交后在个人中心查看发布结果和审核状态

## 也可以用 CLI 发布

```bash
# 初始化
jiuwen-teamskills init my-swarm-skill --type swarmskill

# 校验
jiuwen-teamskills validate my-swarm-skill

# 打包
jiuwen-teamskills pack my-swarm-skill --output out

# 发布
jiuwen-teamskills publish my-swarm-skill \
  --version 1.0.0 \
  --market-url http://localhost:8100 \
  --token <token>
```

## 常见校验问题

- 缺少 `kind` 字段或值不是 `swarm-skill`
- `roles` 不是数组或为空
- 角色少于 2 个
- 角色 `id` 重复
- `SKILL.md` YAML 缩进错误
- `name` 与目录名不一致

## 相关文档

- [Skill 与 SwarmSkill 规范](../5.%20开发指南/Skill与SwarmSkill规范.md)
- [发布 Skill](./发布Skill.md)
- [场景化指引与 FAQ](./场景化指引与FAQ.md)
