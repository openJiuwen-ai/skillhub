# evidence-digest 交付说明

多源材料证据归集与结构化台账：读取材料目录（docx/xlsx/csv/md/txt/图片），产出逐条可回溯证据台账、同口径数字冲突清单（保留期间/主体/单位）、跨口径待核清单与问题覆盖候选列表。读取失败、未知类型、公式缺缓存一律单列，绝不计入事实台账、覆盖匹配或数字统计。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| docx 段落/表格单元格、xlsx 非空单元格（Sheet!坐标+行首标签前缀）、csv 逐行、md/txt 逐行登记 | 是 |
| 每条证据可回溯（id/来源文件/定位/类型/confidence:"sourced"） | 是 |
| 同口径（期间+主体+单位一致或未声明分量兼容）不同数值 → conflicts 双记并列 | 是 |
| 冲突条目保留期间/主体/单位；不同显式期间（含全年 vs 显式月份/季度）或不同主体的数值**不判同口径冲突**，单列 `cross_caliber_review` 待核 | 是 |
| 问题覆盖：关键词命中仅是检索候选（`candidates_need_review`），不构成已证实答案 | 是 |
| 损坏文件 → `unreadable_files` 单列（含错误原因），不进台账/覆盖/统计，错误信息中的文件名不能让问题"有证据" | 是 |
| xlsx 公式无缓存值 → `formula_cells_without_cache` 单列（含公式与坐标），不编造数值 | 是 |
| 未知文件类型 → `unsupported_files` 单列，不静默跳过；空材料目录 → warnings 明示 | 是 |
| 图片 → `image_sources_needing_vision` 登记来源，不编造 OCR 内容 | 是 |
| 部分文件损坏不影响其余可用文件正常归集 | 是 |
| 图片内容识别/OCR、语义级"已证实答案"判定 | 否（候选需人工核对） |
| 损坏/不存在材料目录、问题文件不可读 | 退出 2 |

## 安装与依赖

- Python 3.9+
- python-docx（MIT）、openpyxl（MIT）
- 标准库：argparse / csv / json / os / re / sys

技能包结构：

```text
evidence-digest/
├── SKILL.md
└── scripts/evidence_ledger.py
```

## 用法

```bash
python scripts/evidence_ledger.py --materials 材料目录 --questions q.md --outdir out/
```

questions.md/txt 每行一个问题（支持 UTF-8 BOM）。输出 `outdir/`：

1. `evidence-ledger.json`：全量结果（ledger / image_sources_needing_vision / unreadable_files / unsupported_files / formula_cells_without_cache / conflicts / cross_caliber_review / coverage / warnings / stats）
2. `evidence-ledger.md`：人读版台账（含冲突/跨口径/读取失败/未知类型/公式缺缓存/图片/问题覆盖/提示各节）
3. `conflicts.json`：`conflicts`（同口径）+ `cross_caliber_review`（期间/主体不同的并列值，不判冲突）
4. `open-questions.json`：`no_evidence` 问题列表

## 退出码

- 0：归集完成（含存在读取失败/未知类型/公式缺缓存的情况——它们被单列并计入 warnings，不影响退出码）
- 2：材料目录不存在、问题文件不可读

## 口径与兼容性规则

- 期间分量：年（2026年）/季度（Q3、三季度）/半年（上半年）/月份（3月）/全年（全年、年度）；簇内相邻分量合并（如"2026年Q3"）
- 兼容性：分量双方均显式且不同 → 不同口径；未声明分量视为兼容（"Q3"与"2026年Q3"可同口径）；**全年 vs 显式季度/半年/月份 → 不同期间，不判冲突**
- 主体：显式主体词（甲方/乙方/X公司等）不同 → 不判同口径冲突；标签归一化会剥离期间粘滞前缀（"3月客户满意度"→"客户满意度"）

## 验收依据与测试

对应 `specs.json` 的 evidence-digest（多源材料→结构化证据台账；异常含图片 needs_vision、无来源推断不入结论、数字口径不一致列冲突）与 `decision-register.json`: independent_implement。基线条目 `baseline-matrix.json` rows[0]（豆包"整理评价报告、访谈、表格和图片中的证据"目录快照，未读正文，仅功能对标）。

黑盒测试位于 `tests/office_skills/test_evidence_digest.py`（8 项，独立推导预期值，不 mock 实现，不 import 被测模块）：

