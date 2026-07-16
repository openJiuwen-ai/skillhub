# RESTful API 插件格式

RESTful API 插件把远程 HTTP API 描述为可发现和可调用的工具，对应的 `runtime.type` 为 `restful-api`。

## 创建脚手架

```bash
openjiuwen-plugin init example-api --type restful-api
```

## 开发目录

```text
example-api/
├── plugin.yaml
├── README.md
├── icon.png                  # 可选
├── pyproject.toml
├── schemas/
│   └── tools.json
└── src/
    └── example_api/
        ├── __init__.py
        └── rest_api.py
```

`src/` 是当前 CLI 校验要求；远程调用契约主要由 `plugin.yaml` 和 `schemas/tools.json` 描述。

## `plugin.yaml`

```yaml
name: example-api
version: 0.0.1
display_name: Example API
description: Example remote API tools.
runtime:
  type: restful-api
metadata:
  author: your-name
  tags: [api]
compatibility:
  python: ">=3.11, <3.14"
api:
  base_url: https://api.example.com
  tools_schema: schemas/tools.json
  default_headers:
    Authorization:
      type: string
      send_method: Header
      value: Bearer YOUR_API_KEY
      description: API bearer token.
```

`api.base_url` 必须是非空字符串。密钥只能使用占位符或在运行环境中注入，不要提交真实凭据。

## REST 工具契约

```json
{
  "tools": [
    {
      "name": "get-item",
      "description": "Get an item by id.",
      "path": "/items/{item_id}",
      "method": "GET",
      "input_schema": {
        "type": "object",
        "properties": {
          "item_id": {"type": "string", "send_method": "Path"}
        },
        "required": ["item_id"]
      },
      "output_schema": {"type": "object", "properties": {}, "required": []}
    }
  ]
}
```

约束：

- `method` 支持 `GET`、`POST`、`PUT`、`DELETE` 和 `PATCH`。
- `send_method` 支持 `None`、`Header`、`Query`、`Body` 和 `Path`。
- 路径占位符必须存在同名输入属性，且其 `send_method` 为 `Path`。
- `required` 中的名称必须在 `properties` 中定义。

## 发布 ZIP

```bash
openjiuwen-plugin validate example-api
openjiuwen-plugin pack example-api --output out
```

RESTful API 使用整目录打包，保留 `plugin.yaml`、`schemas/tools.json`、`src/` 和说明文件。

## 常见校验问题

- 缺少 `src/`、`README.md` 或 `schemas/tools.json`。
- `api.base_url` 为空。
- HTTP 方法或 `send_method` 不在支持范围内。
- 路径参数与输入 Schema 不匹配。
- 在元数据或 Header 示例中提交真实密钥。

## 相关文档

- [插件包格式](../插件包格式.md)
- [CLI 说明](../../../../cli/README.md)
