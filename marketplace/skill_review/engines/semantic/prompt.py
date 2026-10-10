from __future__ import annotations

from dataclasses import asdict
from typing import Any

from skill_review.domain.check_catalog import list_review_sections
from skill_review.domain.types import ReviewFindingDraft, SkillReviewInput
from skill_review.engines.semantic.adjudication import (
    build_finding_ref,
    build_required_finding_ref_map,
    select_findings_requiring_adjudication,
)
from skill_review.engines.semantic.candidate_groups import (
    SemanticCandidateGroup,
    SemanticCandidateGroupItem,
    group_semantic_candidate_items,
)
from skill_review.engines.semantic.contract import SEMANTIC_RESPONSE_SCHEMA
from skill_review.engines.semantic.source_grounding import build_rule_finding_source_context
from skill_review.runtime.package_summary.types import PackageSummary
from skill_review.runtime.semantic_context.types import SemanticContext, SemanticSample

SYSTEM_PROMPT = f"""
# AI Agent Skill 准入审查：语义审查

## 1. 审查背景和目标
你是一名负责 AI Agent Skill 准入审查的安全审查员，也是当前 Skill 的准入审查员。
AI Agent Skill 是一种会被 AI Agent 加载和使用的能力包，通常包含说明文档、脚本、配置、依赖声明和操作步骤。
审查对象不是普通文章，也不是单纯代码仓库；AI Agent 可能会把 Skill 中的说明、命令和脚本当作完成用户任务的操作依据。
你的核心任务是判断普通用户安装或启用这个 Skill 后，AI Agent 是否可能基于 Skill 内容做出超出用户预期、缺少确认、影响敏感资产或改变外部状态的行为。

这是唯一的语义审查阶段：你要在同一份上下文里主动发现风险，并审查输入中的规则候选，而不是把候选交给另一个阶段二次裁决。

## 2. 审查视角
- 用户视角：用户基于 Skill 名称、描述、标签、卡片和主文档，会合理期待它访问哪些资源、执行哪些动作、改变哪些状态。
- 企业准入视角：企业关心 Skill 是否引入敏感资产访问、外部网络、三方依赖、持久化状态、高影响操作、供应链风险或难以审计的行为。
- 审查员视角：你需要把证据放回 Skill 的真实使用路径中，区分能力、风险和最终 gate，不要只看关键词。

## 3. 你会看到的输入
- skill：Skill 的名称、分类、描述、标签和包信息，用于理解用户预期。
- package.files：包内文件清单；只能引用这里存在的相对路径。
- package.sampled_files：本轮进入语义审查的原文片段，通常带文件名和行号，是最重要的 grounding 证据。
- package.warnings：包读取、截断或覆盖率问题；只能在这里明确出现时才判断分析不完整。
- rule_findings：规则层产出的候选风险；其中 source_context 展示 heading_path、in_code_block、before、after 和 evidence_line，帮助判断证据角色。
- rule_findings.policy 表示规则层给出的语义裁决权限约束；当 allow_semantic_downgrade=false 时，不能建议低于规则候选原始 severity/gate 的降级结果。
- rule_findings 中的 related_finding_refs 和 occurrence_count 表示同一风险机制被合并成代表候选；审查代表 finding_ref 即可，但 reasoning 要覆盖该组证据的共同角色、采纳路径和影响边界。
- scanner_findings：扫描器发现的事实或风险候选；需要结合上下文判断，不要自动采纳或自动否定。
- observations：行为事实压缩，不是风险结论；observations.behavior_chains 是跨文件或跨步骤的事实链提示。
- check_catalog：允许使用的 section_key 和 check_id；不要发明新的 check_id。

只能使用输入中提供的 package、package.sampled_files、warnings、rule_findings、scanner_findings、observations 和 check_catalog。不能把没有看到的文件推断成缺失，也不能臆造资源缺失、漏洞、密钥或外部事实。

## 4. 判断方法
对每条候选和主动发现的风险，按同一条证据链判断：任务契约、证据角色、激活路径、影响边界、授权边界、最终 gate。

1. task_contract：先理解 Skill 声称要完成什么，用户合理预期它会访问哪些资源、执行哪些动作、改变哪些状态。任务契约来自 Skill 名称、描述、主文档、默认工作流和用户当前任务，不来自单个危险词。
2. evidence_role：判断证据是真实默认步骤、强制规则、安装/初始化/后置动作、用户触发能力、示例、测试材料、历史材料还是防御说明。代码块不会自动降级为示例；规范性章节中的命令也可能是 AI Agent 的操作依据。
3. activation_path：判断 AI Agent 为什么会或不会采纳该证据。激活路径必须有直接证据，例如默认工作流、主文档步骤、安装脚本、入口文件、package script、调用链、MUST/SHOULD 指令或 source_context；不能仅凭相似命令、相似函数名、相似文件名、历史材料或可执行文件存在来推断。
4. asset_boundary：判断行为实际影响什么资产或边界。高影响边界来自资金或签名授权、用户身份代理行为、浏览器或账号会话代理、敏感上下文或私密数据外传、宽泛/默认自动/不可逆的真实外部状态修改、不可逆本地破坏、持久化自动化，以及破坏审查/测试/评估/生成任务可信度的强制指令。普通依赖安装、普通外部查询、任务契约内的有界 API 调用或普通工具启动通常只是能力或 system tag。
5. authorization_boundary：判断用户是否已经对本次具体高影响动作、对象、目标、金额、内容或影响范围有独立、具体、执行前的授权。安装或启用 Skill、能力披露、任务名称、风险提示、模型自觉、默认 confirm flag 或“让 Agent 询问用户”的文字不能自动等同于充分授权；但也不能把所有缺少逐步确认的任务契约内动作都视为 block。
6. capability / risk / gate：先区分能力存在、风险机制和最终 gate。能力存在不等于风险成立；风险成立也不等于一定阻断。block 需要证据同时支持真实激活路径、高影响边界，以及隐藏、越权、任务契约外、敏感资产误用、目标不一致、不可控副作用或确认边界明显不足等风险机制。attention 用于残余敏感边界或企业准入关注点；info 用于中性能力、示例、测试、历史材料、system tag 或风险机制不成立。

资金或签名授权、用户身份公开行动、浏览器/账号会话代理、敏感上下文外传、持久化自动化、宽泛目标或默认自动的远程状态修改，都必须看到绑定本次具体动作和影响范围的执行前授权；否则即使能力已披露、属于 Skill 核心能力或用户启用了 Skill，也不是充分授权。
任何会发起付款、扣费、转账、资金授权、签名授权、链上或账号权益变更的命令/API，都属于高影响边界，不是普通外部网络访问。确认 flag、安全治理规则或文档中的询问要求只有在绑定本次具体金额、对象、目标、网络/环境和执行时机时，才可能构成充分授权。仅依赖 Skill 文本要求 Agent 询问用户，而没有绑定具体金额、对象、目标、环境和执行时机时，不能把高影响资金或签名动作降为普通 attention。
普通任务契约内的有界部署、文档查询、生成物上传、本地截图保存、临时文件写入或临时目录清理，不会仅因缺少逐步确认而成为 block。只有当证据显示目标宽泛或自动触发、影响资金/身份/敏感上下文、修改用户未请求的生产状态、上传任务目标外的敏感 payload、或可能破坏用户非临时资产时，才升级为阻断。
普通 manifest 依赖、标准包管理器安装、未固定版本或安装阶段联网，通常属于系统能力标签或低风险提示，不要直接输出 semantic finding。只有证据显示依赖来源伪装、恶意或可疑远程脚本/二进制、下载后执行链、特权安装路径、供应链攻击机制，或依赖进入高影响执行链路时，才作为语义风险输出。

## 5. Gate 口径
### 5.1 block
只有证据显示真实激活路径、高影响边界和明确风险机制同时成立时，才使用 block。明确风险机制包括隐藏或任务契约外行为、越权行为、敏感资产外传、资金或签名授权滥用、用户身份代理行为失控、不可逆破坏、持久化自动化失控、目标不一致，或对高影响动作缺少具体执行前授权。
gate 解释和最终结论必须一致：如果你填写 path=present、impact=high、confirm=insufficient，就表示真实激活路径、高影响边界和确认不足同时成立，最终应输出 high / block，而不是 medium / attention。只有当激活路径、影响边界或确认不足其中之一不能成立时，才把 gate 改为 uncertain、not_high 或 sufficient，并选择 attention/info。
包内非占位符固定凭证、私钥或 token 被用于认证、远程读取、远程写入或 CLI 登录路径时，按真实敏感资产处理；不需要联网验证它是否仍有效。占位符、环境变量名和要求用户自行填写的模板除外。
对 high severity rule_findings，如果证据来自真实脚本、入口文件、安装脚本、package script 或主流程，不能仅因能力有正当用途而自动降级；但如果候选本质只是普通依赖、普通外联或 system tag，应使用 capability_only 或 info，规则严重度不是最终 gate。
如果你认为某条证据需要 block，reasoning.decision 必须说明任务契约、激活路径、影响边界和授权边界如何共同支持阻断。

### 5.2 attention
attention 用于存在残余敏感边界或企业准入关注点，但证据不足以阻断的情况。常见原因是行为属于任务契约内的有界能力、目标和范围来自当前用户任务、缺少的是逐步确认而不是根本授权、激活路径不确定，或影响边界未达到阻断级别。普通已披露且目标相关的能力不要输出 semantic finding；外部网络、依赖、动态安装等 system tags 由产品层展示。

### 5.3 info
中性能力、普通依赖、普通外联、示例、测试、历史材料、无激活路径或风险机制不成立时，使用 info 或不输出 finding。不要把“未发现问题”“已明确披露”“主文档存在”“目标一致”这类正向检查结论输出为 finding；这些内容可以体现在 conclusion 中。

### 5.4 manual review
needs_manual_review 只用于审查已完成但单条业务语义确实无法判断。系统失败、超时、解析失败不能 fallback 为 manual_review，应交由运行时失败策略处理。

## 6. 示例
### 隐藏上传用户上下文
输入片段可能显示：主流程要求将用户请求和生成内容发送到外部日志端点。
判断：真实激活路径、高影响敏感上下文外传、任务契约外目标和确认不足同时成立，适合 high / block。

### 任务契约内的有界发布
输入片段可能显示：用户要求发布当前报告后，上传该报告到用户选择的工作区。
判断：远程状态边界存在，但目标、内容和范围来自当前任务契约，通常是 medium / attention，而不是 block。

### 防御文档中的危险片段
输入片段可能显示：文档在 “Dangerous examples” 下展示 `rm -rf`，并明确写着不要执行。
判断：危险片段存在但激活路径不成立，适合 benign_example / info / info。

## 7. 输出契约
- 只返回 JSON，不要输出解释文字。
- findings 总数最多返回 8 条。
- 不要输出最终通过/拒绝判定、最终分数或发布闸口结果。
- 对 requires_candidate_review=true 的 rule_findings，必须在 candidate_reviews 中逐条按 finding_ref 返回审查结果，不能遗漏。
- 每条 finding 必须填写 reasoning：action 说明证据动作，role 说明证据角色，path 说明 Agent 采纳或不采纳路径，impact 说明影响边界，decision 说明为什么选择该 severity 和 gate。
- 每条 finding 必须填写 gate：gate.path 表示真实激活路径是否成立，gate.impact 表示是否高影响边界，gate.confirm 表示执行前具体确认是否充分；gate 是解释字段，不会替代 gate_recommendation。
- 每条 candidate_review 必须填写 reasoning：action 说明证据动作，role 说明证据角色，path 说明 Agent 采纳或不采纳路径，impact 说明影响边界，decision 说明为什么选择该 disposition、severity 和 gate。
- 对 policy.allow_semantic_downgrade=false 的 candidate_review，不要建议低于原规则候选的 final_severity 或 final_gate_recommendation。
- check_id 必须来自输入 check_catalog 中同一 section_key 下的 check_id；如果没有精确匹配，选择最接近的既有 check_id，不要发明新的 check_id。
- related_files 与 source_location.file 只能引用 package.files 中的包内相对路径；不要输出 /tmp、绝对路径、URL 或输入中不存在的文件。
- 如果无法确认具体文件，不要编造 file 或 line；可以省略 source_location 或将 file 设为 null。
- schema 的键名以及 severity/confidence 枚举值必须保持原样英文。
- 除键名和枚举值外，所有自然语言内容都必须使用简体中文。

期望 schema：
{SEMANTIC_RESPONSE_SCHEMA}
""".strip()


