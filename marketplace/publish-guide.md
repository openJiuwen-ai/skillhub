# 上传资产

openJiuwen Agentic Hub 可以发布六类资产：Skill、Swarm Skill、插件、连接器、专家、专家团。网页上的「+ 发布」和接口 `POST /api/v1/plugins` 走同一条审核。通过之前，市场仍显示该资产上一个已通过的版本。

## 在网页上发布

1. 登录后，点击右上角「+ 发布」。
2. 选择类型。Skill、Swarm Skill 选择本地文件夹；插件、连接器、专家、专家团选择 zip。页面会识别包内类型，与所选不一致时不能提交。
3. 第一次发布选「新 Skill」或对应的「新 xxx」。给已有资产发新版本时，在关联下拉里选中该项。包名和显示名锁定，不能改。
4. 填写版本号，三段数字，例如 `1.0.0`，不要加 `v`。同版本已存在时，勾选「强制覆盖」后再提交。
5. 选择可见性。公开：审核通过后出现在市场。私有：仅作者、管理员和被授权的组群可以访问。发新版本时沿用已有可见性，不能在这里改。
6. 点击「上传发布」。浏览器会打包并计算 SHA-256，不用手填校验和。
7. 打开个人中心查看状态。

| 状态 | 含义 |
|------|------|
| 审查中 | 规则或模型检查进行中 |
| 审核中 | 等待审核管理员处理 |
| 发布成功 | 已通过。公开资产出现在对应页签 |
| 发布失败 | 查看审查详情或驳回原因，改完后发新版本 |

Skill 目录根上要有 `SKILL.md`。图标只接受 PNG，不超过 5MB。发布抽屉里可以下载模板；站点没放模板时，按下一节自己准备目录。

## 用接口上传

网页提交的也是这个接口。自己调用时带登录颁发的 Bearer Token。

```http
POST /api/v1/plugins
Authorization: Bearer <token>
X-Checksum-SHA256: <zip 的 SHA-256，64 位小写十六进制>
Content-Type: multipart/form-data
```

| 字段 | 说明 |
|------|------|
| `file` | zip，必填。Skill / Swarm Skill 里要有 `SKILL.md`；其余四类可以是含 `manifest.json` 的裸包，或已经带 `plugin.yaml` 的包装包 |
| `plugin_version` | 如 `1.0.0`。不传则读包内版本 |
| `plugin_id` | 给已有资产发新版本时填 `asset_id`。第一次发布不传 |
| `asset_name` | 包名。改名须与包内目录一致 |
| `display_name` | 显示名。以表单为准 |
| `description` | 简介。Skill 留空时用 `SKILL.md` 里的 `description` |
| `tags` | 中文或英文逗号分隔 |
| `visibility` | `public`（默认）或 `private` |
| `force` | `true` 时覆盖同版本 |
| `version_desc` | 版本说明，可选 |

```bash
curl -X POST "https://agentichub.openjiuwen.com/api/v1/plugins" \
  -H "Authorization: Bearer <token>" \
  -H "X-Checksum-SHA256: $(sha256sum skill.zip | awk '{print $1}')" \
  -F "file=@skill.zip" \
  -F "plugin_version=1.0.0" \
  -F "display_name=演示 Skill"
```

Windows PowerShell 先算出校验和，再写入请求头：

```powershell
(Get-FileHash .\skill.zip -Algorithm SHA256).Hash.ToLower()
```

自建实例把主机名换成自己的市场地址。成功时响应里的 `publish_result` 为 `reviewing`（审查中）、`pending_moderation`（审核中），随后变为 `publish_success` 或 `publish_failed`。

| 失败情况 | 处理 |
|----------|------|
| 缺少校验和，或与文件不一致 | 重新计算 zip 的 SHA-256，放入 `X-Checksum-SHA256` |
| 版本格式不对 | 使用 `1.0.0` 这种三段数字，或 7 位小写 git commit，不要加 `v` |
| 同版本已存在 | 换更高版本，或传 `force=true` |
| 类型不一致 | 让发布类型和包内 `kind` / `package_type` 对齐 |
| zip 打不开 | 上传完整压缩包。空文件、损坏包，或把文件夹改名为 `.zip`，都不能解析 |
| 包装包版本不一致 | `plugin_version` 必须与 `manifest.json` 的 `version` 相同 |

## 六类包怎么准备

### Skill

网页上选择文件夹，不要选 zip。目录名与 `SKILL.md` 里的 `name` 一致。

```text
my-demo-skill/
└── SKILL.md
```

```markdown
---
name: my-demo-skill
description: 一句话说明用途
---
```

包名以小写字母开头，只含小写字母、数字、连字符，最长 64 字符。显示名 1～128 字符。

### Swarm Skill

发布类型选 Swarm Skill，仍然上传带 `SKILL.md` 的目录。frontmatter 必须有 `kind: swarm-skill`，以及至少两个角色，`id` 不能重复。

```markdown
---
name: my-swarm-skill
description: 多角色协作完成研究和审查
kind: swarm-skill
roles:
  - id: researcher
    description: 收集和分析信息
  - id: reviewer
    description: 审查和验证结果
---
```

缺少 `kind`、角色少于 2 个、`id` 重复、`name` 与目录名不一致、YAML 缩进错误，都会被拒绝。

### 插件、连接器、专家、专家团

上传 zip。`manifest.json` 的 `package_type` 必须与所选类型一致。

| 页面上的类型 | `package_type` | 通过后出现在 |
|--------------|----------------|--------------|
| 插件 | `plugin` | 插件 |
| 连接器 | `mcp` | 连接器 |
| 专家 | `agent_template` | 专家 |
| 专家团 | `agent_group` | 专家团 |

连接器按接入方式再带文件：本地 MCP 的 `mcp.json` 含 `command`；远程 MCP 的 `mcp.json` 含 `url`；命令行用 `cli.json`。只有 `mcp.json`、没有 `manifest.json` 的旧包不能发。图标用 PNG，写在 `manifest.icon`。

专家被识别成插件时，把 `package_type` 改成 `agent_template`。专家团被识别成专家时，改成 `agent_group`。

## 本地生成后再上传

本机已有 `agentichub` 命令时，可以先生成目录并检查，再交给网页或接口。命令行上传调用的仍是 `POST /api/v1/plugins`。

```bash
agentichub init my-demo-skill --type skill
agentichub validate my-demo-skill
agentichub pack my-demo-skill --output out
agentichub publish my-demo-skill \
  --plugin-version 1.0.0 \
  --market-url https://agentichub.openjiuwen.com \
  --token <token>
```

`--type` 还可以是 `swarmskill`、`agent-plugin`、`agent-mcp`、`agent-template`、`agent-group`。已经打好的 zip 用 `agentichub publish --file out/my-demo-skill.zip`。
