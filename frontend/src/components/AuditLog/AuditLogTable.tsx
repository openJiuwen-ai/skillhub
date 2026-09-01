// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { AlertCircle, Check, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { AuditLogListItem } from '@/api/audit'
import { assetDetailPath } from '@/utils/pluginType'
import { getObjectTypeLabel, pickObjectDisplay } from './badges'

interface AuditLogTableProps {
  items: AuditLogListItem[]
  loading?: boolean
  onOpenDetail: (item: AuditLogListItem) => void
}

/** 标准格式：2026/05/24 12:34:51（年/月/日 时:分:秒）。 */
function formatDateTime(ms: number): string {
  const d = new Date(ms)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${y}/${m}/${day} ${hh}:${mm}:${ss}`
}

/** 耗时格式化：<1s 显示 ms，1s~1min 显示 s，>1min 显示 m s。
 * 异常情况（duration_ms<=0 / null）显示 —。
 */
function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
  const totalSec = Math.floor(ms / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}m ${s}s`
}

/** 慢操作高亮：>= 3s 标橙、>= 5s 标红（与后端 SLOW_THRESHOLD_MS 对齐） */
function durationClassName(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return 'text-[#9CA3AF]'
  if (ms >= 5_000) return 'text-[#B91C1C] font-semibold'  // red-700 加粗
  if (ms >= 3_000) return 'text-[#B45309] font-medium'    // amber-800
  return 'text-[#374151]'
}

function formatVersion(version?: string | null): string {
  if (!version) return '—'
  return version.toLowerCase() === 'all' ? '全部版本' : version
}

const _ACTION_LABEL_MAP: Record<string, string> = {
  APPROVE: '审核通过',
  REJECT: '驳回',
  PUBLISH: '发布',
  DELETE: '删除',
  GIT_SYNC: 'Git 同步',
  GIT_SOURCE_DELETE: '删除 Git 源',
  IMPORT: '批量导入',
  AUTO_REVIEW_PASS: '审查通过',
  AUTO_REVIEW_FAIL: '审查未通过',
  AUTO_REVIEW_SYS_FAIL: '审查异常',
  PENDING_MOD_SET: '转入待审',
  EXPORT: '导出',
  DOWNLOAD: '下载',
  MODERATE: '审核操作',
}

const _SOURCE_LABEL_MAP: Record<string, string> = {
  web: 'Web',
  cli: 'Cli',
  api: 'Api',
  background: '后台',
}

function actionLabel(value: string): string {
  return _ACTION_LABEL_MAP[value] || value
}

function sourceLabel(value?: string | null): string {
  if (!value) return '—'
  return _SOURCE_LABEL_MAP[value] || value
}

/** 状态：实心圆 badge（绿√/红×）+ 文字。 */
function StatusCell({ value }: { value: string }) {
  if (value === 'SUCCESS') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[#111827]">
        <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500">
          <Check className="h-3 w-3 text-white" strokeWidth={3} />
        </span>
        <span>成功</span>
      </span>
    )
  }
  if (value === 'PARTIAL_FAILED') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[#111827]">
        <AlertCircle className="h-4 w-4 text-amber-500" />
        <span>部分失败</span>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[#111827]">
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-rose-500">
        <X className="h-3 w-3 text-white" strokeWidth={3} />
      </span>
      <span>失败</span>
    </span>
  )
}

export function AuditLogTable({
  items,
  loading = false,
  onOpenDetail,
}: AuditLogTableProps) {
  const navigate = useNavigate()
  const handleOpenSkill = (item: AuditLogListItem, slug: string, pluginType?: string | null) => {
    const version = item.resource_version?.trim()
    const query = version && version.toLowerCase() !== 'all' ? `?version=${encodeURIComponent(version)}` : ''
    navigate(`${assetDetailPath(slug, pluginType || item.asset_plugin_type || item.resource_type)}${query}`)
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[#E5E7EB] bg-white">
      <table className="min-w-full divide-y divide-[#E5E7EB] text-sm">
        <thead className="bg-[#F9FAFB] text-xs tracking-wide text-[#111827]">
          <tr>
            <th className="px-4 py-3 text-left font-bold">操作对象</th>
            <th className="px-4 py-3 text-left font-bold">对象类型</th>
            <th className="px-4 py-3 text-left font-bold">版本</th>
            <th className="px-4 py-3 text-left font-bold">操作类型</th>
            <th className="px-4 py-3 text-left font-bold">状态</th>
            <th className="px-4 py-3 text-left font-bold">用户名称</th>
            <th className="px-4 py-3 text-left font-bold">来源</th>
            <th className="px-4 py-3 text-left font-bold">耗时</th>
            <th className="px-4 py-3 text-left font-bold">操作时间</th>
            <th className="px-4 py-3 text-left font-bold">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#F3F4F6] bg-white">
          {items.length === 0 ? (
            <tr>
              <td colSpan={10} className="px-3 py-10 text-center text-sm text-[#6B7280]">
                {loading ? '加载中…' : '当前条件下没有审计记录'}
              </td>
            </tr>
          ) : (
            items.map(item => {
              const obj = pickObjectDisplay(item)
              return (
                <tr key={item.event_id} className="hover:bg-[#F9FAFB]">
                  <td
                    className="max-w-[200px] truncate px-4 py-3"
                    title={obj.title || obj.text}
                  >
                    {obj.clickable && obj.slug ? (
                      <button
                        type="button"
                        onClick={() => handleOpenSkill(item, obj.slug as string, obj.pluginType)}
                        className="truncate text-left text-[#2563EB] hover:underline"
                      >
                        {obj.text}
                      </button>
                    ) : (
                      <span className="text-[#374151]">{obj.text}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[#374151]">{getObjectTypeLabel(item)}</td>
                  <td className="px-4 py-3 text-[#374151] tabular-nums">
                    {formatVersion(item.resource_version)}
                  </td>
                  <td className="px-4 py-3 text-[#111827]">{actionLabel(item.action)}</td>
                  <td className="px-4 py-3">
                    <StatusCell value={item.result} />
                  </td>
                  <td className="px-4 py-3 text-[#374151]" title={item.operator_id}>
                    {item.operator_name || item.operator_id}
                  </td>
                  <td className="px-4 py-3 text-[#374151]">
                    {sourceLabel(item.source_channel)}
                  </td>
                  <td
                    className={`px-4 py-3 tabular-nums ${durationClassName(item.duration_ms)}`}
                    title={item.duration_ms ? `${item.duration_ms} ms` : undefined}
                  >
                    {formatDuration(item.duration_ms)}
                  </td>
                  <td className="px-4 py-3 text-[#111827] tabular-nums">
                    {formatDateTime(item.created_at_ms)}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => onOpenDetail(item)}
                      className="text-sm text-[#2563EB] hover:underline"
                    >
                      查看详情
                    </button>
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      {loading && items.length > 0 ? (
        <div className="border-t border-[#F3F4F6] px-4 py-2 text-center text-xs text-[#9CA3AF]">刷新中…</div>
      ) : null}
    </div>
  )
}
