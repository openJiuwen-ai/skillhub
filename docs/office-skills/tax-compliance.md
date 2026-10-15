# tax-compliance 交付说明

税务合规材料归集与规则分析：扫描材料目录生成归集台账（发票/合同/流水分类 + Decimal 精确金额统计 + 数据错误清单），按业务背景匹配规则库输出逐条可溯源的适用规则分析（现行/历史双轨、官方URL+文号+条款+生效失效日期+核查日期），并列出待确认事实清单。事实缺失或期间不可解析时标 needs_confirmation，绝不输出已确认适用；金额无法解析、空值、无缓存公式、非有限数绝不静默按 0 归总。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 材料归集：发票台账/合同 docx/收支流水 csv·xlsx 分类 + 关键统计（Decimal 合计、单位"元（人民币，材料口径）"、精确字符串值） | 是 |
| 金额不可解析（如 NOT_A_NUMBER）/ 空值 / 无缓存公式 / 非有限数 → 记入 `data_errors`（文件/行/列/值/原因），合计只含已解析值，退出码 1 | 是 |
| 期间门控：`period` 支持 YYYY/YYYYQn/YYYY-MM/YYYY-MM-DD；缺失或不可解析（如 2026-02-30）→ 全部规则 needs_confirmation | 是 |
| 有效期双轨：2026-01-01 起匹配增值税法体系；2025-12-31（含）前匹配历史规则（暂行条例/36号文/2012年第24号），窗口外输出 `not_applicable_for_period` 并指向对应轨 | 是 |
| 事实门控：jurisdiction/taxpayer_type/entity_type 等缺失或无法识别 → needs_confirmation 且列出缺失事实 | 是 |
| 法定与优惠分条：小规模3%法定征收率与 2026年第10号起征点（月10万/季30万/按次1000元）、3%减按1%（均至2027-12-31）分别成条；优惠条件未确认不默认享受 | 是 |
| 跨境业务：零税率（实施条例第九条清单）/免税（11号第六条）/征税（11号第七条）分开；关键词"跨境服务"不直接判免税，须 service/buyer/consumed_overseas 事实 | 是 |
| 出口退（免）税：2026年起引 2026年第5号+第11号（2012年第24号已废止），须报关单/出口发票/收汇/备案单证事实与申报期限提示 | 是 |
| 空材料目录 / 全部不可读 → `materials_status=not_ready`，全部规则降为 needs_confirmation，退出码 1 | 是 |
| business.json 损坏 / 材料目录不存在 → 退出码 2 | 是 |
| 税款计算、申报表填写、合规性结论（"合规通过"） | 否（永不输出，不代申报） |
| 法规文本语义级法律意见 | 否（工程摘录，以主管税务机关解释为准） |

## 安装与依赖

- Python 3.9+
- openpyxl（MIT）、python-docx（MIT，合同内容嗅探用）
- 标准库：argparse / csv / datetime / decimal / json / math / os / re / sys

技能包结构：

```text
tax-compliance/
├── SKILL.md
├── references/tax-rules-cn.json   # 13条规则（现行9+历史4），自研整理
└── scripts/tax_review.py
```

## 用法

```bash
python scripts/tax_review.py --materials 材料目录 --business business.json --outdir out/
```

business.json 关键字段：`taxpayer_type`（一般纳税人/小规模纳税人）、`activities`、`period`、`jurisdiction` 为输出 applicable 的必备事实；`entity_type`（企业所得税）、`filing_period_type`/`sales_by_category`（小规模优惠）、`cross_border_service_details`（跨境）、`export_documents`（出口）分别支撑对应规则。

## 输出

- `materials-ledger.xlsx`（含"数据错误"表，如有）
- `rule-analysis.json` / `rule-analysis.md`（材料状态、数据错误、逐条规则含来源与有效期、缺失事实、待确认条件）
- `confirmations.json`（待确认事实清单）

退出码：`0` 分析完成且无阻断性数据问题；`1` 存在数据错误或材料未就绪；`2` 用法错误。

## 规则库来源与许可

规则库为本技能自研整理的公开法规条目工程摘录（原创，无第三方代码复用）。来源核验记录（2026-10-15 实际抓取官方页面全文核对条款）：增值税法（上海税务）、增值税法实施条例（国务院令第826号，天津税务）、2026年第10号/2026年第5号（总局法规库）、2026年第11号（上海税务）；企业所得税法与发票管理办法（2023年修订）的官方直链未能在开发环境核验（url_verified=false），交付前应在总局法规库按文号复核。运行时依赖 openpyxl/python-docx 均 MIT。

## 测试

黑盒测试 `tests/office_skills/test_tax_compliance.py`（13项）：正常（全事实+三类材料+精确合计）、复杂（历史期间双轨/优惠分条/跨境出口条件）、异常（缺期间管辖地/NOT_A_NUMBER/空材料/无缓存公式/坏文件/坏JSON/目录不存在）。冻结缺陷复测见 `work/skills-comparison-20261013/office-delivery-glm53/benchmarks/tax-complex/after-fix-run1/`。
