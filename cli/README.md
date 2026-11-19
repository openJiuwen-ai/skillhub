# skillhub/cli

本目录是 openJiuwen Agentic Hub 的命令行工具：发行包 `openjiuwen-agentichub`，安装后的命令为 `agentichub`。命令说明见 [`openjiuwen_agentichub/README.md`](openjiuwen_agentichub/README.md)。共享实现在 `cli_core/`（随 wheel 打包，不单独发布）。

| 目录 | 发行名 / 命令 | 说明 |
|------|---|---|
| `openjiuwen_agentichub/` | `openjiuwen-agentichub` / `agentichub` | 七类 Agent 资产的脚手架、校验、打包与市场操作 |
| `cli_core/` | - | 参数解析、校验、打包、市场请求 |

## 开发安装

在仓库根目录执行：

```bash
python -m pip install -e ./cli/openjiuwen_agentichub
```

安装后执行：

- `agentichub -h`

## 测试

从仓库根目录进入 `cli/openjiuwen_agentichub` 后执行：

```bash
pytest
```

## 构建 wheel

在仓库根目录执行：

```bash
python -m pip wheel ./cli/openjiuwen_agentichub
```

可按需追加 `--no-deps`、`-w dist` 等参数。
