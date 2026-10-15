# hr-material-analysis（人力材料分析撰写）

## 定位

薪资表/考勤表/花名册（.xlsx，可只提供其一）跨表比对：按工号关联、缺勤差异手工可复算、部门名单汇总、HR 分析简报 docx 草稿，附机器可读 hr-report.json。敏感字段（身份证/银行卡/手机号）默认脱敏。不修改源表。

## 用法

```
python scripts/hr_compare.py --payroll p.xlsx [--attendance a.xlsx] [--roster r.xlsx] \
    --outdir out/ [--no-mask]
```

- 列名关键词识别：工号/员工编号/员工号/编号、姓名、部门、应发、实发、应出勤、实出勤、身份证/银行卡/手机/电话
- 退出码：0=完成（冲突/数据质量问题属于分析发现）；2=输入错误（未提供任何表、文件损坏、**缺少工号列**）

## 防静默规则（脚本保证）

1. **缺工号列报错**：任何一张提供的表缺工号列 → 退出码 2、stderr 指明文件与实际表头，绝不以 0 人静默成功。
2. **工号冲突不挑行**：同表同工号多行且字段值不一致 → 不取最后行、不取首行、不合并：该人员标注"工号冲突"、冲突字段留空、**不计入部门汇总与应发合计**；「重复工号」sheet 与 hr-report.json 记录来源文件、行号与各字段值；简报声明"X 人因工号冲突未计入汇总"。
3. **一致重复去重留痕**：同工号多行内容完全一致 → 去重计入一次（不重复计薪），单列于「重复工号」（判定"内容一致(已去重计入)"），人员标注"工号重复(一致)"。
4. **考勤口径**：仅当提供考勤表时才比对缺记录（薪资有考勤无→缺考勤记录，反之缺薪资记录）；**未提供考勤表不做缺勤结论**（不默认满勤、不写"已核查缺勤 0 天"）；考勤表缺应出勤/实出勤列 → 明确"缺勤不可计算"。缺勤=应出勤−实出勤，逐人可复算。
5. **数据质量不按 0**：金额/天数为空、不可解析（N/A）、非有限数（NaN/inf/1e400）、公式无缓存值（两次加载 data_only 区分）→ 逐人列入"数据质量"列（含表名/行号/原值），不参与合计；应发表头缺失时合计留空不按 0。
6. **不静默丢行**：工号为空的行 → 「未关联行」sheet 单列（敏感列省略）；合计/总计行剔除汇总并在 hr-report.json 留痕（行号）。
7. **敏感脱敏**：身份证/银行卡/手机号列默认脱敏为 ■（`--no-mask` 显式关闭）；明文不进入任何输出（比对表脱敏列、hr-report.json、未关联行摘要、重复工号明细、简报），清单写入 mask-manifest.json。

## 输出（outdir/）

- `compare-report.xlsx`：人员比对（工号/姓名(薪资)/姓名(考勤)/部门/应出勤/实出勤/缺勤天数/应发/实发/异常/数据质量/冲突明细/敏感列脱敏）、重复工号（来源文件/表/工号/行号/判定/字段值）、未关联行
- `roster-summary.xlsx`：部门汇总（部门/人数/应发合计，仅无冲突人员）+ 汇总说明（汇总范围/应发口径/考勤口径）
- `draft.docx`：简报草稿（人数与汇总范围、考勤口径、异常/数据质量计数、冲突排除说明均由计算值填充）
- `hr-report.json`：people_total/in_summary/excluded_conflict、conflicts（文件+行号+字段值）、dropped_rows、skipped_total_rows、data_quality、attendance_provided/total_absence/absence_computable、pay_sum、dept_summary、people_detail
- `mask-manifest.json`

## 测试

`tests/office_skills/test_hr_material_analysis.py`（黑盒，9 项，独立手工推导预期值）：正常（三表金标准/脱敏无明文）、复杂（工号冲突不静默挑行/不可解析与非有限与无缓存公式不按 0 计入/姓名不一致与缺记录/一致重复去重/仅薪资表无缺勤结论/考勤缺列不可计算）、异常（缺工号列报错、损坏文件、未提供表、空数据表真实 0 人）。

冻结缺陷复测：`work/skills-comparison-20261013/office-delivery-glm53/benchmarks/hr-complex/after-fix-20261015/`（duplicate/bad-header，未覆盖 before-review 历史证据）。

## 来源与许可

原创实现（decision-register：independent_implement；无第三方代码复用，豆包基线仅为目录快照对标未搬运代码）。代码与文档随仓库根 LICENSE（Apache-2.0）分发；运行时依赖 openpyxl 3.1.5（MIT）、python-docx（MIT），已登记 license-register.json runtime_dependencies。
