# proposal-bid-generation 交付说明

方案与标书生成：解析招标文件条款（编号/短条款/续行/表格条款，带出处），与资质材料库
做证据匹配，输出响应矩阵（xlsx+json）、缺口清单、待确认清单与投标草稿 docx。核心诚实
规则：关键词只做检索候选；仅内容级肯定陈述计 responded；否定/过期陈述 → gap 并保留
原文；主体待核或仅文件名命中 → needs_confirmation；草稿不自动承诺资格满足。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 编号条款（第X条/一、/1./（一））逐条解析；短条款（"4. 须盖章"）不做长度过滤 | 是 |
| 无编号招标文件 → 段落回退虚拟编号并在报告声明（paragraph_fallback） | 是 |
| 续行（编号行后的无编号行并入上一条款，带 continued 标记） | 是 |
| docx 表格条款逐行成条、首行表头识别跳过、出处（docx:表N行M） | 是 |
| 泛化词（公司/项目/合同/万元/证书/认证/预算/报价等）剔除后才生成检索关键词；纯数字不作证据关键词 | 是 |
| 条款含 ISO9001/PMP 等字母数字标识时，肯定证据必须命中该标识（防 ISO27001 等近邻误配） | 是 |
| responded：资质内容中的肯定陈述（记录文件+原文摘录，可回溯） | 是 |
| 否定表述（未取得/尚未/不得宣称/没有…）→ gap（negative_evidence），原文保留 | 是 |
| 过期/失效表述（已过期/吊销/注销…）→ gap（expired_or_invalid） | 是 |
| 仅文件名命中（pdf 等不支持内容提取或读取失败）→ needs_confirmation（candidate_only） | 是 |
| `--company` 证据主体核验：句内第三方公司名不一致 → subject_mismatch；未提供 --company → subject_unverified | 是 |
| 肯定证据与否定/过期/主体问题并存 → needs_confirmation（conflicting_evidence） | 是 |
| 草稿：responded 条款列证据并要求人工复核，不写"响应：满足"；缺口标【待补充-缺口】并警示否定陈述 | 是 |
| 资质 .md/.txt（utf-8/gbk）/​.docx/​.xlsx 内容提取；读取失败逐文件记录不中断 | 是 |
| 招标文件不存在/损坏/空文件、资质目录不存在 → 退出 2 | 是 |
| pdf/图片等格式的内容提取 | 否（仅文件名候选，如实 needs_confirmation） |
| 语义级资质真伪/印章效力/有效期真实校验 | 否（主体核验为字符串级，需人工复核） |

## 安装与依赖

- Python 3.9+
- python-docx（MIT）、openpyxl（MIT）
- 标准库：argparse / json / os / re / sys

技能包结构：

```text
proposal-bid-generation/
├── SKILL.md
└── scripts/bid_matrix.py
```

## 用法

```bash
python scripts/bid_matrix.py --tender tender.docx --quals quals_dir --outdir out/ \
        [--company 投标主体名称]
```

输出见 SKILL.md（response-matrix.xlsx/.json、gaps.json、bid-draft.docx、report.json）。

## 退出码

- 0：矩阵/草稿已生成（含全部 gap 或 needs_confirmation 的诚实结果）
- 2：招标文件不存在或不可解析（含空文件）；资质材料目录不存在

## 验收依据与测试

对应 `specs.json` proposal-bid-generation（逐条响应矩阵+资质回溯+不编造；异常含无编号
段落虚拟编号、无资质→gap）；`decision-register.json`: independent_implement。基线条目
`baseline-matrix.json` M07（豆包"方案与标书生成"目录快照，未读正文，仅功能对标）。

黑盒测试位于 `tests/office_skills/test_proposal_bid_generation.py`（10 项，独立推导预期
值，不 mock 实现，不 import 被测模块）：

1. 正常（2）：8 输入混合资质（docx/md/txt/pdf）6 条款全保留，responded=3（证据文件
   真实存在、摘录可回溯）、pdf 仅文件名候选 → needs_confirmation、注册资本/盖章 →
   gap，草稿无"响应：满足"且有复核/待补充/待确认标记；无编号段落回退 + 空资质目录
   全缺口矩阵完整。
2. 复杂（3）：冻结用例复现（否定证据不 responded、第 4 条短条款"须盖章"保留、草稿
   警示相反陈述）；docx 第X条+续行+表格条款+表头跳过+出处+前言计数；过期/主体未核
   验/--company 一致与不一致/证据冲突五类判定。
3. 异常（5）：招标文件不存在、损坏 docx、空文件（均退出 2）；资质目录不存在（退出
   2）；不可读资质（损坏 docx+pdf）仅文件名候选 → needs_confirmation 且不进缺口。

在仓库根目录运行（任务 venv 解释器，任务目录下唯一临时目录）：

```bash
C:\code\jiuwen\work\skills-comparison-20261013\office-delivery-glm53\venv\Scripts\python.exe -m pytest tests/office_skills/test_proposal_bid_generation.py --basetemp <任务目录下唯一测试临时目录>
```

家族回归（同技能）：`tests/office_skills/test_business_family.py::test_bid` 通过
（8 条款全解析、responded>=2、缺口不编造、证据文件存在、草稿标记保持）。

## 修复记录（2026-10-15，相对初版）

主控冻结探针 `benchmarks/bid-complex/before-review/controller-results.json` 证实两项
真实误判，均已修复并复测：

1. **否定证据被判 responded**：资质材料明确写"本公司目前未取得ISO9001质量体系认
   证。不得宣称已经取得。"，原实现仅凭文件名/关键词命中即 responded 并在草稿生成
   "响应：满足"。修复为句子级证据判定：含关键词的句子出现否定/过期表述 → gap，原
   文摘录保留在矩阵、gaps.json 与草稿警示中；草稿措辞改为"检索到支撑材料，请复核
   后确认响应程度"，不再自动承诺满足。
2. **短强制条款丢失**：原实现按 `len>=6` 过滤条款正文，"4. 须盖章"被静默丢弃（仅
   解析 3/4 条）。修复为不做长度过滤，编号条款全部保留；并补齐续行、表格条款、
   前言识别与条款出处。

同时按规则封堵同类过度承诺路径：关键词仅作检索候选（泛化词剔除+纯数字不参与）；
字母数字标识强校验（防近邻资质误配）；仅文件名命中/主体待核/证据冲突一律
needs_confirmation；空资质目录仍输出完整全缺口矩阵；招标文件不存在/损坏/空、资质目
录不存在均退出 2。冻结复测在 `benchmarks/bid-complex/after-fix-20261015/`（新目录，
未覆盖 before-review 历史证据），复跑脚本 probe_rerun.py 可独立验证。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；无第三方代码复
用；基线 M07 仅为目录快照对标，未搬运任何竞品代码）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE`（Apache License 2.0）分发。
- 运行时依赖 python-docx 1.2.0（MIT）、openpyxl 3.1.5（MIT），已登记于
  `license-register.json` runtime_dependencies。任务中安装于任务 venv，未全局安装。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果。
- pdf/图片等资质文件的内容提取：仅文件名候选（needs_confirmation），不做内容判定。
- 语义级资质真伪、印章效力、证书有效期真实性：主体核验为字符串级（--company 与句内
  公司名），有效期仅识别"已过期/失效"等显式表述，其余需人工复核。
- 密级/资质材料中的多级子目录：已按 os.walk 递归扫描（相对路径记录），未对超深目录
  做专门测试。
