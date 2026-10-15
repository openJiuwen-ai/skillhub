# Office Skills 最终独立验收与交付说明

本文件记录 18 个办公技能包的最终独立验收结果。技能开发由 GLM-5.3 会话完成；用户确认 GLM-5.3 配额耗尽后，按 `model-fallback-policy.json` 切换到 `deepseek/deepseek-flash` 执行本轮最终验收。历史 GLM 提交、冻结输入与失败日志均保留。

- 分支：`codex/office-skills-glm53`
- 验收依据提交：`d8fc1af`
- 验收日期：2026-10-15
- 验收方式：完整套件全量重跑 + 官方 CLI 校验/打包 + 冻结控制器复验

## 一、本轮实测通过项（真实结果）

| 检查 | 命令 | 结果 |
|---|---|---|
| 全量黑盒测试 | `venv\Scripts\python.exe -m pytest tests/office_skills -q --basetemp=<可写目录>` | 177 passed / 0 failed |
| 技能校验 | `jiuwen-teamskills validate <skill>` | 18/18 通过 |
| 技能打包 | `jiuwen-teamskills pack <skill> --output <dir>` | 18/18 成功 |
| 发布候选包 | 18 zip + `.sha256` + `dist-manifest.json` | 18/18 sha256 自校验一致 |
| 脱敏冻结验收器 | `benchmarks/redaction-pdf-sidechannels/verify_fixed.py` | passed=true，10/10 checks，leaks=[] |

18 个新增技能全部交付；17 个计入任务级验收通过（门槛 ≥ 10）；`online-office-delivery` 计入交付但因缺少宿主授权不计入通过数。

## 二、未验证 / 阻塞（如实声明，不冒充通过）

- **parity = not_verified**：无法实际调用豆包客户端，无两侧同题真实结果；按策略不得伪造基准结果，双方真实结果齐备前保持 `not_verified`。
- **真实在线交付 blocked**：无 `lark-cli`/飞书授权，`online-office-delivery` 以退出码 3 显式 blocked，不伪造在线链接或截图。
- **not_run**：渲染级检查与 Excel 活公式重算（无 LibreOffice）、扫描页 OCR（无引擎，标 `needs_vision`）、真实机器翻译质量（无 MT 引擎）。
- 既有技能（如 `xlsx`、`pptx-craft`、`email-manager` 等）不在本任务范围，本轮不修改也不重复测试。

## 三、本任务范围内的集成修复

`tests/office_skills/test_business_family.py::test_online_delivery` 为遗留用例，仍断言旧契约 `out/delivery-result.json`；而 `online_delivery.py` 已在提交 `c40c860` 重写为输出 `out/delivery-report.json`。该不一致造成全量复跑 1 项失败。已按当前实际契约更新该测试断言，**未修改任何技能实现**；修复后 177 项全通过。

另：Windows 默认 pytest 临时目录不可写（WinError 5）会令 fixture 阶段报错，运行测试时请显式指定 `--basetemp`。

## 四、许可

- `chinese-official-doc` 复用 `@user_f3d82da7/chinese-official-writing` 2.0.33（MIT），包内保留完整 `LICENSE.md`，`NOTICE.md` 注明来源与版本。
- 其余技能为自主实现；无许可竞品包仅作功能对标，未复制源码/文档/测试。
- 运行依赖：`python-docx`、`openpyxl`、`python-pptx`（MIT）、`pymupdf`（AGPL/商业双许可，独立安装依赖、独立进程调用，不修改不分发本体）、`pypdf`（BSD-3）。

## 五、交付状态

本地提交于 `codex/office-skills-glm53`，**未推送、未发布到 SkillHub、未自动安装到产品**。发布候选包位于任务工作目录 `dist/`（相对目标仓库外部），仅用于审查。远端投递方式待用户确认后执行。