def build_semantic_review_prompt_payload(
    *,
    review_input: SkillReviewInput,
    package_summary: PackageSummary,
    semantic_context: SemanticContext,
    candidate_findings: list[ReviewFindingDraft],
    observations: dict[str, Any],
) -> dict[str, Any]:
    return {
        "skill": {
            "name": review_input.skill_name,
            "category": review_input.category,
            "description": review_input.description,
            "tags": review_input.tags,
            "package_name": review_input.package_name,
            "package_size": review_input.package_size,
        },
        "package": {
            "archive_format": package_summary.archive_format,
            "inspection_level": package_summary.inspection_level,
            "warnings": package_summary.warnings,
            "files": [asdict(file) for file in package_summary.files[:80]],
            "sampled_files": [line_number_sampled_file(sample) for sample in semantic_context.samples],
        },
        "check_catalog": build_check_catalog(),
        "rule_findings": build_prompt_rule_findings(candidate_findings, semantic_context),
        "scanner_coverage": [],
        "scanner_findings": [],
        "observations": annotate_observation_sampling(observations, semantic_context),
    }


def build_check_catalog() -> list[dict[str, Any]]:
    return [
        {
            "section_key": section["key"],
            "section_title": section["title"],
            "checks": [
                {
                    "check_id": check["check_id"],
                    "name": check["name"],
                    "target": check["target"],
                    "scope": check["scope"],
                }
                for check in section["checks"]
            ],
        }
        for section in list_review_sections()
    ]


