import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

interface SelectedTagsBarProps {
  selectedTags: string[]
  /** 点击已选标签 = 取消选中；父级负责移除 + 清技能搜索。 */
  onToggle: (tag: string) => void
  variant: 'desktop' | 'mobile'
}

// 已选标签区：集中展示当前已选标签，点即移除。空时整块隐藏（不占位）。
// 与推荐区分离（推荐区已 filter 掉已选），避免同一标签在两处重复显示。
export function SelectedTagsBar({ selectedTags, onToggle, variant }: SelectedTagsBarProps) {
  const { t } = useTranslation()
  if (selectedTags.length === 0) return null

  return (
    <section
      role="region"
      aria-label={t('plugins.tagSelectedAria')}
      className="flex flex-col gap-1.5"
    >
      <p className="px-1 text-[11px] font-medium uppercase tracking-wide text-[#999]">
        {t('plugins.tagSelectedHeader')}
      </p>
      {variant === 'desktop' ? (
        <div className="max-h-40 overflow-y-auto pr-1">
          {selectedTags.map(tag => (
            <button
              key={tag}
              type="button"
              onClick={() => onToggle(tag)}
              aria-label={`${t('plugins.tagSelectedRemoveAria')} ${tag}`}
              className="flex h-9 w-full items-center justify-between rounded-[4px] bg-[#EEF3FF] px-3 text-[14px] leading-[22px] text-[#1E54F9] transition-colors hover:bg-[#E0E7FF]"
            >
              <span className="truncate text-left font-medium">{tag}</span>
              <X className="h-3.5 w-3.5 shrink-0 text-[#6B7CF6]" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {selectedTags.map(tag => (
            <button
              key={tag}
              type="button"
              onClick={() => onToggle(tag)}
              aria-label={`${t('plugins.tagSelectedRemoveAria')} ${tag}`}
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-[#c8d9ff] bg-[#EEF3FF] px-[14px] py-[6px] text-[14px] font-medium text-[#1E54F9] transition-colors hover:bg-[#E0E7FF]"
            >
              <span className="whitespace-nowrap">{tag}</span>
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

export default SelectedTagsBar
