# month-end-close（月末关账交付）

## 定位

期末试算平衡表（.xlsx）勾稽复算与关账审查：期初/本期/期末借贷平衡、资产=负债+权益恒等式、合计行防重复计入核对、科目结构与数据质量检查。只输出差异与建议，不执行过账或锁定账期。

## 用法

```
python scripts/close_check.py --trial tb.xlsx [--checklist checks.json] \
    --output close-check.xlsx --report close-report.json
```

- 列名自动识别（科目编码/科目名称 + 期初/本期/期末 × 借/贷，关键词包含即匹配）
- 默认勾稽四项（TB-OPEN/TB-PERIOD/TB-CLOSE/EQ-1），可用 `--checklist` 自定义（表达式支持 `col('列')`、`sump('列','前缀')` 与 `+ - * ==`）
- 退出码：0=完成（含发现差异与未决项），2=输入错误（文件不存在/损坏/清单非法）

## 判定规则（balanced 的完整条件）

`verdict=balanced` 仅当同时满足：

1. 全部勾稽通过——引用列缺失时检查以"缺少列"失败，绝不以 0==0 通过；引用列含不可解析/非有限/无缓存金额时标记 `unreliable_columns` 且不通过
2. 合计行与明细复算一致（逐行 stated vs recomputed，分位精度）
3. 无未决项：金额无法解析（如 NOT_A_NUMBER）、金额非有限数（如 1e400→inf）、公式无缓存值（data_only 读回空且原格是公式）、缺少科目编码、合计行金额问题
4. 无科目编码重复、无父子科目并存（合并层级重复计入风险）

任一不满足 → `differences_found`，`verdict_reasons` 逐条说明。

## 数据质量语义

- 无法解析/非有限/无缓存的金额**不静默按 0**：所在行进 `pending_items`，所在列令引用它的勾稽检查标记不可靠且不通过
- 缺少科目编码的行计入 `col()` 合计但不参与 `sump()` 前缀分类，显式列为未决项；其非零金额令 sump 类勾稽标记不可靠
- 合计/总计行不参与明细求和（防重复计入），但逐行与复算值比对
- 浮点精度：左右各按分位（2 位小数）取整后比较，0.1+0.2 vs 0.3 不误报；≥0.01 的差异必报

## 输出

- `close-check.xlsx`：勾稽检查（编号/名称/表达式/左值/右值/差异/结论/备注）、未决项（行号/原因/科目/列）、结构检查（科目重复/父子并存）三个 sheet
- `close-report.json`：`checks` / `total_row_check` / `structure_review` / `pending_items` / `verdict` / `verdict_reasons` / `note`

## 测试

`tests/office_skills/test_month_end_close.py`（黑盒，独立手工推导预期值）：正常（金标准差异复算/平衡判定/浮点容差/自定义清单）、复杂（缺列/合计不符/不可解析金额/无缓存公式/非有限数/科目重复/父子并存/缺编码行不静默丢弃）、异常（损坏文件/非法清单/文件不存在）。

冻结缺陷复测：`work/skills-comparison-20261013/office-delivery-glm53/benchmarks/close-complex/after-fix-20261015/`（passed=true，未覆盖 before-review 历史证据）。

## 来源与许可

原创实现（decision-register：independent_implement；无第三方代码复用，豆包 M08 仅目录快照对标未搬运代码）。代码与文档随仓库根 LICENSE（Apache-2.0）分发；运行时依赖 openpyxl 3.1.5（MIT），已登记 license-register.json runtime_dependencies。