1. 正常（2）：五源材料（md/docx/xlsx/csv/png）全量归集——独立计数 13 条证据、来源定位/类型/置信、xlsx 标签前缀、图片 needs_vision 不入台账、同口径冲突（销售额 1200 vs 1150 万元、满意度 92% vs 93%）含期间/主体/单位字段、候选语义；同口径冲突 + 两次运行确定性。
2. 复杂（3）：不同年份期间/不同主体不判同口径冲突并保留期间（2023年/2024年）与主体（甲方/乙方）于 cross_caliber_review；xlsx 公式缺缓存单列不编造（=B1*2 不产生 20）；未知类型（.doc/.zip/.pdf）单列不参与匹配。
3. 异常（3）：损坏 docx/xlsx 错误串绝不成证据（冻结缺陷回归：问题"合同"仍 no_evidence、错误串不进台账/统计、部分失败不影响可用文件）；空材料目录警告+全部问题无证据；材料目录/问题文件缺失退出 2。

在仓库根目录运行（任务 venv 解释器，任务目录下唯一临时目录）：

```bash
C:\code\jiuwen\work\skills-comparison-20261013\office-delivery-glm53\venv\Scripts\python.exe -m pytest tests/office_skills/test_evidence_digest.py --basetemp <任务目录下唯一测试临时目录>
```

家族回归（同技能）：`tests/office_skills/test_business_family.py::test_evidence` 通过。

## 修复记录（2026-10-15，相对初版）

主控冻结探针 `benchmarks/evidence-failure/before-review/controller-results.json` 证实真实缺陷，均已修复并复测：

1. **损坏 docx 的读取失败错误串被做成 E-0001 证据**：`<读取失败: Package not found at '…合同.docx'>` 进入台账且 `has_numbers=true`（路径含数字）。修复为读取失败单列 `unreadable_files`（含错误原因），绝不进入台账/覆盖/统计。
2. **错误信息含文件名让问题"合同"被判有证据**：问题按关键词匹配到错误串。修复后读取失败/未知类型/公式缺缓存/图片一律不参与匹配，问题"合同"正确判 `no_evidence`。
3. **SKILL.md 承诺的 conflicts.json 从未生成**：补齐独立 `conflicts.json` 输出（含 cross_caliber_review）。
4. **冲突判定无期间/主体概念**：不同期间的值被自动判为同口径冲突。修复为口径=期间+主体+单位，冲突条目保留三者；显式不同期间（含全年 vs 月份/季度）或不同主体的并列值单列 cross_caliber_review，不判冲突。
5. **公式缺缓存被静默跳过**：`data_only=True` 读到 None 即丢弃。修复为与 `data_only=False` 双读比对，公式无缓存值单列 `formula_cells_without_cache`（含公式与坐标）。
6. **未知类型静默跳过、空材料无提示**：单列 `unsupported_files`；空目录/无可用内容写入 warnings。
7. **关键词命中被当作"已证实"**：覆盖状态改为 `candidates_need_review`（检索候选，需人工核对），并修复问句分词（原 `[\u4e00-\u9fa5A-Za-z0-9]{2,}` 将"Q3销售额是多少"整句当作单个关键词导致永不命中）。
8. **标签归一化粘滞**："3月客户满意度"与"客户满意度"因期间字粘入标签而无法对齐；剥离期间粘滞前缀并对超长尾串取尾端 6 字。

冻结复测：`benchmarks/evidence-failure/controller-after-fix-20261015/`（新目录，未覆盖 before-review 历史证据），`passed=true`。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；未复制外部代码；基线条目仅目录快照对标，未搬运任何竞品代码）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE`（Apache License 2.0）分发。
- 运行时依赖 python-docx 1.2.0（MIT）、openpyxl 3.1.5（MIT），已登记于 `license-register.json` runtime_dependencies。任务中安装于任务 venv，未全局安装。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果（基线仅为目录快照，未读竞品正文）。
- 图片 OCR / 视觉识别：图片仅登记 `needs_vision` 来源项，内容识别不在本技能范围。
- 语义级"已证实答案"判定：覆盖命中是确定性关键词候选，语义证实需人工或宿主模型复核。
- docx 页眉/页脚/批注/修订、xlsx 图表/透视表数据源、doc（老版二进制 Word）、pdf 不在读取范围（pdf/doc 单列为未知类型）。

