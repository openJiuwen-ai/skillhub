// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { Bell } from 'lucide-react'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { fetchNotifications, markAllNotificationsRead, type SiteNotificationItem } from '@/api/notifications'

const POLL_MS = 60_000

function formatListTime(createdAtMs: number, locale: string): string {
  try {
    return new Date(createdAtMs).toLocaleString(locale === 'en' || locale.startsWith('en') ? 'en-US' : 'zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

function notificationTarget(template: string): string | null {
  return template === 'group_skill_grant_pending' ? '/profile?tab=groups' : null
}

export function NotificationBell() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { isAuthenticated } = useGitCodeAuth()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<SiteNotificationItem[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null)

  const load = useCallback(async () => {
    if (!isAuthenticated) return
    setLoading(true)
    try {
      const data = await fetchNotifications()
      if (data) {
        setItems(data.items)
        setUnreadCount(data.unread_count)
      }
    } catch {
      // 静默；轮询失败不应打扰用户
    } finally {
      setLoading(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated) {
      setItems([])
      setUnreadCount(0)
      return
    }
    void load()
    const id = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(id)
  }, [isAuthenticated, load])

  const markReadAndRefresh = useCallback(async () => {
    const ok = await markAllNotificationsRead()
    if (ok) {
      setUnreadCount(0)
      setItems(p => p.map(it => ({ ...it, read: true })))
    }
    await load()
  }, [load])

  const recalc = useCallback(() => {
    const el = rootRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setCoords({ top: r.bottom + 6, right: window.innerWidth - r.right })
  }, [])

  useEffect(() => {
    if (!open) {
      setCoords(null)
      return
    }
    recalc()
    const onScrollOrResize = () => recalc()
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [open, recalc])

  useEffect(() => {
    if (!open) return
    const onClickOutside = (event: MouseEvent) => {
      const target = event.target as Node
      if (rootRef.current?.contains(target)) return
      if (menuRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onClickOutside)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const onToggle = () => {
    setOpen(p => {
      const next = !p
      if (next) {
        void markReadAndRefresh()
      }
      return next
    })
  }

  if (!isAuthenticated) {
    return null
  }

  const panel =
    open && coords ? (
      <div
        ref={menuRef}
        style={{ position: 'fixed', top: coords.top, right: coords.right, zIndex: 1400 }}
        className="w-[min(20rem,calc(100vw-1.5rem))] max-h-[min(22rem,70vh)] overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-[0_8px_24px_rgba(15,23,42,0.12)]"
        role="dialog"
        aria-label={t('appHeader.notificationsTitle')}
      >
        <div className="border-b border-slate-100 px-3 py-2">
          <div className="text-sm font-semibold text-[#111827]">{t('appHeader.notificationsTitle')}</div>
        </div>
        <div className="max-h-[min(18rem,60vh)] overflow-y-auto px-1 py-1">
          {loading && !items.length ? (
            <div className="px-3 py-4 text-center text-sm text-slate-500">…</div>
          ) : items.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-slate-500">{t('appHeader.notificationsEmpty')}</div>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {items.map(it => {
                const target = notificationTarget(it.template)
                return (
                  <li key={it.id}>
                    <button
                      type="button"
                      onClick={() => {
                        if (target) navigate(target)
                        setOpen(false)
                      }}
                      className="w-full rounded-md px-2.5 py-2.5 text-left hover:bg-slate-50/90 disabled:cursor-default"
                      disabled={!target}
                    >
                      <p className="text-sm leading-snug text-[#374151]">{it.message}</p>
                      <p className="mt-1.5 text-xs text-[#9CA3AF]">{formatListTime(it.created_at_ms, i18n.language)}</p>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
        <div className="border-t border-slate-100 px-3 py-2.5 text-center text-xs text-[#6B7280]">
          <Link
            to="/profile"
            className="font-medium text-[#0369a1] hover:text-[#0c4a6e]"
            onClick={() => setOpen(false)}
          >
            {t('appHeader.profile')}
          </Link>
        </div>
      </div>
    ) : null

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={onToggle}
        aria-label={t('appHeader.notificationsAria')}
        className="relative inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-600 outline-none transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
      >
        <Bell className="h-[1.15rem] w-[1.15rem]" strokeWidth={2} aria-hidden />
        {unreadCount > 0 ? (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold leading-none text-white"
            aria-label={t('appHeader.notificationsUnreadBadge')}
          >
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        ) : null}
      </button>
      {typeof document !== 'undefined' && panel ? createPortal(panel, document.body) : null}
    </div>
  )
}
