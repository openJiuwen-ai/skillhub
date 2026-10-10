// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { ExternalLink, Plus } from 'lucide-react'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { getPrimarySkillPluginType } from '@/utils/pluginType'
import jiuwenLogo from '@/assets/jiuwen-logo.png'
import { LanguageSwitcher } from '@/components/Common/common-page/LanguageSwitcher'
import { NotificationBell } from '@/components/Common/NotificationBell'

export type AppHeaderProps = {
  /** 覆写「+ 发布」按钮的行为；未提供时默认打开发布抽屉。 */
  onPublish?: () => void
  /** 覆盖「发布」文案，默认取 `appHeader.publish`。 */
  publishLabel?: string
  /** 显式控制是否渲染发布按钮；默认登录后始终渲染（个人中心页传 false 隐藏）。 */
  showPublish?: boolean
  /** 右侧渲染 logo 之外的附加节点（如语言切换、刷新），放在发布按钮左侧。 */
  extraRight?: React.ReactNode
}

/** 全站顶部导航栏：粘性固定在页面顶端，品牌在左、发布与账号菜单在右。 */
export function AppHeader({
  onPublish,
  publishLabel,
  showPublish = true,
  extraRight,
}: AppHeaderProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, isAuthenticated, logout } = useGitCodeAuth()
  const { openPublish } = usePublishDrawer()

  const handlePublishClick = onPublish ?? (() => openPublish(getPrimarySkillPluginType()))

  return (
    <header className="sticky top-0 z-30 w-full shrink-0 border-b border-[#EDEDED] bg-white/95 backdrop-blur supports-[backdrop-filter]:bg-white/80 pt-[env(safe-area-inset-top,0px)]">
      <div className="mx-auto flex h-14 w-full min-w-0 items-center justify-between gap-2 px-3 sm:gap-3 sm:px-4 md:h-16 md:max-w-[1600px] md:px-0">
        <Link
          to="/"
          className="inline-flex min-w-0 max-w-[55%] items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] sm:max-w-none sm:gap-2"
          aria-label={t('appHeader.brand')}
        >
          <img
            src={jiuwenLogo}
            alt=""
            aria-hidden
            draggable={false}
            className="h-7 w-7 shrink-0 select-none sm:h-8 sm:w-8"
          />
          <span className="truncate text-[15px] font-bold tracking-wide text-[#191919] sm:text-[16px]">
            {t('appHeader.brand')}
          </span>
        </Link>

        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2 md:gap-3">
          {extraRight}
          {showPublish && isAuthenticated ? (
            <button
              type="button"
              onClick={handlePublishClick}
              className="inline-flex h-8 min-w-[5.25rem] items-center justify-center gap-1 rounded-full bg-[linear-gradient(99.61deg,#1E54FA_0%,#842EFD_100%)] px-2.5 text-xs font-medium text-white shadow-[0_4px_12px_rgba(81,64,246,0.18)] transition-opacity hover:opacity-90 sm:h-9 sm:w-[116px] sm:px-0 sm:text-sm"
            >
              <Plus className="h-4 w-4 shrink-0" aria-hidden />
              <span className="truncate">{publishLabel ?? t('appHeader.publish')}</span>
            </button>
          ) : null}
          <a
            href="https://www.openjiuwen.com"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex h-8 items-center gap-1 rounded-full border border-slate-200 bg-white/80 px-2 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] sm:gap-1.5 sm:px-3 sm:text-sm"
            aria-label={t('appHeader.openJiuwen')}
            title={t('appHeader.openJiuwen')}
          >
            <span className="hidden sm:inline">openJiuwen</span>
            <ExternalLink className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />
          </a>
          <span className="hidden h-5 w-px bg-slate-200 md:inline-block" aria-hidden />
          <LanguageSwitcher />
          <NotificationBell />
          {isAuthenticated && user ? (
            <HeaderAccountMenu
              avatarUrl={user.avatar_url || ''}
              primaryLabel={user.name || user.login}
              secondaryLabel={`@${user.login}`}
              onProfile={() => navigate('/profile')}
              onGroups={() => navigate('/groups')}
              onLogout={() => logout()}
            />
          ) : (
            <button
              type="button"
              onClick={() => navigate('/login')}
              className="text-sm font-medium text-[#0369a1] hover:text-[#0c4a6e]"
            >
              {t('auth.toolbar.login')}
            </button>
          )}
        </div>
      </div>
    </header>
  )
}

