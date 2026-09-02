// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

export const PRIMARY_SKILL_PLUGIN_TYPE = 'swarmskill'
export const SKILL_LIKE_PLUGIN_TYPES = ['skill', 'swarmskill'] as const
export const SKILL_LIKE_QUERY_VALUE = SKILL_LIKE_PLUGIN_TYPES.join(',')

export const AGENT_ASSET_PLUGIN_TYPES = ['agent-plugin', 'agent-template', 'agent-mcp'] as const
export const AGENT_ASSET_QUERY_VALUE = AGENT_ASSET_PLUGIN_TYPES.join(',')

/** 市场五个平级 Tab（与后端 MODERATED_MARKET_ASSET_TYPES 对齐，顺序即 Tab 顺序）。 */
export const MARKET_TAB_PLUGIN_TYPES = [
  ...SKILL_LIKE_PLUGIN_TYPES,
  ...AGENT_ASSET_PLUGIN_TYPES,
] as const

export const MODERATED_MARKET_QUERY_VALUE = MARKET_TAB_PLUGIN_TYPES.join(',')

export type SkillLikePluginType = (typeof SKILL_LIKE_PLUGIN_TYPES)[number]
export type AgentAssetPluginType = (typeof AGENT_ASSET_PLUGIN_TYPES)[number]
export type MarketTabPluginType = (typeof MARKET_TAB_PLUGIN_TYPES)[number]

const SKILL_LIKE_PLUGIN_TYPE_SET = new Set<string>(SKILL_LIKE_PLUGIN_TYPES)
const AGENT_ASSET_PLUGIN_TYPE_SET = new Set<string>(AGENT_ASSET_PLUGIN_TYPES)
const MARKET_TAB_PLUGIN_TYPE_SET = new Set<string>(MARKET_TAB_PLUGIN_TYPES)

export function normalizePluginType(value: string | null | undefined): string {
  const normalized = (value || '').trim().toLowerCase()
  return normalized === 'teamskills' ? 'swarmskill' : normalized
}

export function parseSkillLikePluginType(value: string | null | undefined): SkillLikePluginType | null {
  const normalized = normalizePluginType(value)
  return SKILL_LIKE_PLUGIN_TYPE_SET.has(normalized) ? (normalized as SkillLikePluginType) : null
}

export function parseAgentAssetPluginType(value: string | null | undefined): AgentAssetPluginType | null {
  const normalized = normalizePluginType(value)
  return AGENT_ASSET_PLUGIN_TYPE_SET.has(normalized) ? (normalized as AgentAssetPluginType) : null
}

export function parseMarketTabType(value: string | null | undefined): MarketTabPluginType | null {
  const normalized = normalizePluginType(value)
  return MARKET_TAB_PLUGIN_TYPE_SET.has(normalized) ? (normalized as MarketTabPluginType) : null
}

export function isSkillLikePluginType(value: string | null | undefined): value is SkillLikePluginType {
  return parseSkillLikePluginType(value) !== null
}

export function isAgentAssetPluginType(value: string | null | undefined): value is AgentAssetPluginType {
  return parseAgentAssetPluginType(value) !== null
}

export function isModeratedMarketAssetType(value: string | null | undefined): value is MarketTabPluginType {
  return parseMarketTabType(value) !== null
}

export function getSkillLikePluginTypes(): SkillLikePluginType[] {
  return [...SKILL_LIKE_PLUGIN_TYPES]
}

export function getAgentAssetPluginTypes(): AgentAssetPluginType[] {
  return [...AGENT_ASSET_PLUGIN_TYPES]
}

export function getMarketTabPluginTypes(): MarketTabPluginType[] {
  return [...MARKET_TAB_PLUGIN_TYPES]
}

export function getPrimarySkillPluginType(): SkillLikePluginType {
  return PRIMARY_SKILL_PLUGIN_TYPE
}

/** 详情页路由：Skill/SwarmSkill → /skills/:id；三类 Agent → /assets/:id。 */
export function assetDetailPath(assetId: string, pluginType?: string | null): string {
  const id = encodeURIComponent(assetId)
  if (isAgentAssetPluginType(pluginType)) {
    return `/assets/${id}`
  }
  return `/skills/${id}`
}