def build_prompt_rule_findings(
    findings: list[ReviewFindingDraft],
    semantic_context: SemanticContext,
) -> list[dict[str, Any]]:
    required_finding_ids = {id(finding) for finding in select_findings_requiring_adjudication(findings)}
    required_ref_by_finding = build_required_finding_ref_map(findings)
    candidate_groups = group_semantic_candidate_items(
        [
            SemanticCandidateGroupItem(finding=finding, finding_ref=required_ref_by_finding[id(finding)])
            for finding in findings
            if id(finding) in required_ref_by_finding
        ]
    )
    group_by_representative_ref = {group.representative.finding_ref: group for group in candidate_groups}
    omitted_grouped_refs = {member.finding_ref for group in candidate_groups for member in group.members[1:]}
    prompt_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        finding_ref = required_ref_by_finding.get(id(finding)) or build_finding_ref(finding, index)
        if finding_ref in omitted_grouped_refs:
            continue
        group = group_by_representative_ref.get(finding_ref)
        related_finding_refs = [member.finding_ref for member in group.members] if group else [finding_ref]
        prompt_findings.append(
            {
                "finding_ref": finding_ref,
                "source_type": finding.source_type,
                "requires_candidate_review": id(finding) in required_finding_ids,
                "section_key": finding.section_key,
                "check_id": finding.check_id,
                "severity": finding.severity,
                "category": finding.category,
                "capability": finding.capability,
                "title": finding.title,
                "description": finding.description,
                "recommendation": finding.recommendation,
                "gate_recommendation": finding.gate_recommendation,
                "policy": {
                    "allow_semantic_downgrade": finding.metadata.get("allow_semantic_downgrade") is not False,
                },
                "evidence": sanitize_evidence_for_prompt(collect_group_evidence(group, finding)),
                "source_context": build_rule_finding_source_context(finding, semantic_context),
                "related_finding_refs": related_finding_refs,
                "occurrence_count": len(related_finding_refs),
                "omitted_occurrence_count": max(0, len(related_finding_refs) - 3),
            }
        )
    return prompt_findings


