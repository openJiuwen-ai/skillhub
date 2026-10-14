---
name: sheet-consolidate-reconcile
description: 多源 Excel/CSV 表格合并、清洗与对账，产出标准化合并表与审计台账。当用户给出多份同名指标但来源/口径不同的 xlsx/csv（如各区域销售表、多系统导出），要求"合并这些表/对账/核对差异/多表汇总/找出不一致"时使用。自动剔除合计行避免重复计入、主键匹配与重复主键明细、同名不同口径列显式标注来源、匹配率可复算。单文件编辑用 xlsx，统计分析用 data-analysis。
---

# 多源表格合并、清洗与对账

## 何时使用

- 2 份以上 .xlsx/.csv，指标同名但来源/口径不同，需要合并+对账
- 用户问"这几张表哪些数据对不上/差异在哪"

## 用法

配置 config.json：

```json
{
  "primary_key": "产品编码",
  "metrics": ["销售额"],
  "sources": [
    {"file": "east.xlsx", "sheet": "Sheet1", "source_name": "华东区",
     "column_map": {"产品编码": "编码", "销售额": "销售额(万元)"}},
    {"file": "south.xlsx", "source_name": "华南区", "column_map": {}}
  ]
}
```

执行：

```
python scripts/consolidate.py --config config.json --output merged.xlsx --report reconcile-report.json
```

## 输出

- `merged.xlsx`
  - Sheet「合并汇总」：每个主键一行，指标列按来源命名（如 `销售额@华东区`），附 `数据一致` 标记与各源缺失标注
  - Sheet「源明细」：全部源行（来源/主键/指标值/行号），可回溯到源文件
- `reconcile-report.json`：
  - `match_stats`：唯一主键数、各源齐全数、单源数、匹配率（=齐全主键/唯一主键）
  - `mismatches`：主键+指标级差异（各源值并列）
  - `duplicates`：同源重复主键明细（不静默去重）
  - `source_missing`：各源缺失的主键清单
  - `skipped_total_rows`：被剔除的合计/小计/累计行（带行号）

## 清洗规则（脚本保证）

- 聚合前剔除主键列为 合计/总计/小计/累计 的行，并在报告中列明行号
- 同名不同口径列在合并表以 `指标@来源` 命名，显式标注口径来源
- 重复主键：保留全部值并列入 duplicates 明细；对账时逐值比较
- 数字统一按 float 解析；无法解析的值保留原样并列出，不猜不补 0

## 失败与降级

- 某一源文件损坏/缺列：该源记 `failed`，其余源继续；报告中说明
- 全部源失败：退出码 2
- 主键列缺失（映射后找不到）：该源 failed

## 依赖

Python 3.9+，openpyxl（`pip install openpyxl`）。
