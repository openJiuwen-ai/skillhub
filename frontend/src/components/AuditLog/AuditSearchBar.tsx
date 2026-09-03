// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useRef, useState } from 'react'
import { Calendar, ChevronLeft, Download, Search, X } from 'lucide-react'

import { RangeDayPicker } from './RangeDayPicker'

export type DateRangePreset = 'today' | '7d' | '30d' | 'custom'

export interface DateRange {
  preset: DateRangePreset
  date_from_ms: number
  date_to_ms: number
}

/** 市场资产类型筛选值：可能映射到后端 asset_plugin_type 或 resource_type */
export type SkillTypeFilterValue =
  | { kind: 'all' }
  | { kind: 'asset_plugin_type'; value: string; label: string }
  | { kind: 'resource_type'; value: string; label: string }

export const SKILL_TYPE_OPTIONS: SkillTypeFilterValue[] = [
  { kind: 'all' },
  { kind: 'asset_plugin_type', value: 'skill', label: 'Skill' },
  { kind: 'asset_plugin_type', value: 'swarmskill', label: 'SwarmSkill' },
  { kind: 'resource_type', value: 'plugin', label: '插件' },
  { kind: 'resource_type', value: 'agent-plugin', label: 'agent-plugin' },
  { kind: 'resource_type', value: 'agent-template', label: 'agent-template' },
  { kind: 'resource_type', value: 'agent-mcp', label: 'agent-mcp' },
  { kind: 'resource_type', value: 'git_source', label: 'Git 源' },
  { kind: 'resource_type', value: 'skill_bundle', label: '批量包' },
]

export const ACTION_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'APPROVE', label: '审核通过' },
  { value: 'REJECT', label: '驳回' },
  { value: 'PUBLISH', label: '发布' },
  { value: 'DELETE', label: '删除' },
  { value: 'GIT_SYNC', label: 'Git 同步' },
  { value: 'GIT_SOURCE_DELETE', label: '删除 Git 源' },
  { value: 'IMPORT', label: '批量导入' },
  { value: 'AUTO_REVIEW_PASS', label: '审查通过' },
  { value: 'AUTO_REVIEW_FAIL', label: '审查未通过' },
  { value: 'AUTO_REVIEW_SYS_FAIL', label: '审查异常' },
  { value: 'PENDING_MOD_SET', label: '转入待审' },
  { value: 'EXPORT', label: '导出' },
] as const

export const RESULT_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'SUCCESS', label: '成功' },
  { value: 'FAILED', label: '失败' },
  { value: 'PARTIAL_FAILED', label: '部分失败' },
] as const

export const SOURCE_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'web', label: 'Web' },
  { value: 'cli', label: 'Cli' },
  { value: 'api', label: 'Api' },
  { value: 'background', label: '后台' },
] as const

const MS_PER_DAY = 86_400_000

// 后端按北京时间（UTC+8）解释毫秒戳并与 created_at 比较，前端日界必须同口径，
// 否则非 UTC+8 用户选「今天 / 近 7 天」会差一天或漏/多几条。北京无 DST，固定偏移即可。
const BJ_OFFSET_MS = 8 * 60 * 60 * 1000

function startOfTodayMs(): number {
  const bj = new Date(Date.now() + BJ_OFFSET_MS)
  bj.setUTCHours(0, 0, 0, 0)
  return bj.getTime() - BJ_OFFSET_MS
}

function endOfTodayMs(): number {
  const bj = new Date(Date.now() + BJ_OFFSET_MS)
  bj.setUTCHours(23, 59, 59, 999)
  return bj.getTime() - BJ_OFFSET_MS
}

export function buildLast7DaysRange(): DateRange {
  return {
    preset: '7d',
    date_from_ms: startOfTodayMs() - 6 * MS_PER_DAY,
    date_to_ms: endOfTodayMs(),
  }
}

