// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { ReactNode } from 'react'

/** 既能兼容列表项也能兼容详情，凡涉及"操作对象"列的字段子集。 */
interface ObjectLike {
  resource_type?: string | null
  resource_id?: string | null
  asset_plugin_type?: string | null
  asset_name?: string | null
  asset_display_name?: string | null
  extra?: Record<string, unknown> | null
  /** 审计操作类型，用于决定是否允许点击跳转 */
  action?: string | null
}

/**
 * 事件类型 → 中文 + 配色
 * 未知事件类型 fallback 为灰色 + 原始 key。
 */
const EVENT_TYPE_META: Record<string, { label: string; className: string }> = {
  SKILL_MODERATION: { label: 'Skill 审核', className: 'bg-blue-50 text-blue-700' },
  SKILL_MANAGE: { label: 'Skill 管理', className: 'bg-purple-50 text-purple-700' },
  PLUGIN_MANAGE: { label: '插件管理', className: 'bg-orange-50 text-orange-700' },
  SKILL_REVIEW: { label: '系统审查', className: 'bg-indigo-50 text-indigo-700' },
  SKILL_USE: { label: 'Skill 使用', className: 'bg-teal-50 text-teal-700' },
  AUDIT: { label: '审计自身', className: 'bg-amber-50 text-amber-800' },
  UNKNOWN: { label: '未识别', className: 'bg-slate-100 text-slate-500' },
}

const ACTION_META: Record<string, { label: string; className: string }> = {
  APPROVE: { label: '审核通过', className: 'bg-emerald-50 text-emerald-800' },
  REJECT: { label: '驳回', className: 'bg-rose-50 text-rose-800' },
  PUBLISH: { label: '发布', className: 'bg-blue-50 text-blue-700' },
  DELETE: { label: '删除', className: 'bg-rose-50 text-rose-800' },
  GIT_SYNC: { label: 'Git 同步', className: 'bg-cyan-50 text-cyan-800' },
  GIT_SOURCE_DELETE: { label: '删除 Git 源', className: 'bg-orange-50 text-orange-800' },
  IMPORT: { label: '批量导入', className: 'bg-purple-50 text-purple-700' },
  // 系统审查相关
  AUTO_REVIEW_PASS: { label: '审查通过', className: 'bg-emerald-50 text-emerald-800' },
  AUTO_REVIEW_FAIL: { label: '审查未通过', className: 'bg-rose-50 text-rose-800' },
  AUTO_REVIEW_SYS_FAIL: { label: '审查异常', className: 'bg-amber-50 text-amber-800' },
  PENDING_MOD_SET: { label: '转入待审', className: 'bg-sky-50 text-sky-800' },
  // 失败补录中可能出现的兜底
  MODERATE: { label: '审核操作', className: 'bg-slate-100 text-slate-700' },
  // 审计自身相关
  EXPORT: { label: '导出', className: 'bg-amber-50 text-amber-800' },
  // Skill 使用相关
  DOWNLOAD: { label: '下载', className: 'bg-teal-50 text-teal-700' },
}