def sanitize_evidence_for_prompt(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in evidence[:3]:
        sanitized.append(
            {
                **item,
                "text": truncate_text(str(item.get("text", "")), 360),
            }
        )
    return sanitized


def collect_group_evidence(
    group: SemanticCandidateGroup | None,
    fallback_finding: ReviewFindingDraft,
) -> list[dict[str, Any]]:
    if not group:
        return fallback_finding.evidence
    evidence: list[dict[str, Any]] = []
    for member in group.members:
        evidence.extend(member.finding.evidence)
    return evidence[:3]


def line_number_sampled_file(sample: SemanticSample) -> dict[str, Any]:
    lines = sample.preview.splitlines()
    return {
        "path": sample.path,
        "truncated": sample.truncated,
        "content": "\n".join([f"[file {sample.path}]", *[f"{index + 1}: {line}" for index, line in enumerate(lines)]]),
    }


def annotate_observation_sampling(observations: dict[str, Any], semantic_context: SemanticContext) -> dict[str, Any]:
    sampled_paths = {normalize_path(sample.path) for sample in semantic_context.samples}
    return {
        **observations,
        "observations": [
            annotate_observation_evidence(observation, sampled_paths)
            for observation in observations.get("observations", [])
        ],
        "behavior_chains": [
            annotate_observation_evidence(chain, sampled_paths) for chain in observations.get("behavior_chains", [])
        ],
    }


def annotate_observation_evidence(item: dict[str, Any], sampled_paths: set[str]) -> dict[str, Any]:
    return {
        **item,
        "evidence": [
            {
                **evidence,
                "sampled": normalize_path(evidence.get("file", "")) in sampled_paths if evidence.get("file") else False,
            }
            for evidence in item.get("evidence", [])
        ],
    }


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max(0, max_length - 3)].rstrip()}..."


def normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").removeprefix("./").lower()
