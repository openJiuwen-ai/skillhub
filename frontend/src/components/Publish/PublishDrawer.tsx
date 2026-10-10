// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from 'react-query'
import { X } from 'lucide-react'
import { PublishForm } from './PublishForm'
import type { PublishDrawerType } from '@/contexts/PublishDrawer'
import { isAgentAssetPluginType } from '@/utils/pluginType'
import { resolvePublishTypeLabel } from '@/utils/publishFieldLabels'

type PublishDrawerProps = {
  open: boolean
  type: PublishDrawerType
  onClose: () => void
}

/** 右侧发布抽屉：点击遮罩或 ESC 关闭。 */
export function PublishDrawer({ open, type, onClose }: PublishDrawerProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [activeType, setActiveType] = useState<PublishDrawerType>(type)

  useEffect(() => {
    if (open) setActiveType(type)
  }, [open, type])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  const handleSuccess = () => {
    void queryClient.invalidateQueries({ queryKey: ['my-published-skills'] })
    void queryClient.invalidateQueries({ queryKey: ['admin-pending-skills'] })
    void queryClient.invalidateQueries({ queryKey: ['publish-my-plugins'] })
    void queryClient.invalidateQueries({ queryKey: ['market-configs'] })
  }

  const typeLabel = resolvePublishTypeLabel(activeType, t)
  const intro = isAgentAssetPluginType(activeType) ? t('publish.introAgent') : t('publish.introSkill')

  return (
    <div
      className={`fixed inset-0 z-50 ${open ? 'pointer-events-auto' : 'pointer-events-none'}`}
      role="dialog"
      aria-modal="true"
      aria-hidden={!open}
      aria-label={t('publish.titleWithType', { typeLabel })}
    >
      <button
        type="button"
        aria-label={t('appHeader.closePublish')}
        onClick={onClose}
        tabIndex={open ? 0 : -1}
        className={`absolute inset-0 h-full w-full cursor-default bg-slate-900/45 transition-opacity duration-200 ${
          open ? 'opacity-100' : 'opacity-0'
        }`}
      />

      <aside
        className={`absolute inset-y-0 right-0 flex h-full w-full max-w-[480px] flex-col bg-white pb-[env(safe-area-inset-bottom,0px)] pt-[env(safe-area-inset-top,0px)] shadow-[-16px_0_32px_-16px_rgba(15,23,42,0.18)] transition-transform duration-300 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        onClick={e => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 px-5 pb-3 pt-5 sm:px-6 sm:pb-3 sm:pt-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-[17px] font-semibold tracking-tight text-[#0F172A]">
              {t('publish.titleWithType', { typeLabel })}
            </h2>
            <p className="mt-1 max-w-[26rem] text-[12px] leading-5 text-[#64748B]">
              {intro}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('appHeader.closePublish')}
            className="-mt-1 -mr-1 rounded-full p-1.5 text-[#94A3B8] transition-colors hover:bg-slate-100 hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div className="mx-5 h-px bg-slate-200 sm:mx-6" />

        {open ? (
          <PublishForm type={type} onCancel={onClose} onSuccess={handleSuccess} onTypeChange={setActiveType} />
        ) : null}
      </aside>
    </div>
  )
}

export default PublishDrawer
