# 贡献指南

感谢你对 openJiuwen Agentic Hub 的关注。欢迎通过 **Issue** 与 **Pull Request** 参与。

## 参与方式

- **Bug / 需求**：尽量说明环境（OS、Python/Node 版本）、复现方式、期望行为与实际行为。
- **文档**：修正笔误、补充部署步骤、改进交叉链接都很欢迎。
- **代码**：请先检索是否已有相关 Issue/PR，避免重复劳动。

## 开发约定

- **范围**：单次 PR 聚焦一类变更，便于评审与回滚。
- **风格**：与周边现有代码保持一致（命名、类型、注释密度）。
- **密钥**：不要提交 `.env`、访问密钥、个人令牌；以 [`.env.example`](.env.example) 为唯一样板补充说明。

## 本地校验（按需）

- **CLI**：在 `cli/openjiuwen_agentichub` 下可运行 `pytest`（详见 [`cli/README.md`](cli/README.md)）。
- **marketplace / frontend**：以各目录现有脚本与团队 CI 为准；提交前请至少保证改动路径可启动或自测通过。

## Pull Request

- **描述**：说明动机、主要变更点；若涉及行为变更，请写清单或简要迁移说明。
- **关联**：可关闭相关 Issue（如 `Fixes #123`）。

维护者会尽快评审；若长时间无回复，可在 Issue 中温和提醒。
