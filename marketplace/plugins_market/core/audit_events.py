# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""审计事件类型集中定义。

所有 ``event_type`` / ``action`` / ``result`` / ``resource_type`` 字符串
在这里定义一次，其它模块全部 import 使用，避免散落硬编码。

不引入新事件类型——本模块是把现状集中，便于未来重命名 / 扩展时只改一处。

约定：
* 字段值与数据库写入一致（保留大写枚举形态）
* ``resource_type`` 保留小写（与代码现有习惯一致）
* 提供 ``ALL_*`` tuple 便于校验和迭代
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────────────
# event_type
# ──────────────────────────────────────────────────────────────────────────────


class EventType:
    """审计事件大类。"""

    SKILL_MANAGE = "SKILL_MANAGE"
    PLUGIN_MANAGE = "PLUGIN_MANAGE"
    SKILL_MODERATION = "SKILL_MODERATION"
    SKILL_REVIEW = "SKILL_REVIEW"
    SKILL_USE = "SKILL_USE"
    AUDIT = "AUDIT"
    # UNKNOWN 是失败补录无法识别接口时的兜底，不算正式业务事件
    UNKNOWN = "UNKNOWN"


ALL_EVENT_TYPES: tuple[str, ...] = (
    EventType.SKILL_MANAGE,
    EventType.PLUGIN_MANAGE,
    EventType.SKILL_MODERATION,
    EventType.SKILL_REVIEW,
    EventType.SKILL_USE,
    EventType.AUDIT,
)


# 旧代码用过的别名（避免破坏现有 import 路径）
EVENT_SKILL_MODERATION = EventType.SKILL_MODERATION


# ──────────────────────────────────────────────────────────────────────────────
# action
# ──────────────────────────────────────────────────────────────────────────────


class Action:
    """具体操作动作。"""

    PUBLISH = "PUBLISH"
    DELETE = "DELETE"
    IMPORT = "IMPORT"
    GIT_SYNC = "GIT_SYNC"
    GIT_SOURCE_DELETE = "GIT_SOURCE_DELETE"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    # MODERATE 仅用于失败补录：无法预知是 APPROVE 还是 REJECT 时的兜底
    MODERATE = "MODERATE"
    AUTO_REVIEW_PASS = "AUTO_REVIEW_PASS"
    AUTO_REVIEW_FAIL = "AUTO_REVIEW_FAIL"
    AUTO_REVIEW_SYS_FAIL = "AUTO_REVIEW_SYS_FAIL"
    PENDING_MOD_SET = "PENDING_MOD_SET"
    EXPORT = "EXPORT"
    DOWNLOAD = "DOWNLOAD"


ALL_ACTIONS: tuple[str, ...] = (
    Action.PUBLISH,
    Action.DELETE,
    Action.IMPORT,
    Action.GIT_SYNC,
    Action.GIT_SOURCE_DELETE,
    Action.APPROVE,
    Action.REJECT,
    Action.MODERATE,
    Action.AUTO_REVIEW_PASS,
    Action.AUTO_REVIEW_FAIL,
    Action.AUTO_REVIEW_SYS_FAIL,
    Action.PENDING_MOD_SET,
    Action.EXPORT,
    Action.DOWNLOAD,
)


# ──────────────────────────────────────────────────────────────────────────────
# result
# ──────────────────────────────────────────────────────────────────────────────


class Result:
    """操作结果。"""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL_FAILED = "PARTIAL_FAILED"


ALL_RESULTS: tuple[str, ...] = (Result.SUCCESS, Result.FAILED, Result.PARTIAL_FAILED)


def resolve_batch_audit_result(
    *,
    ok_count: int,
    failed_count: int,
    skipped_count: int = 0,
) -> str:
    """批量任务审计结果，与 git_skill_sync.last_index_status 口径对齐。

    失败数=0 → 成功；有失败且仍有成功或跳过 → 部分失败；全部失败 → 失败。
    """
    if failed_count <= 0:
        return Result.SUCCESS
    if ok_count > 0 or skipped_count > 0:
        return Result.PARTIAL_FAILED
    return Result.FAILED


# ──────────────────────────────────────────────────────────────────────────────
# resource_type
# ──────────────────────────────────────────────────────────────────────────────


class ResourceType:
    """资源类型（小写）。"""

    SKILL = "skill"
    SWARMSKILL = "swarmskill"
    PLUGIN = "plugin"
    AGENT_PLUGIN = "agent-plugin"
    AGENT_TEMPLATE = "agent-template"
    AGENT_GROUP = "agent-group"
    AGENT_MCP = "agent-mcp"
    GIT_SOURCE = "git_source"
    SKILL_BUNDLE = "skill_bundle"
    AUDIT_LOG = "audit_log"


# 指向 market_assets 的资源类型——用于审计列表/详情时 JOIN 补 display_name
ASSET_LINKED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        ResourceType.SKILL,
        ResourceType.SWARMSKILL,
        ResourceType.PLUGIN,
        ResourceType.AGENT_PLUGIN,
        ResourceType.AGENT_TEMPLATE,
        ResourceType.AGENT_GROUP,
        ResourceType.AGENT_MCP,
    }
)
