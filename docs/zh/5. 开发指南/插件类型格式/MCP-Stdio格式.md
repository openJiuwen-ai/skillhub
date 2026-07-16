# MCP Stdio 插件格式

MCP Stdio 插件用于分发可由宿主进程通过标准输入输出启动的 MCP Server，对应的 `runtime.type` 为 `mcp-stdio`。

## 创建脚手架

```bash
openjiuwen-plugin init example-mcp --type mcp-stdio
```

## 开发目录

```text
example-mcp/
├── plugin.yaml
├── README.md
├── icon.png                  # 可选
├── pyproject.toml
├── schemas/
│   └── tools.json
└── src/
    └── example_mcp/
        ├── __init__.py
        └── mcp_server.py
```

当前脚手架会生成 `schemas/tools.json`；MCP Stdio 校验的核心是 `plugin.yaml`、`README.md`、`src/` 和 MCP 启动配置。

## `plugin.yaml`

```yaml
name: example-mcp
version: 0.0.1
display_name: Example MCP
description: Example MCP stdio server.
runtime:
  type: mcp-stdio
metadata:
  author: your-name
  tags: [mcp]
compatibility:
  python: ">=3.11, <3.14"
mcp:
  transport: stdio
  command:
    - python
    - -m
    - example_mcp.mcp_server
```

约束：

- `mcp.transport` 必须是 `stdio`。
- `mcp.command` 必须是非空字符串数组。
- Python 包名通常将插件名中的连字符替换为下划线。

## MCP 入口

```python
from fastmcp import FastMCP

mcp = FastMCP("example-mcp")

@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
```

## 发布 ZIP

```bash
openjiuwen-plugin validate example-mcp
openjiuwen-plugin pack example-mcp --output out
```

MCP Stdio 使用整目录打包，并过滤虚拟环境、缓存和构建输出：

```text
example-mcp-0.0.1/
├── plugin.yaml
├── README.md
├── pyproject.toml
├── schemas/
│   └── tools.json
└── src/
    └── example_mcp/
        └── mcp_server.py
```

## 常见校验问题

- 缺少 `src/` 或 `README.md`。
- `mcp.transport` 不是 `stdio`。
- `mcp.command` 为空、包含非字符串值或指向错误模块。
- `compatibility.python` 不是有效的 PEP 440 版本范围。

## 相关文档

- [插件包格式](../插件包格式.md)
- [CLI 说明](../../../../cli/README.md)
