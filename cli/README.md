# skillhub/cli

本目录是 openJiuwen Agentic Hub 的命令行工具：发行包 `openjiuwen-agentichub`，安装后的命令为 `agentichub`。命令说明见 [`openjiuwen_agentichub/README.md`](openjiuwen_agentichub/README.md)。共享实现在 `cli_core/`（随 wheel 打包，不单独发布）。

| 目录 | 发行名 / 命令 | 说明 |
|------|---|---|
| `openjiuwen_agentichub/` | `openjiuwen-agentichub` / `agentichub` | 六类 Agent 资产的脚手架、校验、打包与市场操作 |
| `cli_core/` | - | 参数解析、校验、打包、市场请求 |

## 开发安装

```bash
cd skillhub/cli/openjiuwen_agentichub
pip install -e .
```

安装后执行：

- `agentichub -h`

## 测试

在 `skillhub/cli/openjiuwen_agentichub` 下执行：

```bash
pytest
```

## 构建 wheel

在 `skillhub/cli/` 下执行：

```bash
pip wheel ./openjiuwen_agentichub
```

可按需追加 `--no-deps`、`-w dist` 等参数。
