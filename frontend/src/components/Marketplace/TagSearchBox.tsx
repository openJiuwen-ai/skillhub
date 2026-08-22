import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from 'react-query'
import { Check, Search, X } from 'lucide-react'
import { getPluginTagOptions } from '@/api/plugin'
import { loadRecentTags } from '@/utils/recentTags'

interface TagSearchBoxProps {
  activeType: string
  selectedTags: string[]
  /** 选中/取消选中一个标签；父级负责写历史 + 选中 + 清技能搜索。 */
  onToggle: (tag: string) => void
}

const DEBOUNCE_MS = 250
const SEARCH_LIMIT = 50

// 标签搜索下拉：空输入聚焦 -> 最近选过的标签；输入 -> debounce 后服务端子串匹配。
// 历史/选中状态由父级管理，本组件只管搜索输入、下拉、键盘导航。
export function TagSearchBox({ activeType, selectedTags, onToggle }: TagSearchBoxProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'
  const containerRef = useRef<HTMLDivElement>(null)

  const [input, setInput] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [recentTags, setRecentTags] = useState<string[]>(() => loadRecentTags(activeType))

  // activeType 切换时重载该类型的历史（不同类型标签集不同，历史隔离）。
  useEffect(() => {
    setRecentTags(loadRecentTags(activeType))
  }, [activeType])

  // debounce input -> debounced；清空立即同步（不等延迟，让历史即时回显）。
  useEffect(() => {
    const trimmed = input.trim()
    if (trimmed === debounced) return
    if (trimmed === '') {
      setDebounced('')
      return
    }
    const handle = window.setTimeout(() => setDebounced(trimmed), DEBOUNCE_MS)
    return () => window.clearTimeout(handle)
  }, [input, debounced])

  const searchQuery = useQuery(
    ['plugins', 'tag-options', activeType, debounced],
    () => getPluginTagOptions({ plugin_type: activeType, limit: SEARCH_LIMIT, keyword: debounced }),
    { enabled: open && debounced.length > 0, staleTime: 60_000, keepPreviousData: true },
  )
  const searchResults = useMemo(() => searchQuery.data ?? [], [searchQuery.data])

  // 下拉数据源：有 keyword -> 服务端匹配（带 count）；空 -> 历史（无 count）。
  const items = useMemo<{ tag: string; count?: number }[]>(() => {
    if (debounced) return searchResults.map(r => ({ tag: r.tag, count: r.count }))
    return recentTags.map(tag => ({ tag }))
  }, [debounced, searchResults, recentTags])

  // 列表变化时重置高亮：有项 -> 0（回车直选首项），无项 -> -1。
  useEffect(() => {
    setActiveIdx(items.length > 0 ? 0 : -1)
  }, [items])

  // 点击外部关闭。
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', handler)
    return () => window.removeEventListener('mousedown', handler)
  }, [open])

  const handleSelect = (tag: string) => {
    onToggle(tag)
    // onToggle 已写 localStorage，刷新本地 recentTags 副本，否则下次空输入开下拉时「最近使用」仍显旧值（activeType 未变不触发 useEffect 重载）。
    setRecentTags(loadRecentTags(activeType))
    setInput('')
    setDebounced('')
    setOpen(false)
    setActiveIdx(-1)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setOpen(false)
      return
    }
    if (items.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIdx(i => (i + 1) % items.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      setActiveIdx(i => (i - 1 + items.length) % items.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = items[activeIdx]
      if (item) handleSelect(item.tag)
    }
  }

  const isSearching = Boolean(debounced) && searchQuery.isFetching
  const emptyState =
    items.length === 0
      ? isSearching
        ? t('plugins.tagSearchLoading')
        : debounced
          ? t('plugins.tagSearchNoMatch')
          : t('plugins.tagSearchEmptyHistory')
      : null
  const showRecentHeader = !debounced && recentTags.length > 0

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#A3A3A3]" aria-hidden="true" />
        <input
          type="text"
          value={input}
          onChange={e => {
            setInput(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={t('plugins.tagSearchPlaceholder')}
          aria-label={t('plugins.tagSearchPlaceholder')}
          aria-expanded={open}
          aria-controls="tag-search-listbox"
          aria-activedescendant={open && activeIdx >= 0 ? `tag-search-opt-${activeIdx}` : undefined}
          className="h-9 w-full rounded-full border border-slate-200 bg-white pl-9 pr-8 text-[14px] leading-none text-[#191919] outline-none placeholder:text-[#A3A3A3] focus:border-[#1E54F9]"
        />
        {input && (
          <button
            type="button"
            onClick={() => {
              setInput('')
              setDebounced('')
            }}
            className="absolute right-2 top-1/2 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label={t('plugins.tagSearchClearAria')}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div
          id="tag-search-listbox"
          role="listbox"
          aria-label={t('plugins.tagSearchListAria')}
          className="absolute left-0 right-0 top-full z-30 mt-2 max-h-80 overflow-y-auto rounded-xl border border-[#E5E7EB] bg-white py-1.5 shadow-lg"
        >
          {emptyState ? (
            <p className="px-3 py-2 text-[13px] text-[#999]">{emptyState}</p>
          ) : (
            <>
              {showRecentHeader && (
                <p className="px-3 pb-1 pt-1 text-[11px] font-medium uppercase tracking-wide text-[#999]">
                  {t('plugins.tagSearchRecent')}
                </p>
              )}
              {items.map((item, idx) => {
                const active = selectedTags.includes(item.tag)
                const highlighted = idx === activeIdx
                return (
                  <button
                    key={item.tag}
                    id={`tag-search-opt-${idx}`}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => handleSelect(item.tag)}
                    className={`flex w-full items-center justify-between px-3 py-2 text-left text-[14px] transition-colors ${
                      highlighted ? 'bg-[#F5F7FF] text-[#1E54F9]' : 'text-[#191919] hover:bg-[#F9FAFB]'
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      {active && <Check className="h-3.5 w-3.5 shrink-0 text-[#6B7CF6]" aria-hidden="true" />}
                      <span className="truncate">{item.tag}</span>
                    </span>
                    {item.count != null && (
                      <span className="shrink-0 pl-2 text-[12px] tabular-nums text-[#999]">
                        {item.count.toLocaleString(locale)}
                      </span>
                    )}
                  </button>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default TagSearchBox
