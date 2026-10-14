# sheet-consolidate-reconcile 交付说明

多源 xlsx/csv 合并、清洗与对账：2 份以上来源/口径不同的表格，按主键合并为标准化台账，剔除合计行、逐值对账、显式标注口径来源与差异明细，产出可回溯的合并表与审计报告。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 2+ 份 .xlsx/.csv，指标同名但来源/口径不同（如各区域销售表、多系统导出） | 是（核心场景） |
| 列名口径映射（`column_map`，如 `编码`→`产品编码`、`销售额(万元)`→`销售额`） | 是 |
| 聚合前剔除主键为 合计/总计/小计/累计 的行，报告列明行号 | 是 |
| 同名不同口径列显式标来源：合并表列名 `指标@来源` | 是 |
| 同源重复主键：duplicates 明细（次数+行号），不静默去重，对账逐值比较 | 是 |
| 源缺失主键清单、匹配率可复算（=齐全主键/唯一主键） | 是 |
| 差异回溯到源表：`源明细` 表含来源/主键/指标原值/行号 | 是 |
| xlsx 公式单元格带缓存值（Excel 等保存过） | 是：静态读取缓存值参与对账，`formula_handling.cached_cells_per_source` 计数 |
| xlsx 公式单元格无缓存值（程序生成未重算） | 显式降级：`formula_handling.no_cache` 逐条列明（来源/主键/指标/行号/公式文本）+ `pending_values`，合并表标 `(公式未算)`、行标 `待核`，不猜不补 0，不参与数值比较 |
| 文本型指标值（如"待定/N/A"） | 列入 `pending_values`（带原值与行号），不算差异，不补 0 |
| 非有限文本（`nan`/`inf`，可被 `float()` 解析但非有限） | 列入 `pending_values` 按原值待核，不当数值参与对账，不静默变 `None`，报告不输出非标准 JSON 的 `NaN`/`Infinity` |
| 主键列缺失（映射后找不到）/ 源文件损坏或不存在 | 该源记 `failed_sources`，其余源继续；全部失败退出码 2 |
| 公式重算（LibreOffice/Excel 引擎） | 否：静态读值不重算，无缓存公式显式列为待核（报告 note 声明二者分开） |
| 单文件单元格级编辑 / 统计可视化建模 | 否（用 xlsx / data-analysis） |

## 安装与依赖

- Python 3.9+
- openpyxl（MIT）
- 标准库：argparse / csv / json / os / re / sys

技能包结构：

```text
sheet-consolidate-reconcile/
├── SKILL.md
└── scripts/consolidate.py
```

## 用法

```bash
python scripts/consolidate.py --config config.json \
    --output merged.xlsx --report reconcile-report.json
```

config.json（源文件相对路径以 config 所在目录为基准）：

```json
{
  "primary_key": "产品编码",
  "metrics": ["销售额"],
  "sources": [
    {"file": "east.xlsx", "sheet": "Sheet1", "source_name": "华东区",
     "column_map": {"产品编码": "编码", "销售额": "销售额(万元)"}},
    {"file": "south.csv", "source_name": "华南区", "column_map": {}}
  ]
}
```

表头在前 20 行内自动定位（要求含主键目标列）；CSV 按 utf-8-sig 读取。

## 输出

- `merged.xlsx`
  - `合并汇总`：每主键一行，列 `主键 / 指标@来源… / 数据一致(是|否|待核) / 缺失来源`
  - `源明细`：全部源行（来源/主键/行号/指标原值），无缓存公式显示 `(公式未算)<公式>`
  - `重复主键`：来源/主键/出现次数/行号
- `reconcile-report.json`：`match_stats`（唯一主键/齐全/单源/差异主键数/匹配率）、`mismatches`（主键+指标级各源值并列）、`duplicates`、`source_missing`、`skipped_total_rows`、`pending_values`（带行号）、`formula_handling`（缓存值使用计数 + 无缓存公式明细）、`failed_sources`
- stdout：JSON 汇总（match_stats/mismatch_count/duplicate_groups/formula_no_cache/pending）

