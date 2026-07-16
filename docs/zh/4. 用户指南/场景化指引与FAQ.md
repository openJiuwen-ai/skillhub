# 场景化指引与 FAQ

本文提供 **实操教程**、**集成说明** 与 **常见问题**，帮助创作者与集成方降低排查成本。

---

## 场景一：从零发布第一个 Skill

### 目标

将本地 Skill 目录发布到 Hub，通过审核后在市场可见。

### 前置条件

- 已登录 SkillHub（GitCode 或 GitHub）
- 本地 Skill 目录结构正确

### 推荐目录结构

```
my-demo-skill/
├── SKILL.md          # 必需，位于根目录
├── icon.png          # 可选，发布时可单独上传
├── scripts/
├── references/
└── assets/
```

`SKILL.md` 头部示例：

```yaml
---
name: my-demo-skill
description: 一句话描述该 Skill 的用途
version: 1.0.0
tags:
  - demo
---
# 正文说明（Markdown）
```

SwarmSkill 另须在 `SKILL.md` frontmatter 中设置 `kind: swarm-skill` 及 `roles` 列表（至少 2 个角色），详见发布表单校验提示。

### 操作步骤

1. 点击 **「+ 发布」** → 选择 **新 Skill**
2. **下载模板**（若可用）并对照修改
3. 填写技能名（与目录名规则一致）、显示名、版本 `1.0.0`
4. 选择 Skill **文件夹**（非 zip；系统自动打包）
5. 上传 PNG 图标（可选但推荐）
6. 提交 → 进入 **个人中心 → 我的 Skills** 查看状态
7. 若启用系统审查，先查看 **系统审查详情**；通过后等待人工审核
8. 审核通过后，在首页搜索并验证 Skill 可见

### 验收标准

- 个人中心显示「发布成功」或「人工审核中」→「审核通过」
- 首页可搜索到该 Skill，版本号与发布一致
- 详情页可正常下载 zip

---

## 场景二：发布新版本并维护 changelog

### 何时发新版本

- 修复 Skill 逻辑或文档
- 人工审核被驳回后须 **发布新版本** 重新提交（不可在驳回态上直接覆盖）
- 功能迭代（版本号递增，建议 semver）

### 操作步骤

1. 本地修改 Skill 内容，更新 `SKILL.md` 中 `version`（或与表单版本号一致）
2. **「+ 发布」** → 关联 Skill 选择 **已有项**
3. 填写 **新版本号**（如 `1.1.0`）与 **版本说明**
4. 提交审核

### 市场行为

- 审核完成前，访客仍看到 **旧已通过版本**
- 审核通过后，市场 `latest_version` 切换为新版本
- 历史版本可在 **个人中心 → Skill 详情** 查看或单独删除

### 强制覆盖同版本

若须覆盖已存在的相同版本号（如测试环境），勾选 **强制覆盖**。生产环境慎用。

---

## 场景三：Git 仓库批量接入

### 适用情况

多个 Skill 存放在同一公开 Git 仓库的 monorepo 结构中。

### 操作步骤

1. 个人中心 → **Git 源**
2. 填写 `https://` 克隆地址、分支/tag、`skills_subpath`（如 `skills/`）
3. 创建并等待同步完成
4. 同步成功的 Skill 出现在「我的 Skills」；**仍需按实例审核策略完成人工审核**（系统 Token 导入除外）

### 常见问题

| 现象 | 处理 |
|------|------|
| 同步一直「同步中」 | 等待最多约 30 分钟；仍无结果联系管理员查服务端 git 与日志 |
| 部分失败 | 查看失败 Skill 名称与日志；修正仓库后 **再次同步** |
| 重复注册 | 同一 URL+分支+子路径已被他人注册，需换路径或联系管理员 |

---

## 场景四：CLI 检索与安装

```bash
# 安装 CLI（示例）
pip install jiuwen-teamskills

# 搜索
jiuwen-teamskills search pdf

# 安装指定 Skill
jiuwen-teamskills install my-demo-skill

# 指定市场地址（自建实例）
export TEAMSKILLS_HUB_URL=https://your-hub.example.com
```

详细命令与 ClawHub 兼容说明见 [cli/README.md](../../../cli/README.md) 与 [ClawHub 兼容层 API](../7.%20API参考/ClawHub兼容层.md)。

---

## 场景五：审核员日常处理待审 Skill

