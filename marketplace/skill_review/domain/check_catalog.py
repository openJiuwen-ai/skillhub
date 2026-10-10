from __future__ import annotations

from typing import Any


def _check(check_id: str, name: str, target: str, scope: str, recommendation: str) -> dict[str, str]:
    return {
        "check_id": check_id,
        "name": name,
        "target": target,
        "scope": scope,
        "recommendation": recommendation,
    }


SECTION_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "auditability",
        "title": "包规范与可审计性",
        "passed_summary": "Skill 包具备基础可审计性，关键说明与结构可被复查。",
        "attention_summary": "Skill 包存在可审计性不足，建议补充说明后再发布。",
        "failed_summary": "Skill 包缺少必要说明或结构，无法完成可靠审查。",
        "recommendation": "请补齐主说明、资源清单、环境要求和副作用说明。",
        "checks": [
            _check(
                "main_doc_presence",
                "主说明文件检查",
                "检查 Skill 包是否包含完整主说明文件。",
                "SKILL.md、skill.md 与主文档元信息。",
                "请在包根目录补充清晰的 SKILL.md。",
            ),
            _check(
                "package_structure_integrity",
                "包结构完整性检查",
                "检查目录结构、关键文件与资源引用是否完整。",
                "压缩包文件列表、资源目录和结构性告警。",
                "请补齐缺失目录或修正异常文件组织方式。",
            ),
            _check(
                "resource_manifest_clarity",
                "资源清单清晰度检查",
                "检查脚本、模板、知识文件和资源是否被声明。",
                "SKILL.md、README、manifest 与资源目录。",
                "请列明 Skill 会读取或依赖的资源文件。",
            ),
            _check(
                "environment_requirements_clarity",
                "环境要求说明检查",
                "检查运行环境、依赖和前置条件是否明确。",
                "依赖说明、环境变量、安装步骤和运行前置。",
                "请补充环境要求、依赖版本和必要配置说明。",
            ),
            _check(
                "side_effect_disclosure",
                "副作用披露检查",
                "检查写入、联网、持久化等副作用是否提前说明。",
                "Skill 文档、脚本说明和操作步骤。",
                "请明确披露所有高影响副作用。",
            ),
            _check(
                "script_usage_documentation",
                "脚本使用说明检查",
                "检查脚本用途、输入输出和权限边界是否可审计。",
                "脚本文件、README 和相关使用说明。",
                "请为脚本补充用途、参数、输出和权限边界说明。",
            ),
        ],
    },
    {
        "key": "instruction",
        "title": "指令与上下文安全",
        "passed_summary": "未发现阻断性的上下文操纵或提示词泄露风险。",
        "attention_summary": "发现指令或上下文安全需关注项，建议确认提示词边界。",
        "failed_summary": "发现阻断性的指令覆盖、注入或提示词泄露风险。",
        "recommendation": "请移除覆盖系统约束、泄露提示词或隐藏恶意意图的内容。",
        "checks": [
            _check(
                "direct_override_or_jailbreak",
                "直接越权与越狱检查",
                "识别显式覆盖上文、忽略约束或绕过安全边界的指令。",
                "Skill 文档、模板、示例和提示词资源。",
                "请删除覆盖系统或开发者指令的内容。",
            ),
            _check(
                "hidden_instruction_carrier",
                "隐藏指令载体检查",
                "识别 HTML、Markdown、注释、编码和不可见字符中的隐藏指令。",
                "文本资源、Markdown、HTML、脚本注释和编码片段。",
                "请移除隐藏指令和混淆载体。",
            ),
            _check(
                "indirect_injection_carrier",
                "间接注入载体检查",
                "识别通过引用文档、脚本注释或知识文件传递的间接注入。",
                "被 Skill 引用或延迟加载的资源文件。",
                "请清理间接注入内容并限制资源信任边界。",
            ),
            _check(
                "prompt_or_system_leakage",
                "提示词泄露检查",
                "识别索要系统提示词、隐藏规则或开发者消息的表达。",
                "Skill 指令、示例对话和提示模板。",
                "请删除提示词泄露相关表达。",
            ),
            _check(
                "instruction_priority_conflict",
                "指令优先级冲突检查",
                "识别 Skill 指令与平台或系统级约束之间的冲突。",
                "Skill 主说明、模板和使用步骤。",
                "请调整 Skill 指令，避免声明高于平台安全约束的权限。",
            ),
            _check(
                "unsafe_context_delegation",
                "上下文安全委托检查",
                "识别把权限、安全控制或秘密保护错误委托给 prompt 的做法。",
                "权限说明、安全承诺和运行边界。",
                "请把安全控制迁移到代码或平台侧，不要仅依赖提示词。",
            ),
        ],
    },
    {
        "key": "data_asset",
        "title": "知识资产与敏感信息安全",
        "passed_summary": "未发现阻断性的敏感凭证或知识资产暴露风险。",
        "attention_summary": "发现知识资产或敏感信息相关需关注项，建议确认发布边界。",
        "failed_summary": "发现阻断性的敏感信息、默认凭证或内部资料暴露风险。",
        "recommendation": "请移除明文凭证、内部资料和不应随 Skill 发布的知识文件。",
        "checks": [
            _check(
                "hardcoded_secret_material",
                "明文密钥检查",
                "识别私钥、API Key、Token、密码和连接串。",
                "Skill 文档、示例代码、脚本和配置。",
                "请移除明文凭证，并使用运行时安全注入。",
            ),
            _check(
                "default_credentials_or_backdoor",
                "默认凭证与后门检查",
                "识别默认账号、默认密码、后门口令和调试入口。",
                "账号口令、默认凭证和访问说明。",
                "请删除默认凭证或后门账号说明。",
            ),
            _check(
                "internal_material_exposure",
                "内部资料暴露检查",
                "识别不应发布的内部流程、客户信息和私有资料。",
                "知识文件、资源目录、示例和说明文档。",
                "请移除内部资料或改为受控运行时访问。",
            ),
            _check(
                "knowledge_file_leakage_risk",
                "知识文件泄露风险检查",
                "识别知识文件可被诱导输出或外泄的风险。",
                "知识文件、附件、检索资源和引用说明。",
                "请降低知识文件敏感度并补充访问边界。",
            ),
            _check(
                "metadata_overexposure",
                "元数据过度暴露检查",
                "识别文件名、路径、资源描述中的敏感元数据。",
                "文件清单、资源路径和说明。",
                "请清理敏感路径、客户名和内部系统名称。",
            ),
            _check(
                "license_or_copyright_risk",
                "版权与受限资料检查",
                "识别可疑版权材料、受限文件和来源不明资产。",
                "资源文件、知识文件、许可证和来源说明。",
                "请补充授权来源或移除受限材料。",
            ),
        ],
    },
    {
        "key": "execution",
        "title": "执行与副作用边界",
        "passed_summary": "未发现阻断性的执行、联网、写入或权限边界风险。",
        "attention_summary": "发现执行或副作用相关需关注项，建议确认风险边界。",
        "failed_summary": "发现阻断性的执行、联网、写入或权限边界风险。",
        "recommendation": "请移除无关执行链路，或明确收紧执行范围和用户确认边界。",
        "checks": [
            _check(
                "dynamic_or_shell_execution",
                "动态执行与 Shell 检查",
                "识别 eval、shell、子进程和动态执行入口。",
                "Skill 文档、脚本和命令片段。",
                "请移除无关动态执行入口，或改为明确的用户确认步骤。",
            ),
            _check(
                "destructive_operation",
                "破坏性操作检查",
                "识别删除、覆盖、清空、回滚和强制重置动作。",
                "删除、清理、回滚与批量清空命令。",
                "请移除不可逆动作，或增加明确确认与作用域限制。",
            ),
            _check(
                "external_network_io",
                "外部网络输入输出检查",
                "识别下载、上传、远程同步和外部端点通信。",
                "公网网络访问、文件传输和外部服务调用。",
                "请删除未授权外部端点访问，或说明必要性和数据边界。",
            ),
            _check(
                "local_runtime_attachment",
                "本地运行时连接检查",
                "识别默认探测、连接或附着到本地调试端口、本地 MCP、桌面应用或本地运行时服务的行为。",
                "localhost、127.0.0.1、本地端口、本地 MCP、CDP 和桌面应用桥接。",
                "请明确本地运行时前置条件、连接目标和用户确认边界。",
            ),
            _check(
                "remote_script_or_binary_fetch",
                "远程脚本与二进制获取检查",
                "识别远程脚本拉取后执行和二进制下载。",
                "下载命令、安装步骤和脚本执行链。",
                "请移除下载后执行链，或固定来源并补充完整性校验。",
            ),
            _check(
                "repository_boundary_change",
                "仓库边界变更检查",
                "识别修改 Git 远端、强推或跨仓库写入。",
                "Git 远端、提交边界和全局配置相关命令。",
                "请移除跨仓库或强制推送行为。",
            ),
            _check(
                "permission_weakening",
                "权限弱化检查",
                "识别关闭校验、放宽权限或跳过安全检查。",
                "权限、TLS/SSH 校验和安全策略配置。",
                "请恢复必要权限控制和安全校验。",
            ),
            _check(
                "persistent_side_effect",
                "持久化副作用检查",
                "识别修改全局配置、写入持久目录或后台持续任务。",
                "全局配置、持久目录、后台进程和定时任务。",
                "请避免默认持久化副作用，或要求用户显式确认。",
            ),
            _check(
                "resource_exhaustion_or_dos",
                "资源耗尽与拒绝服务检查",
                "检查 Skill 是否包含可能导致 CPU、内存、磁盘或执行时长失控的无界行为。",
                "循环、递归、批量计算、持续写入、放大量执行链和相关脚本说明。",
                "请限制循环、分配、写入和高成本计算的边界，并避免默认触发无界执行。",
            ),
            _check(
                "filesystem_boundary_violation",
                "文件系统边界突破检查",
                "检查 Skill 是否存在越界路径访问、路径遍历或包外文件读写风险。",
                "路径拼接、相对路径、绝对路径、文件读写逻辑和脚本命令。",
                "请限制文件访问范围，避免基于不可信路径访问包外或工作目录外资源。",
            ),
            _check(
                "unsafe_state_deserialization_or_loading",
                "不安全反序列化与加载检查",
                "检查 Skill 是否会把不可信输入恢复为对象、状态或可执行结构。",
                "反序列化 API、状态恢复、配置加载、对象恢复和相关脚本逻辑。",
                "请改用安全加载方式，并限制可恢复内容只来自受控、可信的数据源。",
            ),
            _check(
                "unsafe_input_interpretation_or_injection",
                "不安全输入解释与注入检查",
                "检查 Skill 是否把不可信输入解释为查询、模板或 XML 等高风险结构。",
                "查询构造、模板渲染、XML 解析、解释链和相关代码或文档。",
                "请避免将不可信输入直接参与查询、模板或 XML 解释，并补充必要隔离与约束。",
            ),
        ],
    },
    {
        "key": "authorization",
        "title": "目标一致性与用户授权边界",
        "passed_summary": "未发现阻断性的目标不一致或授权边界缺失。",
        "attention_summary": "发现目标一致性或用户授权边界需关注项。",
        "failed_summary": "发现阻断性的目标不一致、越界动作或授权缺失。",
        "recommendation": "请确保 Skill 行为与声明目标一致，并为高影响动作补充用户确认。",
        "checks": [
            _check(
                "skill_goal_vs_actual_action_mismatch",
                "目标与实际行为一致性检查",
                "识别声明目标与实际动作不一致的情况。",
                "Skill 主说明、脚本、模板和资源引用。",
                "请删除与 Skill 目标无关的动作或重写能力声明。",
            ),
            _check(
                "missing_user_consent_boundary",
                "用户确认边界检查",
                "识别高影响动作缺少显式用户确认的情况。",
                "写入、联网、上传、删除和持久化动作。",
                "请为高影响动作补充明确确认步骤。",
            ),
            _check(
                "overbroad_default_behavior",
                "默认行为过宽检查",
                "识别默认联网、默认上传、默认写入和默认持久化。",
                "默认步骤、自动化流程和脚本入口。",
                "请改为按需执行，并说明默认行为边界。",
            ),
            _check(
                "unnecessary_data_collection",
                "非必要数据收集检查",
                "识别与主任务无关的数据或凭据收集。",
                "输入要求、表单、脚本参数和上传流程。",
                "请删除非必要数据收集。",
            ),
            _check(
                "hidden_followup_action",
                "隐藏后续动作检查",
                "识别任务完成后附带执行的额外动作。",
                "完成步骤、清理步骤和后置脚本。",
                "请移除隐藏后续动作，或显式声明并要求确认。",
            ),
            _check(
                "semantic_behavioral_inconsistency",
                "语义行为不一致检查",
                "识别说明文档、脚本和模板之间的行为冲突。",
                "Skill 主说明、脚本、模板和资源引用。",
                "请统一文档与实际行为。",
            ),
            _check(
                "abusive_automation_or_service_boundary",
                "滥用自动化与服务边界检查",
                "检查 Skill 是否默认执行过宽自动化、批量请求或规避服务限制的行为。",
                "并发请求、重试放大、批处理流程、外部接口调用和自动化说明。",
                "请收紧自动化范围，避免默认批量操作、爆量请求或规避服务限制的行为。",
            ),
        ],
    },
    {
        "key": "supply_chain",
        "title": "来源可信与供应链",
        "passed_summary": "未发现阻断性的来源、依赖或供应链风险。",
        "attention_summary": "发现来源可信或供应链相关需关注项。",
        "failed_summary": "发现阻断性的来源、依赖或供应链风险。",
        "recommendation": "请针对已声明或已发现的外部依赖、远程资源和第三方材料补充来源、版本、完整性或许可证说明。",
        "checks": [
            _check(
                "provenance_missing",
                "来源信息缺失检查",
                "仅在包内已有外部来源、第三方资源或远程依赖证据时，检查来源和版本信息是否明确。",
                "元信息、README、manifest、发布说明和外部资源引用。",
                "请为已引用的外部来源补充作者、来源和版本信息。",
            ),
            _check(
                "typosquat_or_impersonation_risk",
                "名称仿冒风险检查",
                "识别名称仿冒、描述误导和来源伪装风险。",
                "Skill 名称、描述、标签和来源信息。",
                "请避免仿冒知名 Skill 或误导性命名。",
            ),
            _check(
                "untrusted_remote_dependency",
                "不可信远程依赖检查",
                "识别不可信外部依赖和远程资源。",
                "依赖声明、下载链接和远程资源引用。",
                "请删除不可信远程依赖或补充可信来源。",
            ),
            _check(
                "integrity_or_pinning_missing",
                "版本固定与完整性检查",
                "仅对已声明依赖、下载资源和安装步骤识别版本未固定、校验缺失和完整性信息不足。",
                "依赖版本、下载资源和安装步骤。",
                "请为已使用的外部依赖固定版本，并按风险补充哈希或签名校验。",
            ),
            _check(
                "stale_or_unmaintained_asset",
                "过期资产检查",
                "识别长期未维护或来源不可验证的资产。",
                "外部资源、依赖和版本说明。",
                "请更新依赖或说明维护状态。",
            ),
            _check(
                "license_unknown_or_conflicting",
                "许可证冲突检查",
                "仅在包内包含第三方内容、许可证材料或明确复用外部资产时，识别许可证未知或与使用场景冲突的资产。",
                "许可证、资源来源和第三方内容。",
                "请为已复用的第三方内容补充许可证或替换冲突资产。",
            ),
        ],
    },
]


CHECK_BY_ID = {check["check_id"]: check for section in SECTION_DEFINITIONS for check in section["checks"]}


def list_review_sections() -> list[dict[str, Any]]:
    return SECTION_DEFINITIONS


def get_review_check(check_id: str) -> dict[str, str]:
    return CHECK_BY_ID[check_id]
