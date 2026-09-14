# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retrieval configuration for the three wrapped Agent asset types."""

from __future__ import annotations

from dataclasses import dataclass

from plugins_market.validation.constants import (
    RUNTIME_AGENT_MCP,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_TEMPLATE,
)


@dataclass(frozen=True, slots=True)
class AgentRetrievalGroup:
    asset_type: str
    storage_root: str
    index_prefix: str
    tag_prefix: str

    @property
    def setting_suffix(self) -> str:
        return self.asset_type.replace("-", "_")


AGENT_RETRIEVAL_GROUPS = (
    AgentRetrievalGroup(RUNTIME_AGENT_PLUGIN, "agent-plugins", "agent-plugins-index", "agent-plugins-tag"),
    AgentRetrievalGroup(RUNTIME_AGENT_TEMPLATE, "agent-templates", "agent-templates-index", "agent-templates-tag"),
    AgentRetrievalGroup(RUNTIME_AGENT_MCP, "agent-mcps", "agent-mcps-index", "agent-mcps-tag"),
)

_AGENT_GROUP_BY_TYPE = {spec.asset_type: spec for spec in AGENT_RETRIEVAL_GROUPS}


def agent_retrieval_group(group: str | None) -> AgentRetrievalGroup | None:
    return _AGENT_GROUP_BY_TYPE.get((group or "").strip().lower())


def scanner_type_for_group(group: str) -> str:
    return "agent" if agent_retrieval_group(group) else group