function formatDate(ms: number): string {
  // 以北京时间展示日期，和日界口径保持一致
  const bj = new Date(ms + BJ_OFFSET_MS)
  const y = bj.getUTCFullYear()
  const m = String(bj.getUTCMonth() + 1).padStart(2, '0')
  const day = String(bj.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function parseInputDate(value: string, endOfDay: boolean): number | null {
  if (!value) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!m) return null
  // 用户选的日期按北京时间的 00:00 / 23:59 解释，再换算成 UTC 毫秒戳
  const utcMs = endOfDay
    ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59, 999)
    : Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0, 0)
  return utcMs - BJ_OFFSET_MS
}

interface AuditSearchBarProps {
  keyword: string
  onKeywordChange: (kw: string) => void
  range: DateRange
  onRangeChange: (range: DateRange) => void
  onExport: () => void
  exporting?: boolean
  skillTypeFilter: SkillTypeFilterValue
  onSkillTypeFilterChange: (v: SkillTypeFilterValue) => void
  actionFilter: string
  onActionFilterChange: (v: string) => void
  resultFilter: string
  onResultFilterChange: (v: string) => void
  sourceFilter: string
  onSourceFilterChange: (v: string) => void
}

type FilterDimension = 'skill_type' | 'action' | 'result' | 'source'

const DIMENSION_LABELS: Record<FilterDimension, string> = {
  skill_type: 'Skill 类型',
  action: '操作类型',
  result: '状态',
  source: '来源',
}


