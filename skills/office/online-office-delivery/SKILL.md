---
name: online-office-delivery
description: 在线办公文档交付与回读：探测宿主环境可用的授权 CLI/API（lark-cli 等，只检查存在性不读取凭据值），有授权时执行创建/导入/定点修改并回读校验内容与权限，输出真实可访问链接与回读结果；无授权环境显式输出 blocked 及缺失项清单并以非零退出，绝不伪造链接或以本地文件/截图冒充在线交付。当用户要求"把这个表格/文档发布到线上/创建在线文档并修改"时使用。
---

# 在线办公文档交付与回读

## 何时使用

- 本地 DOCX/XLSX/PPTX → 在线文档/表格/幻灯片（真实可访问可编辑链接）
- 用户要求"发布到飞书/建在线表格给大家填/在线文档定点修改"

## 前提（诚实边界）

本技能**依赖宿主环境提供已授权的官方 CLI/API**（如 lark-cli、飞书开放 API）。技能本身不内置任何凭据，也不假设 Jiuwen 自带豆包专属工具（present_files / lark-cli）。探测不到授权工具时：
- 输出 `delivery-result.json`：`status: "blocked"` + 缺失项清单（哪些工具/环境变量未找到）
- 退出码 3，明确告知用户需要什么授权，**不伪造成功链接，不用本地文件或截图冒充在线交付**

## 用法

```
python scripts/online_delivery.py --plan plan.json --outdir out/
```

plan.json：

```json
{
  "target": {"type": "lark_sheet", "position": "共享目录/2026Q3"},
  "files": [{"local": "merged.xlsx", "name": "Q3对账表"}],
  "readback": true
}
```

## 流程（有授权环境时）

1. 探测 CLI（`lark-cli`、`lark` 等在 PATH 中）与授权环境变量（**只检查变量名存在，不读取值**）
2. 按目标类型执行创建/导入（命令模板见 `references/`）
3. 回读校验：拉取在线内容与本地文件比对（哈希或单元格抽样），核对权限（可编辑）
4. 输出 `delivery-result.json`：真实链接、回读一致性结果、权限核对结果

回读不一致或命令失败 → `status: failed` 并给出原因，不判成功。

## 依赖

Python 3.9+（标准库）。实际交付依赖宿主授权的 lark-cli 或官方 API；本环境未安装 → 在线用例记 `blocked`，不以 mock 冒充在线验收。
