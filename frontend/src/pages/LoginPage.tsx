// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, ArrowRight, Loader2, ShieldCheck, Sparkles } from 'lucide-react'
import {
  exchangeOAuthSession,
  fetchOAuthMe,
  getOAuthStartUrl,
  OAUTH_ACTIVE_PROVIDER_KEY,
  OAUTH_PENDING_KEY,
  parseOAuthPending,
  stringifyOAuthPending,
} from '@/api/auth'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import type { OAuthProvider } from '@/auth/gitcodeStorage'
import { getSiteConfig } from '@/api/playground'
import { POST_LOGIN_REDIRECT_KEY, sanitizePostLoginPath } from '@/auth/postLoginRedirect'
import { AppHeader } from '@/components/Common/AppHeader'
import jiuwenLogo from '@/assets/jiuwen-logo.png'

export default function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const { login, isAuthenticated } = useGitCodeAuth()
  const [commonError, setCommonError] = useState('')
  const [exchanging, setExchanging] = useState(false)

  // AgentOS 登录按钮是否显示：由后端 /site/config 返回的 agentos_oauth_enabled 控制
  // （环境变量 MARKET_AGENTOS_OAUTH_ENABLED），默认 false 不显示
  const [agentosOAuthEnabled, setAgentosOAuthEnabled] = useState(false)

  // 是否从标星按钮跳来：从 URL 参数同步读取，惰性初始化避免闪烁
  const [fromStar, setFromStar] = useState(() => searchParams.get('from') === 'star')

  useEffect(() => {
    getSiteConfig()
      .then(cfg => setAgentosOAuthEnabled(Boolean(cfg.agentos_oauth_enabled)))
      .catch(() => { /* 获取失败按未启用处理 */ })
  }, [])

  useEffect(() => {
    if (!isAuthenticated) return
    if (location.pathname !== '/login') return
    let next = '/'
    try {
      const raw = sessionStorage.getItem(POST_LOGIN_REDIRECT_KEY)
      if (raw) {
        sessionStorage.removeItem(POST_LOGIN_REDIRECT_KEY)
        next = sanitizePostLoginPath(raw, '/')
      }
    } catch {
      /* ignore */
    }
    navigate(next, { replace: true })
  }, [isAuthenticated, navigate, location.pathname])

  useEffect(() => {
    const oauthErr = searchParams.get('oauth_error')
    if (!oauthErr) return
    try {
      setCommonError(decodeURIComponent(oauthErr.replace(/\+/g, ' ')))
    } catch {
      setCommonError(oauthErr)
    }
    navigate('/login', { replace: true })
  }, [searchParams, navigate])

  useEffect(() => {
    const fromUrl = searchParams.get('oauth_session')
    if (fromUrl) {
      const providerFromUrl = (searchParams.get('oauth_provider') || '').trim().toLowerCase()
      const provider =
        providerFromUrl === 'github' || providerFromUrl === 'gitcode' || providerFromUrl === 'agentos'
          ? providerFromUrl
          : (() => {
              const stored = (sessionStorage.getItem(OAUTH_ACTIVE_PROVIDER_KEY) || 'gitcode').toLowerCase()
              if (stored === 'github' || stored === 'agentos') return stored as OAuthProvider
              return 'gitcode'
            })()
      sessionStorage.setItem(OAUTH_PENDING_KEY, stringifyOAuthPending({ provider, session: fromUrl }))
      navigate('/login', { replace: true })
      return
    }
    const raw = sessionStorage.getItem(OAUTH_PENDING_KEY)
    if (!raw) return
    const pending = parseOAuthPending(raw)
    sessionStorage.removeItem(OAUTH_PENDING_KEY)
    if (!pending) {
      setCommonError(t('auth.oauth.genericError'))
      return
    }
    setExchanging(true)
    setCommonError('')
    exchangeOAuthSession(pending.provider, pending.session)
      .then(async data => {
        const profile = await fetchOAuthMe(data.access_token, data.provider)
        login(data.access_token, profile, data.provider)
      })
      .catch(e => {
        const msg = e instanceof Error ? e.message : t('auth.oauth.genericError')
        setCommonError(msg)
      })
      .finally(() => setExchanging(false))
  }, [searchParams, navigate, login, t])

  const startOAuth = (provider: OAuthProvider) => {
    setCommonError('')
    sessionStorage.setItem(OAUTH_ACTIVE_PROVIDER_KEY, provider)
    window.location.href = getOAuthStartUrl(provider)
  }

  return (
    <div className="flex min-h-dvh flex-col overflow-x-hidden bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
      <AppHeader showPublish={false} />
      <main className="relative flex flex-1 flex-col items-center justify-center px-4 py-10 sm:py-14">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 -left-24 h-72 w-72 rounded-full bg-[#1E54F9] opacity-[0.08] blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-32 -right-20 h-80 w-80 rounded-full bg-[#852EFE] opacity-[0.10] blur-3xl"
        />

        <div className="animate-notice-in relative w-full max-w-md">
          <div className="relative overflow-hidden rounded-2xl border border-white/60 bg-white/80 p-5 shadow-[0_24px_60px_-20px_rgba(79,70,229,0.25)] backdrop-blur-xl sm:rounded-3xl sm:p-8">
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#1E54F9] via-[#6366F1] to-[#852EFE]"
            />

            <div className="mb-6 flex flex-col items-center text-center">
              <div className="relative mb-4 flex h-16 w-16 items-center justify-center">
                <span
                  aria-hidden
                  className="absolute -inset-1 rounded-2xl bg-gradient-to-br from-[#1E54F9]/25 to-[#852EFE]/25 blur-lg"
                />
                <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl border border-white/80 bg-white shadow-[0_10px_30px_-10px_rgba(79,70,229,0.35)] ring-1 ring-slate-100">
                  <img
                    src={jiuwenLogo}
                    alt=""
                    aria-hidden
                    draggable={false}
                    className="h-10 w-10 select-none"
                  />
                </div>
              </div>
              <h1 className="text-[22px] font-bold tracking-tight text-slate-900">
                {t('auth.login.title')}
              </h1>
              <p className="mt-2 text-[13.5px] leading-relaxed text-slate-500">
                {t('auth.login.subtitle')}
              </p>
            </div>

            {commonError ? (
              <div className="mb-4 flex items-start gap-2 rounded-xl border border-red-200/80 bg-red-50/80 px-3 py-2.5 text-sm text-red-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" aria-hidden />
                <span className="leading-relaxed">{commonError}</span>
              </div>
            ) : null}

            {exchanging ? (
              <div className="mb-4 flex items-center justify-center gap-2 rounded-xl border border-indigo-100/80 bg-indigo-50/60 px-3 py-2.5 text-sm text-indigo-700">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                <span>{t('auth.oauth.exchanging')}</span>
              </div>
            ) : null}

            {/* 标星入口只显示 GitHub 登录；正常入口显示 GitCode + GitHub */}
            {fromStar ? null : (
              <button
                type="button"
                disabled={exchanging}
                onClick={() => startOAuth('gitcode')}
                className="group relative inline-flex h-12 w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-[#1E54F9] to-[#852EFE] px-5 text-[15px] font-semibold text-white shadow-[0_10px_24px_-10px_rgba(79,70,229,0.65)] transition-all hover:shadow-[0_14px_28px_-10px_rgba(79,70,229,0.75)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span
                  aria-hidden
                  className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/20 to-white/0 opacity-0 transition-opacity group-hover:opacity-100"
                />
                <Sparkles className="relative h-4 w-4" aria-hidden />
                <span className="relative">{t('auth.login.gitcodeButton')}</span>
                <ArrowRight className="relative h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
              </button>
            )}
            {fromStar || !agentosOAuthEnabled ? null : (
              <button
                type="button"
                disabled={exchanging}
                onClick={() => startOAuth('agentos')}
                className="group relative inline-flex h-12 w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-[#1E54F9] to-[#852EFE] px-5 text-[15px] font-semibold text-white shadow-[0_10px_24px_-10px_rgba(79,70,229,0.65)] transition-all hover:shadow-[0_14px_28px_-10px_rgba(79,70,229,0.75)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] disabled:cursor-not-allowed disabled:opacity-60 mt-3"
              >
                <span
                  aria-hidden
                  className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/20 to-white/0 opacity-0 transition-opacity group-hover:opacity-100"
                />
                <Sparkles className="relative h-4 w-4" aria-hidden />
                <span className="relative">{t('auth.login.agentosButton')}</span>
                <ArrowRight className="relative h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
              </button>
            )}
            <button
              type="button"
              disabled={exchanging}
              onClick={() => startOAuth('github')}
              className={`mt-3 group relative inline-flex h-12 w-full items-center justify-center gap-2 overflow-hidden rounded-xl px-5 text-[15px] font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] disabled:cursor-not-allowed disabled:opacity-60 ${
                fromStar
                  ? 'bg-gradient-to-r from-[#1E54F9] to-[#852EFE] text-white shadow-[0_10px_24px_-10px_rgba(79,70,229,0.65)] hover:shadow-[0_14px_28px_-10px_rgba(79,70,229,0.75)]'
                  : 'border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50'
              }`}
            >
              {fromStar ? (
                <span
                  aria-hidden
                  className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/20 to-white/0 opacity-0 transition-opacity group-hover:opacity-100"
                />
              ) : null}
              <Sparkles className="relative h-4 w-4" aria-hidden />
              <span className="relative">{t('auth.login.githubButton')}</span>
              <ArrowRight className="relative h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
            </button>

            <div className="mt-5 flex items-start gap-2 rounded-xl bg-slate-50/80 px-3 py-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
              <p className="text-[12px] leading-relaxed text-slate-500">
                {t('auth.login.hintOAuthSession')}
              </p>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