function BaseBadge({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${className}`}
    >
      {children}
    </span>
  )
}

export function AuditEventTypeBadge({ value }: { value: string }) {
  const meta = EVENT_TYPE_META[value] || { label: value, className: 'bg-slate-100 text-slate-700' }
  return <BaseBadge className={meta.className}>{meta.label}</BaseBadge>
}

export function AuditActionBadge({ value }: { value: string }) {
  const meta = ACTION_META[value] || { label: value, className: 'bg-slate-100 text-slate-700' }
  return <BaseBadge className={meta.className}>{meta.label}</BaseBadge>
}

export function AuditResultBadge({ value }: { value: string }) {
  if (value === 'SUCCESS') {
    return <BaseBadge className="bg-emerald-50 text-emerald-800">✅ 成功</BaseBadge>
  }
  if (value === 'PARTIAL_FAILED') {
    return <BaseBadge className="bg-amber-50 text-amber-800">部分失败</BaseBadge>
  }
  return <BaseBadge className="bg-rose-50 text-rose-800">❌ 失败</BaseBadge>
}

const SOURCE_CHANNEL_META: Record<string, { label: string; className: string }> = {
  web: { label: 'Web', className: 'bg-blue-50 text-blue-700' },
  cli: { label: 'CLI', className: 'bg-purple-50 text-purple-700' },
  api: { label: 'API', className: 'bg-slate-100 text-slate-700' },
  background: { label: '后台', className: 'bg-cyan-50 text-cyan-800' },
}

export function AuditSourceChannelBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-xs text-[#9CA3AF]">—</span>
  const meta = SOURCE_CHANNEL_META[value] || { label: value, className: 'bg-slate-100 text-slate-700' }
  return <BaseBadge className={meta.className}>{meta.label}</BaseBadge>
}

export function getEventTypeLabel(value: string): string {
  return EVENT_TYPE_META[value]?.label || value
}

const AGENT_ASSET_RESOURCE_TYPES = new Set(['agent-plugin', 'agent-template', 'agent-mcp'])
const MARKET_ASSET_RESOURCE_TYPES = new Set(['skill', 'swarmskill', 'plugin'])

/** 把一条审计记录归到对应的操作对象形态，名称列 / 类型 badge 都由此分支。 */
export type AuditObjectKind =
  | 'skill'
  | 'agent_asset'
  | 'audit_log'
  | 'git_source'
  | 'skill_bundle'
  | 'unknown'

export function getObjectKind(item: ObjectLike): AuditObjectKind {
  const rt = item.resource_type
  if (rt === 'audit_log') return 'audit_log'
  if (rt === 'git_source') return 'git_source'
  if (rt === 'skill_bundle') return 'skill_bundle'
  if (rt && AGENT_ASSET_RESOURCE_TYPES.has(rt)) return 'agent_asset'
  if (rt && MARKET_ASSET_RESOURCE_TYPES.has(rt)) return 'skill'
  if (item.asset_plugin_type) return 'skill'
  return 'unknown'
}

export interface ObjectDisplay {
  text: string
  /** 非 Skill 类 / 缺信息的兜底——列里用灰色斜体渲染 */
  isPlaceholder: boolean
  /** Skill / Agent 详情可点击跳转；其他形态恒为 false */
  clickable: boolean
  /** 跳转详情用的 asset_id */
  slug: string | null
  /** 用于 /skills vs /assets 分流 */
  pluginType?: string | null
  /** hover 提示，可选 */
  title?: string
}

/** 计算"操作对象"列展示内容。优先级保持与原 pickSkillName 一致，新增非 Skill 分支与早拒兜底。 */
export function pickObjectDisplay(item: ObjectLike): ObjectDisplay {
  const kind = getObjectKind(item)
  const extra = (item.extra ?? {}) as Record<string, unknown>

  if (kind === 'audit_log') {
    return { text: '审计日志', isPlaceholder: true, clickable: false, slug: null }
  }
  if (kind === 'git_source') {
    const gitName = String(extra.git_source_name || '').trim()
    const repoUrl = String(extra.repo_url || '').trim()
    if (gitName) {
      return {
        text: gitName,
        isPlaceholder: false,
        clickable: false,
        slug: null,
        title: repoUrl || item.resource_id || undefined,
      }
    }
    const gid = item.resource_id ? `${item.resource_id.slice(0, 8)}…` : '未知'
    return {
      text: `Git 源 ${gid}`,
      isPlaceholder: true,
      clickable: false,
      slug: null,
      title: item.resource_id || undefined,
    }
  }
  if (kind === 'skill_bundle') {
    const filename = String(extra.upload_filename || '').trim()
    return {
      text: filename ? `导入包 ${filename}` : 'Skill 包导入',
      isPlaceholder: true,
      clickable: false,
      slug: null,
    }
  }

  // 市场资产 / unknown：跟原 pickSkillName 同优先级
  const fromExtraDisplay = String(extra.skill_display_name || '').trim()
  const extraSlug = String(extra.skill_name || '').trim()
  // 优先用 resource_id (UUID) 当跳转 slug：详情路由需要后端 asset_id (UUID)，
  // asset_name 是人类可读名，无法路由命中。仅当资源不存在或动作是"删除全部版本"时不可点。
  const isDeleteAll = String(item.action || '').trim().toUpperCase() === 'DELETE'
  const bestSlug = item.resource_id || extraSlug || item.asset_name || null
  const pluginType = item.asset_plugin_type || item.resource_type || null
  const clickable = (kind === 'skill' || kind === 'agent_asset') && Boolean(bestSlug) && !isDeleteAll
  if (fromExtraDisplay) {
    return { text: fromExtraDisplay, isPlaceholder: false, clickable, slug: bestSlug, pluginType }
  }
  if (item.asset_display_name) {
    return { text: item.asset_display_name, isPlaceholder: false, clickable, slug: bestSlug, pluginType }
  }
  if (extraSlug) {
    return { text: extraSlug, isPlaceholder: false, clickable, slug: bestSlug, pluginType }
  }
  if (item.asset_name) {
    return { text: item.asset_name, isPlaceholder: false, clickable, slug: bestSlug, pluginType }
  }
  if (item.resource_id) {
    return { text: item.resource_id, isPlaceholder: false, clickable, slug: bestSlug, pluginType }
  }

  // 早拒兜底：连资源标识都没有
  const uploadFilename = String(extra.upload_filename || '').trim()
  if (uploadFilename) {
    return {
      text: `文件 ${uploadFilename}`,
      isPlaceholder: true,
      clickable: false,
      slug: null,
      title: '请求在识别出具体 Skill 之前已被拒绝',
    }
  }
  return {
    text: '无对象信息',
    isPlaceholder: true,
    clickable: false,
    slug: null,
    title: '请求被早期拒绝，未识别出操作对象',
  }
}

const OBJECT_TYPE_LABEL: Record<string, string> = {
  skill: 'skill',
  swarmskill: 'swarmskill',
  plugin: '插件',
  'agent-plugin': 'agent-plugin',
  'agent-template': 'agent-template',
  'agent-mcp': 'agent-mcp',
  audit_log: '日志',
  git_source: 'Git 源',
  skill_bundle: 'Skill 包',
  unknown: '未识别',
}

/** "对象类型"列文本：市场资产优先使用 asset_plugin_type 保留精确分类。 */
export function getObjectTypeLabel(item: ObjectLike): string {
  const kind = getObjectKind(item)
  if (kind === 'skill' || kind === 'agent_asset') {
    const sub = item.asset_plugin_type
    if (sub && OBJECT_TYPE_LABEL[sub]) return OBJECT_TYPE_LABEL[sub]
    if (sub) return sub
    if (
      item.resource_type &&
      (MARKET_ASSET_RESOURCE_TYPES.has(item.resource_type) ||
        AGENT_ASSET_RESOURCE_TYPES.has(item.resource_type))
    ) {
      return OBJECT_TYPE_LABEL[item.resource_type] || item.resource_type
    }
    return OBJECT_TYPE_LABEL.skill
  }
  return OBJECT_TYPE_LABEL[kind] || kind
}