type HeaderAccountMenuProps = {
  avatarUrl: string
  primaryLabel: string
  secondaryLabel?: string
  onProfile: () => void
  onGroups: () => void
  onLogout: () => void
}

/** 顶部头像下拉菜单：头像图加载失败自动回落为姓名首字母。 */
function HeaderAccountMenu({
  avatarUrl,
  primaryLabel,
  secondaryLabel,
  onProfile,
  onGroups,
  onLogout,
}: HeaderAccountMenuProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLUListElement>(null)
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null)

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

  const items: Array<{ id: string; label: string; onClick: () => void; danger?: boolean }> = [
    { id: 'profile', label: t('appHeader.profile'), onClick: onProfile },
    { id: 'groups', label: t('appHeader.groups'), onClick: onGroups },
    { id: 'logout', label: t('appHeader.logout'), onClick: onLogout },
  ]

  const panel =
    open && coords ? (
      <div
        ref={menuRef as unknown as React.RefObject<HTMLDivElement>}
        style={{
          position: 'fixed',
          top: coords.top,
          right: coords.right,
          zIndex: 1400,
        }}
        className="min-w-[160px] overflow-hidden rounded-[12px] border border-transparent bg-white shadow-[0_4px_40px_rgba(0,0,0,0.10)]"
        role="menu"
      >
        <div className="border-b border-slate-100 px-3 py-2.5">
          <div className="truncate text-sm font-semibold text-[#111827]" title={primaryLabel}>
            {primaryLabel}
          </div>
          {secondaryLabel ? (
            <div className="mt-0.5 truncate text-xs text-[#9CA3AF]" title={secondaryLabel}>
              {secondaryLabel}
            </div>
          ) : null}
        </div>
        <ul className="py-1">
          {items.map(item => (
            <li key={item.id} role="none">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  item.onClick()
                }}
                className="flex w-full items-center px-3 py-2.5 text-left text-sm text-[#374151] hover:bg-slate-50 active:bg-slate-100"
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
    ) : null

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={primaryLabel}
        onClick={() => setOpen(prev => !prev)}
        className="inline-flex h-9 w-9 items-center justify-center rounded-full outline-none ring-offset-2 transition-shadow hover:ring-2 hover:ring-[#E0E7FF] focus:ring-2 focus:ring-[#C7D2FE]"
      >
        <HeaderAvatar avatarUrl={avatarUrl} name={primaryLabel} />
      </button>
      {typeof document !== 'undefined' && panel ? createPortal(panel, document.body) : null}
    </div>
  )
}

type HeaderAvatarProps = { avatarUrl: string; name: string }

/** 顶栏头像：`avatar_url` 为空或加载失败时回落为姓名首字母（支持中英文首字）。 */
function HeaderAvatar({ avatarUrl, name }: HeaderAvatarProps) {
  const [failed, setFailed] = useState(false)
  const src = avatarUrl.trim()
  const showImg = Boolean(src) && !failed
  const initial = (name || 'U').trim().charAt(0).toUpperCase() || 'U'

  if (showImg) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden
        draggable={false}
        onError={() => setFailed(true)}
        className="h-9 w-9 rounded-full object-cover"
      />
    )
  }
  return (
    <div
      aria-hidden
      className="flex h-9 w-9 items-center justify-center rounded-full bg-[#E0E7FF] text-sm font-semibold text-[#4338CA]"
    >
      {initial}
    </div>
  )
}

export default AppHeader