/** 已选条件的小标签，展示在搜索框上方；× 可清除。 */
function ActiveFilterPill({
  label,
  value,
  onClear,
}: {
  label: string
  value: string
  onClear: () => void
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[#EFF6FF] px-2.5 py-1 text-xs font-medium text-[#0950DE]">
      <span>
        {label}: {value}
      </span>
      <button
        type="button"
        onClick={onClear}
        aria-label={`清除 ${label} 筛选`}
        className="rounded-full p-0.5 hover:bg-[#DBEAFE]"
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  )
}


export function AuditSearchBar({
  keyword,
  onKeywordChange,
  range,
  onRangeChange,
  onExport,
  exporting = false,
  skillTypeFilter,
  onSkillTypeFilterChange,
  actionFilter,
  onActionFilterChange,
  resultFilter,
  onResultFilterChange,
  sourceFilter,
  onSourceFilterChange,
}: AuditSearchBarProps) {
  const [localKw, setLocalKw] = useState(keyword)
  const [panelOpen, setPanelOpen] = useState(false)
  // null = 选维度阶段；非 null = 已选某维度，正在选值
  const [pickingDimension, setPickingDimension] = useState<FilterDimension | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const dateAnchorRef = useRef<HTMLButtonElement | null>(null)
  const [rangePickerOpen, setRangePickerOpen] = useState(false)

  useEffect(() => {
    setLocalKw(keyword)
  }, [keyword])

  // Debounce keyword input
  useEffect(() => {
    if (localKw === keyword) return
    const handle = window.setTimeout(() => {
      onKeywordChange(localKw)
    }, 400)
    return () => window.clearTimeout(handle)
  }, [localKw, keyword, onKeywordChange])

  // 点外面关闭面板
  useEffect(() => {
    if (!panelOpen) return
    const handler = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setPanelOpen(false)
        setPickingDimension(null)
      }
    }
    window.addEventListener('mousedown', handler)
    return () => window.removeEventListener('mousedown', handler)
  }, [panelOpen])

  // ESC 关闭/回退
  useEffect(() => {
    if (!panelOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (pickingDimension) setPickingDimension(null)
        else setPanelOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [panelOpen, pickingDimension])

  const handleDateChange = (field: 'from' | 'to', value: string) => {
    const newMs = parseInputDate(value, field === 'to')
    if (newMs == null) return
    onRangeChange({
      preset: 'custom',
      date_from_ms: field === 'from' ? newMs : range.date_from_ms,
      date_to_ms: field === 'to' ? newMs : range.date_to_ms,
    })
  }

  const openPanel = () => {
    setPanelOpen(true)
    setPickingDimension(null)
  }

  const handleApplyValue = (dim: FilterDimension, value: string, valueLabel: string) => {
    if (dim === 'skill_type') {
      // value 形如 "asset_plugin_type:skill" 或 "resource_type:plugin"，空串 = 全部
      if (!value) {
        onSkillTypeFilterChange({ kind: 'all' })
      } else {
        const [kind, raw] = value.split(':') as [
          'asset_plugin_type' | 'resource_type',
          string,
        ]
        onSkillTypeFilterChange({ kind, value: raw, label: valueLabel })
      }
    } else if (dim === 'action') onActionFilterChange(value)
    else if (dim === 'result') onResultFilterChange(value)
    else if (dim === 'source') onSourceFilterChange(value)
    setPickingDimension(null)
    setPanelOpen(false)
  }

  // 已选筛选条件
  const activePills: { key: string; label: string; value: string; onClear: () => void }[] = []
  if (skillTypeFilter.kind !== 'all') {
    activePills.push({
      key: 'skill_type',
      label: 'Skill 类型',
      value: skillTypeFilter.label,
      onClear: () => onSkillTypeFilterChange({ kind: 'all' }),
    })
  }
  if (actionFilter) {
    activePills.push({
      key: 'action',
      label: '操作类型',
      value: ACTION_OPTIONS.find(o => o.value === actionFilter)?.label || actionFilter,
      onClear: () => onActionFilterChange(''),
    })
  }
  if (resultFilter) {
    activePills.push({
      key: 'result',
      label: '状态',
      value: RESULT_OPTIONS.find(o => o.value === resultFilter)?.label || resultFilter,
      onClear: () => onResultFilterChange(''),
    })
  }
  if (sourceFilter) {
    activePills.push({
      key: 'source',
      label: '来源',
      value: SOURCE_OPTIONS.find(o => o.value === sourceFilter)?.label || sourceFilter,
      onClear: () => onSourceFilterChange(''),
    })
  }

  // 当前正选维度对应的值列表
  let valueOptions: ReadonlyArray<{ value: string; label: string }> = []
  if (pickingDimension === 'skill_type') {
    valueOptions = SKILL_TYPE_OPTIONS.map(opt =>
      opt.kind === 'all'
        ? { value: '', label: '全部' }
        : { value: `${opt.kind}:${opt.value}`, label: opt.label },
    )
  } else if (pickingDimension === 'action') {
    valueOptions = ACTION_OPTIONS.map(o => ({ value: o.value, label: o.label }))
  } else if (pickingDimension === 'result') {
    valueOptions = RESULT_OPTIONS.map(o => ({ value: o.value, label: o.label }))
  } else if (pickingDimension === 'source') {
    valueOptions = SOURCE_OPTIONS.map(o => ({ value: o.value, label: o.label }))
  }

  const inputPrefixText = pickingDimension ? `${DIMENSION_LABELS[pickingDimension]}:` : ''

  return (
    <div className="flex flex-col gap-3">
      {/* 已选筛选条件 */}
      {activePills.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {activePills.map(p => (
            <ActiveFilterPill key={p.key} label={p.label} value={p.value} onClear={p.onClear} />
          ))}
        </div>
      ) : null}

      {/* 搜索框（含筛选面板） + 日期 + 导出 */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div ref={containerRef} className="relative min-w-0 flex-[6]">
          {/* 搜索框本体 */}
          <div
            className={`flex h-10 w-full items-center gap-2 rounded-lg border bg-white pl-3.5 pr-3 ${
              panelOpen
                ? 'border-[#4F46E5] ring-2 ring-[#E0E7FF]'
                : 'border-[#E5E7EB]'
            }`}
            onClick={openPanel}
          >
            <Search className="h-4 w-4 shrink-0 text-[#9CA3AF]" aria-hidden />
            {/* 已选维度的前缀 */}
            {pickingDimension ? (
              <span className="inline-flex items-center gap-1 text-sm text-[#0950DE]">
                <span className="font-medium">{inputPrefixText}</span>
                <button
                  type="button"
                  onClick={e => {
                    e.stopPropagation()
                    setPickingDimension(null)
                  }}
                  className="rounded p-0.5 text-[#6B7280] hover:bg-slate-100"
                  aria-label="返回选择筛选维度"
                >
                  <ChevronLeft className="h-3 w-3" />
                </button>
              </span>
            ) : null}
            {/* keyword 输入：仅在未选维度时可输入 */}
            <input
              type="text"
              value={pickingDimension ? '' : localKw}
              onChange={e => {
                if (pickingDimension) return
                setLocalKw(e.target.value)
              }}
              onFocus={openPanel}
              placeholder={
                pickingDimension
                  ? '在下方选择值'
                  : '添加筛选条件：搜索用户名、Skill 名称、资源 ID 或 IP'
              }
              readOnly={Boolean(pickingDimension)}
              className="h-full flex-1 bg-transparent text-sm text-[#111827] placeholder:text-[#9CA3AF] focus:outline-none"
            />
          </div>

          {/* 筛选面板 */}
          {panelOpen ? (
            <div className="absolute left-0 right-0 top-full z-30 mt-2 rounded-xl border border-[#E5E7EB] bg-white py-1.5 shadow-lg">
              {pickingDimension == null ? (
                <>
                  <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[#9CA3AF]">
                    选择筛选维度
                  </div>
                  {(['skill_type', 'action', 'result', 'source'] as FilterDimension[]).map(d => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setPickingDimension(d)}
                      className="block w-full px-3 py-2 text-left text-sm text-[#111827] hover:bg-[#F9FAFB]"
                    >
                      {DIMENSION_LABELS[d]}
                    </button>
                  ))}
                </>
              ) : (
                <>
                  <div className="flex items-center gap-1 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[#9CA3AF]">
                    <button
                      type="button"
                      onClick={() => setPickingDimension(null)}
                      className="inline-flex items-center gap-0.5 text-[11px] text-[#6B7280] hover:text-[#0950DE]"
                    >
                      <ChevronLeft className="h-3 w-3" />
                      返回
                    </button>
                    <span className="ml-2">{DIMENSION_LABELS[pickingDimension]} 值</span>
                  </div>
                  <div className="max-h-72 overflow-y-auto">
                    {valueOptions.map(opt => (
                      <button
                        key={`${opt.value}-${opt.label}`}
                        type="button"
                        onClick={() =>
                          handleApplyValue(pickingDimension, opt.value, opt.label)
                        }
                        className="block w-full px-3 py-2 text-left text-sm text-[#111827] hover:bg-[#F9FAFB]"
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : null}
        </div>

        {/* 日期范围 + 导出 */}
        <div className="flex items-center gap-2">
          <button
            ref={dateAnchorRef}
            type="button"
            aria-label="选择日期范围"
            onClick={() => setRangePickerOpen(true)}
            className="flex h-10 items-center gap-2 rounded-lg border border-[#E5E7EB] bg-white px-3 text-sm text-[#111827] hover:border-[#9CA3AF] focus:border-[#4F46E5] focus:outline-none focus:ring-2 focus:ring-[#E0E7FF]"
          >
            <span>{formatDate(range.date_from_ms)}</span>
            <span className="text-xs text-[#9CA3AF]">~</span>
            <span>{formatDate(range.date_to_ms)}</span>
            <Calendar className="ml-1 h-4 w-4 text-[#6B7280]" aria-hidden />
          </button>
          <RangeDayPicker
            anchorEl={dateAnchorRef.current}
            open={rangePickerOpen}
            onClose={() => setRangePickerOpen(false)}
            range={range}
            onChange={onRangeChange}
          />

          <button
            type="button"
            onClick={onExport}
            disabled={exporting}
            className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-[#D1D5DB] bg-white px-4 text-sm font-medium text-[#374151] hover:border-[#9CA3AF] hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Download className="h-4 w-4" aria-hidden />
            {exporting ? '导出中…' : '导出 CSV'}
          </button>
        </div>
      </div>
    </div>
  )
}
