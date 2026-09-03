# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""市场上架审核：状态常量、类型集合与可见性判断。"""

from __future__ import annotations

from plugins_market.validation.constants import (
    RUNTIME_AGENT_MCP,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_TEMPLATE,
)

# 主表 market_assets.moderation_status
MODERATION_PENDING = "PENDING"
MODERATION_APPROVED = "APPROVED"
MODERATION_REJECTED = "REJECTED"

# 与 CLI ``SKILL_LIKE_RUNTIME_TYPES`` 对齐：Skill / SwarmSkill。
SKILL_LIKE_PLUGIN_TYPES = frozenset({"skill", "swarmskill"})

# 包装型 Agent 资产（与 validation.constants RUNTIME_AGENT_* 对齐）。
AGENT_ASSET_PLUGIN_TYPES = frozenset(
    {
        RUNTIME_AGENT_PLUGIN,
        RUNTIME_AGENT_TEMPLATE,
        RUNTIME_AGENT_MCP,
    }
)

# 走审核 / 公开可见性聚合的市场资产类型 = skill-like ∪ agent 三类。
# 不含 tools / mcp-stdio / restful-api（历史插件，仍按「非 moderated」直通）。
MODERATED_MARKET_ASSET_TYPES = SKILL_LIKE_PLUGIN_TYPES | AGENT_ASSET_PLUGIN_TYPES


def normalize_skill_like_plugin_type(plugin_type: str | None) -> str:
    normalized = (plugin_type or "").strip().lower()
    return "swarmskill" if normalized == "teamskills" else normalized


def normalize_market_plugin_type(plugin_type: str | None) -> str:
    """统一小写；teamskills → swarmskill。Agent 类型原样保留。"""
    return normalize_skill_like_plugin_type(plugin_type)


def is_skill_like_plugin_type(plugin_type: str | None) -> bool:
    return normalize_market_plugin_type(plugin_type) in SKILL_LIKE_PLUGIN_TYPES


def is_wrapped_agent_asset_type(plugin_type: str | None) -> bool:
    return normalize_market_plugin_type(plugin_type) in AGENT_ASSET_PLUGIN_TYPES


def is_moderated_market_asset_type(plugin_type: str | None) -> bool:
    """是否参与上架审核与公开可见性闸门（Skill/SwarmSkill + 三类 Agent）。"""
    return normalize_market_plugin_type(plugin_type) in MODERATED_MARKET_ASSET_TYPES


def moderated_asset_type_label(plugin_type: str | None) -> str:
    """审核/错误文案中的资产类型中文简称。"""
    labels = {
        "skill": "Skill",
        "swarmskill": "SwarmSkill",
        RUNTIME_AGENT_PLUGIN: "Agent 插件",
        RUNTIME_AGENT_TEMPLATE: "Agent 模版",
        RUNTIME_AGENT_MCP: "Agent 连接器",
    }
    return labels.get(normalize_market_plugin_type(plugin_type), "市场资产")


def moderation_coalesce_display(status: str | None) -> str:
    """空值视为已通过（兼容旧数据）。"""
    s = (status or "").strip()
    return s if s else MODERATION_APPROVED


def is_skill_moderation_publicly_visible(status: str | None) -> bool:
    return moderation_coalesce_display(status) == MODERATION_APPROVED