## 退出码

- 0：完成（可含差异/待核/部分源失败）
- 2：config 损坏或缺字段 / 全部源失败 / 输出不可写

## 验收依据与测试

对应基线 M19（同名不同口径列显式标来源、聚合前剔除合计行、差异可回溯到源表）、M20（重复主键给出明细而非静默去重、未匹配单列出、匹配率可复算）、M15（多源对账方向）与补充基准 S02（本技能分担：公式单元格静态读值与重算分开记录、无缓存公式不猜不补 0；公式重算/循环引用阻断归 xlsx 技能）。黑盒测试位于 `tests/office_skills/test_sheet_consolidate_reconcile.py`（6 项，独立推导预期值，不 mock 实现）：

1. 正常：三源合并对账金标准——唯一主键 5、齐全 2、匹配率 0.4、单源 1、差异主键 2（值不同+重复多值）、华南 P004 重复明细、华东合计行剔除、华北缺失 P002、合并表 `指标@来源` 列与 `源明细` 行数
2. 复杂（公式）：华东 P001 公式带缓存值（zip 级补写 `<v>` 模拟 Excel 保存）参与对账、华南 P001 同公式无缓存值——`cached_cells_per_source` 计数、`no_cache` 逐条（来源/主键/指标/行号/公式文本）、`pending_values`、P001 不算差异不算缺失、合并表 `(公式未算)`+`待核`、`源明细` 公式文本与缓存值各自正确
3. 复杂/异常（不可数值化值）：`待定` 文本 → pending 带原值与行号、不算差异、合并表空（非 0）、`待核` 标记、无 "None" 字符串化伪值
4. 异常（非有限文本）：`nan`/`inf` 可被 `float()` 解析但非有限 → 原值待核、不计差异不计缺失、报告无 `NaN`/`Infinity` 字面量
5. 异常：单源损坏/缺失（`failed_sources` 记录，其余源继续，退出 0）；全部源失败退出 2

家族回归测试 `tests/office_skills/test_sheet_consolidate.py`（3 项）同轮通过。在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_sheet_consolidate_reconcile.py --basetemp <任务目录下唯一临时目录>
```

修复记录（2026-10-14，相对初版）：初版公式单元格仅按 `data_only=True` 读值——无缓存公式被静默当作缺失（S02 缺口）。本轮增加 `data_only=False` 公式识别遍：缓存值采用并计数，无缓存公式逐条列明（含整行公式、主键列公式两种边界），pending 采集由"整源聚合"改为逐行记录（修复 None 值被字符串化为 "None" 的伪 pending），合并表增加 `待核` 状态，`_display_num` 修复 inf/nan 边界。第二列复核发现纯 `_display_num` 修复不足：文本 `nan`/`inf` 会被 `float()` 解析为非有限浮点进入数值对账，合并表静默落成 `None` 且报告序列化出非标准 JSON 的 `NaN`/`Infinity`；已在 `to_num` 增加 `math.isfinite` 守卫，非有限值按原值走 `pending_values`/`待核`，并补第 4 项黑盒测试。已用任务 venv 行为探针实证 openpyxl 3.1.5 两种读法的行为差异后实现。

未测项（不宣称通过）：LibreOffice/Excel 引擎公式重算与循环引用阻断（归 xlsx 技能，未装引擎未测）；豆包端同题竞品验收（无授权，parity=not_verified，不伪造）；真实 Excel 导出的共享公式/数组公式文件（机制同普通公式，未取样验证）。

## 来源与许可

原创实现（无外部代码复制；竞品 doubao/sheet 与 workbuddy excel-xlsx/wps-office-suite 均无许可，仅功能对标未搬运）。openpyxl 3.1.5（MIT）为运行时依赖，安装于任务 venv，未全局安装；行为探针仅调用其公开 API，未复制其源码（dependency-reference/openpyxl/_reader.py 为只读参考，不属于交付包）。
