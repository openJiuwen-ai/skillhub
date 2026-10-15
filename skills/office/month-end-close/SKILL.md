---
name: month-end-close
description: 月末关账勾稽检查：读取试算平衡表 xlsx，复算期初/本期/期末借贷平衡与资产=负债+权益恒等式，核对合计行防重复计入，检查缺列/不可解析金额/公式无缓存/科目重复或父子并存，产出关账检查表与未决项清单。当用户给出期末试算表（科目/借贷方余额）要求"关账检查/试算平衡核对/月末结账检查"时使用。只输出差异与建议，不执行过账或锁定账期。
---

# 月末关账交付（清单/勾稽/审查）

## 何时使用

- 期末试算平衡表（.xlsx）→ 关账检查表 + 勾稽结果 + 未决项
- 用户问"帮我核对这份试算表是否平/月末关账检查"

## 用法

```
python scripts/close_check.py --trial tb.xlsx --checklist checks.json --output close-check.xlsx --report close-report.json
```

试算表列名自动识别（包含关键词即匹配）：
- 科目编码/科目名称
- 期初余额借/期初余额贷（或 期初借/期初贷）
- 本期发生额借/本期发生额贷（或 本期借/本期贷）
- 期末余额借/期末余额贷（或 期末借/期末贷）

checklist.json 勾稽规则（表达式仅支持 col()/sump() 与 + - * ==）：

```json
{"checks": [
  {"id": "TB-OPEN", "name": "期初借贷平衡", "expr": "col('期初余额借') == col('期初余额贷')"},
  {"id": "TB-PERIOD", "name": "本期发生额借贷平衡", "expr": "col('本期发生额借') == col('本期发生额贷')"},
  {"id": "TB-CLOSE", "name": "期末借贷平衡", "expr": "col('期末余额借') == col('期末余额贷')"},
  {"id": "EQ-1", "name": "资产=负债+权益(期末)", "expr": "sump('期末余额借','1') == sump('期末余额贷','2') + sump('期末余额贷','4')"}
]}
```

`col('X')` = 剔除合计行后 X 列合计；`sump('X','1')` = 科目编码以 1 开头的行 X 列合计（默认恒等式按中国科目编号：1 资产、2 负债、4 所有者权益，可用 --checklist 自定义前缀）。

## 输出

- `close-check.xlsx`：三个 sheet——勾稽检查（编号/名称/表达式/左值/右值/差异/结论/备注）、未决项（行号/原因/科目/列）、结构检查（科目重复/父子并存）
- `close-report.json`：
  - `checks`：每项 {id, name, left, right, difference, pass}；引用列缺失时为 {error: "缺少列…", pass: false}，引用列含不可解析/非有限/无缓存金额时附 `unreliable_columns` 且 pass=false
  - `total_row_check`：合计行与明细复算是否一致（逐行给出 stated/recomputed；不一致=可能重复计入或漏记）
  - `structure_review`：科目编码重复（duplicate_codes）与父子科目并存（parent_child，合并层级重复计入风险）
  - `pending_items`：金额无法解析/金额非有限数/公式无缓存值/缺少科目编码/合计行金额问题的行
  - `verdict`：`balanced` 或 `differences_found`，附 `verdict_reasons` 逐条说明
  - 附注：只输出建议与差异清单，不执行任何过账

## 规则（脚本保证）

- `balanced` 仅在同时满足时给出：全部勾稽通过（含无缺列/无不可靠列）、合计行与明细复算一致、无未决项、无科目编码重复、无父子科目并存；任一不满足即为 `differences_found` 并给出原因
- 勾稽表达式引用的金额列在试算表中不存在时，该检查以"缺少列"错误失败，绝不以 0==0 通过
- 金额无法解析（如 NOT_A_NUMBER）、非有限数（如 1e400）、公式无缓存值（data_only 读回空且原格是公式）的行进入未决项；其所在列令引用它的勾稽检查标记不可靠且不通过，不得静默按 0 参与平衡
- 合计/总计行不参与明细求和（防重复计入），但逐行与复算值比对（分位精度），不一致阻断 balanced
- 缺少科目编码的行计入 col() 合计但不参与 sump() 前缀分类，显式列为未决项；其非零金额令 sump 类勾稽标记不可靠
- 借贷平衡差值精确给出（左右各按分位取整后比较，避免 0.1+0.2 类浮点累加误差；≥0.01 的差异必报）
- 未决项显式列出而非静默通过

## 依赖

Python 3.9+，openpyxl（`pip install openpyxl`）。
