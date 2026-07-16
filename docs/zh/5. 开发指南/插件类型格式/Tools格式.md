# Tools 插件格式

Tools 插件用于分发 Python 工具实现。开发目录保留源码，发布时由 CLI 构建 wheel，并将 wheel 与工具定义打入 ZIP。

## 适用场景

- 工具逻辑在本地 Python 环境中运行。
- 需要将实现安装为 wheel。
- 工具名称、输入和输出通过 `schemas/tools.json` 声明。

对应的 `runtime.type` 为 `tools`。

## 创建脚手架

```bash
openjiuwen-plugin init example-tools --type tools
```

## 开发目录

```text
example-tools/
├── plugin.yaml
├── README.md
├── icon.png                  # 可选
├── pyproject.toml
├── schemas/
│   └── tools.json
└── src/
    └── example_tools/
        ├── __init__.py
        └── plugin.py
```

`plugin.py` 中 `@tool(name="...")` 的名称应与 `schemas/tools.json` 一致。

## `plugin.yaml`

```yaml
name: example-tools
version: 0.0.1
display_name: Example Tools
description: Example Python tools.
runtime:
  type: tools
metadata:
  author: your-name
  tags: [tools]
compatibility:
  python: ">=3.11, <3.14"
tools_schema: schemas/tools.json
```

`tools_schema` 省略时 CLI 默认使用 `schemas/tools.json`，建议显式填写；其他路径会校验失败。

## 工具 Schema

```json
{
  "tools": [
    {
      "name": "example",
      "description": "Describe the tool.",
      "input_schema": {"type": "object", "properties": {}, "required": []},
      "output_schema": {"type": "object", "properties": {}, "required": []}
    }
  ]
}
```

`tools` 必须是非空数组；名称不可重复；每项必须有名称、描述以及类型为 `object` 的输入和输出 Schema。

## 发布 ZIP

```bash
openjiuwen-plugin validate example-tools
openjiuwen-plugin pack example-tools --output out
```

CLI 会先运行 `pip wheel . --no-deps`。ZIP 的关键结构为：

```text
example-tools-0.0.1/
├── plugin.yaml
├── README.md
├── icon.png                  # 存在时打包
├── schemas/
│   └── tools.json
└── dist/
    └── example_tools-0.0.1-py3-none-any.whl
```

发布 ZIP 不包含开发阶段的 `src/` 和 `pyproject.toml`。

## 常见校验问题

- 缺少 `pyproject.toml`、`src/` 或 `schemas/tools.json`。
- Schema 中工具名称重复或不符合命名规则。
- Schema 工具名称与 `plugin.py` 中的 `@tool` 不一致。
- wheel 构建失败或 `dist/` 中没有 `.whl`。

## 相关文档

- [插件包格式](../插件包格式.md)
- [CLI 说明](../../../../cli/README.md)
