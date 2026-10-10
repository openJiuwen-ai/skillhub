// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useState } from 'react'
import { useQuery } from 'react-query'
import { Check, ChevronRight, Copy, X as XIcon } from 'lucide-react'
import { getAuditLogDetail, type AuditLogDetail } from '@/api/audit'
import { assetDetailPath } from '@/utils/pluginType'
import { getObjectTypeLabel, pickObjectDisplay } from './badges'

interface AuditLogDetailDrawerProps {
  eventId: string | null
  onClose: () => void
}

function formatFullDateTime(ms: number): string {
  const d = new Date(ms)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${y}/${m}/${day} ${hh}:${mm}:${ss}`
}

function formatVersion(version?: string | null): string {
  if (!version) return '—'
  return version.toLowerCase() === 'all' ? '全部版本' : version
}

/** label/value 行：label 固定宽度灰色，value 多行可换行。 */
function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-6 py-2">
      <div className="w-20 shrink-0 text-xs text-[#9CA3AF]">{label}</div>
      <div className="min-w-0 flex-1 break-words text-[13px] text-[#111827]">{children}</div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-1.5 text-sm font-semibold text-[#111827]">{title}</h2>
      <div className="divide-y divide-[#F3F4F6]">{children}</div>
    </section>
  )
}

/**
 * Copy text to clipboard with a fallback for non-secure contexts.
 * `navigator.clipboard` requires HTTPS or localhost (Secure Context). When the
 * page is served over plain http on a custom host (e.g. http://skillhub.local),
 * `navigator.clipboard` is undefined and `writeText` throws; fall back to the
 * legacy `document.execCommand('copy')` approach via a hidden textarea.
 */
async function copyTextToClipboard(value: string): Promise<boolean> {
  // Prefer the async Clipboard API when available in a secure context.
  if (typeof window !== 'undefined' && window.isSecureContext && navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // fall through to legacy path
    }
  }
  // Legacy fallback: hidden textarea + execCommand('copy').
  if (typeof document === 'undefined') return false
  const ta = document.createElement('textarea')
  ta.value = value
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.width = '1px'
  ta.style.height = '1px'
  ta.style.padding = '0'
  ta.style.border = 'none'
  ta.style.outline = 'none'
  ta.style.boxShadow = 'none'
  ta.style.background = 'transparent'
  document.body.appendChild(ta)
  try {
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, value.length)
    const ok = document.execCommand('copy')
    return ok
  } catch {
    return false
  } finally {
    document.body.removeChild(ta)
  }
}

function MonoCopy({ value }: { value?: string | null }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  if (!value) return <span className="text-[#9CA3AF]">—</span>
  const handleCopy = async () => {
    const ok = await copyTextToClipboard(value)
    setState(ok ? 'copied' : 'failed')
    window.setTimeout(() => setState('idle'), 1500)
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="break-all font-mono text-xs text-[#374151]">{value}</span>
      <button
        type="button"
        onClick={handleCopy}
        aria-label={state === 'failed' ? '复制失败' : '复制'}
        title={state === 'failed' ? '复制失败' : '复制'}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[#9CA3AF] hover:bg-slate-100 hover:text-[#374151]"
      >
        {state === 'copied' ? (
          <Check className="h-3 w-3 text-emerald-600" />
        ) : state === 'failed' ? (
          <XIcon className="h-3 w-3 text-red-600" />
        ) : (
          <Copy className="h-3 w-3" />
        )}
      </button>
    </span>
  )
}

/** 审计记录详情页：内嵌在 audit-log tab 主区域，替换列表展示。 */
export function AuditLogDetailDrawer({ eventId, onClose }: AuditLogDetailDrawerProps) {
  const enabled = Boolean(eventId)
  const { data, isLoading, error } = useQuery(
    ['audit-log-detail', eventId],
    () => getAuditLogDetail(eventId as string),
    { enabled, keepPreviousData: false, refetchOnMount: 'always' },
  )

  // ESC 返回列表
  useEffect(() => {
    if (!eventId) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [eventId, onClose])

  // 进入详情滚到顶部
  useEffect(() => {
    if (eventId) window.scrollTo({ top: 0, behavior: 'auto' })
  }, [eventId])

  if (!eventId) return null

  return (
    <div className="flex flex-col gap-5">
      {/* 面包屑 */}
      <nav className="flex items-center gap-1.5 text-sm text-[#6B7280]" aria-label="breadcrumb">
        <button
          type="button"
          onClick={onClose}
          className="text-[#6B7280] hover:text-[#0950DE]"
        >
          审计日志
        </button>
        <ChevronRight className="h-3 w-3 text-[#9CA3AF]" />
        <span className="text-[#111827]">审计记录详情</span>
      </nav>

      {isLoading ? (
        <div className="py-20 text-center text-sm text-[#6B7280]">加载中…</div>
      ) : error ? (
        <div className="py-20 text-center text-sm text-rose-700">
          {error instanceof Error ? error.message : '加载失败'}
        </div>
      ) : data ? (
        <DetailBody detail={data} />
      ) : null}
    </div>
  )
}

function DetailBody({ detail }: { detail: AuditLogDetail }) {
  const obj = pickObjectDisplay(detail)
  const extra = (detail.extra ?? {}) as Record<string, unknown>

  const handleOpenSkill = () => {
    if (!obj.slug) return
    const version = detail.resource_version?.trim()
    const query =
      version && version.toLowerCase() !== 'all'
        ? `?version=${encodeURIComponent(version)}`
        : ''
    window.open(
      `${assetDetailPath(obj.slug, obj.pluginType || detail.asset_plugin_type || detail.resource_type)}${query}`,
      '_blank',
      'noopener',
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 标题：直接是操作摘要 */}
      <h1 className="text-base font-semibold text-[#111827]">
        {detail.detail || '(无操作摘要)'}
      </h1>

      <Section title="操作对象">
        <KV label="名称">
          {obj.clickable && obj.slug ? (
            <button
              type="button"
              onClick={handleOpenSkill}
              className="break-all text-left text-[#2563EB] hover:underline"
            >
              {obj.text}
            </button>
          ) : (
            <span className="text-[#111827]" title={obj.title}>
              {obj.text}
            </span>
          )}
        </KV>
        <KV label="类型">{getObjectTypeLabel(detail)}</KV>
        <KV label="版本">{formatVersion(detail.resource_version)}</KV>
        <KV label="资源 ID">
          <MonoCopy value={detail.resource_id} />
        </KV>
      </Section>

      {/* 操作者 */}
      <Section title="操作者">
        <KV label="用户名称">{detail.operator_name || '—'}</KV>
        <KV label="用户 ID">
          <MonoCopy value={detail.operator_id} />
        </KV>
        <KV label="IP 地址">{detail.ip_address || '—'}</KV>
        <KV label="User-Agent">
          {detail.user_agent ? (
            <span className="text-xs text-[#374151]">{detail.user_agent}</span>
          ) : (
            <span className="text-[#9CA3AF]">—</span>
          )}
        </KV>
      </Section>

      {/* 技术信息 */}
      <Section title="技术信息">
        <KV label="事件 ID">
          <MonoCopy value={detail.event_id} />
        </KV>
        <KV label="请求 ID">
          <MonoCopy value={detail.request_id} />
        </KV>
        <KV label="耗时">{detail.duration_ms} ms</KV>
        <KV label="时间">{formatFullDateTime(detail.created_at_ms)}</KV>
        {detail.extra && Object.keys(extra).length > 0 ? (
          <KV label="扩展信息">
            <pre className="max-h-72 overflow-auto rounded-md bg-[#F9FAFB] p-2 text-[11px] leading-4 text-[#374151]">
              {JSON.stringify(extra, null, 2)}
            </pre>
          </KV>
        ) : null}
      </Section>
    </div>
  )
}