1. 登录审核账号（须在 `MARKET_REVIEW_ADMIN_USERNAMES` 中）
2. 个人中心 → **待审核**
3. 打开 Skill，阅读描述与包内文件
4. 若启用系统审查，可先查看 **系统审查详情**
5. **通过** 或 **驳回**（驳回必填原因）
6. 在 **审核历史** 追溯操作记录

---

## 常见问题（FAQ）

### 登录与账号

**Q：点击登录无反应或回调失败？**
A：确认访问的前端地址与管理员配置的 `MARKET_OAUTH_FRONTEND_ORIGIN` 一致；OAuth 回调 URL 须在 GitCode/GitHub 应用设置中 **完全一致** 注册。

**Q：我应该是审核员但看不到「待审核」菜单？**
A：确认 OAuth 登录名与 `MARKET_REVIEW_ADMIN_USERNAMES` **精确匹配**（区分大小写以配置为准）；重新登录或联系管理员。

### 发布与打包

**Q：提示「SKILL.md 必须位于根目录」？**
A：选择文件夹时应选中 **Skill 根目录**（内含 SKILL.md），不要选上级目录或仅选子文件夹。推荐使用 Chrome / Edge。

**Q：版本号格式错误？**
A：须为三段数字，如 `1.0.0`（不含 `v` 前缀）。

**Q：获取模板下载链接失败？**
A：管理员未配置 `MARKET_SKILL_TEMPLATE_OBJECT_KEY` 或对象存储中无对应对象；可手动按 [场景一](#场景一从零发布第一个-skill) 结构准备。

**Q：发布成功但首页搜不到？**
A：Skill 可能仍在 **系统审查** 或 **人工审核**；或从未有过 `APPROVED` 版本。在个人中心确认状态。

**Q：新版本发布后市场仍显示旧版本？**
A：新版本待审期间对外 **故意** 保持旧版本；审核通过后会切换。

### 审核

**Q：人工审核按钮灰色不可用？**
A：Skill 可能仍在 **系统审查中**；或当前版本状态不允许该操作（如已通过不可驳回）。

**Q：驳回后如何重新上架？**
A：修改 Skill 内容后 **发布新版本**；若审核员认为驳回有误，也可在详情页对该版本执行 **审核通过**。

### 下载与 CLI

**Q：下载链接过期？**
A：预签名 URL 默认约 30 分钟有效（`MARKET_S3_PRESIGNED_EXPIRES`）；请重新点击下载。

**Q：CLI 连不上市场？**
A：检查 `TEAMSKILLS_HUB_URL` / CLI 配置文件中的 base URL；自建实例需 HTTPS 或网络可达。

### 互动

**Q：无法点赞自己的 Skill？**
A：产品设计如此，点赞/收藏仅用于他人作品。

---

## 错误码速查（API）

Web 发布失败时，响应体含 `detail.error_code`。常见码：

| error_code | 含义 | 建议 |
|------------|------|------|
| `SKILLHUB_IMPORT_CHECKSUM_MISMATCH` | 校验和不匹配 | 重新选择文件/目录让浏览器重算 SHA-256 |
| `SKILLHUB_IMPORT_VALIDATION_*` | 包结构或字段校验失败 | 对照表单错误与 `SKILL.md` frontmatter 规范（旧版插件包可能含 `plugin.yaml`） |
| `permission_denied` | 无权限 | 确认登录账号或审核员身份 |
| `http_403` | 鉴权失败 | Token 过期，重新登录 |

完整错误模型见 [TeamSkillsHub API — 全局错误响应](../7.%20API参考/TeamSkillsHub.md#全局错误响应)。

---

## 最佳实践

1. **版本号语义化**：修复用 patch（1.0.1），兼容功能用 minor（1.1.0）
2. **changelog 写清楚**：便于用户与审核员理解变更
3. **先本地验证 SKILL.md**：YAML 头缩进错误是常见驳回原因
4. **图标统一 PNG**：避免发布阶段图标校验失败
5. **Git 同步后抽查**：批量导入后逐个确认审核状态与市场展示
6. **生产禁用客户端 System Token**：仅服务端持有 `X-System-Token`

---

## 相关文档

- [快速开始](../2.%20快速开始.md)
- [角色与权限](./角色与权限.md)
- [安装指导](../3.%20安装指导/本地安装/SkillHub安装指导.md)
