// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import axios from 'axios'
import {
  clearOAuthSession,
  getStoredOAuthLogoutInfo,
  getStoredOAuthProvider,
  getStoredOAuthToken,
  getStoredOAuthUser,
  setOAuthSession,
  updateStoredOAuthUser,
  type OAuthLogoutInfo,
  type OAuthProvider,
  type OAuthUser,
} from './gitcodeStorage'
import { fetchOAuthMe } from '@/api/auth'

type GitCodeAuthState = {
  token: string | null
  user: OAuthUser | null
  provider: OAuthProvider
  isAuthenticated: boolean
  /** Skill 上架审核管理员（配置文件用户名，与 /auth/me 一致） */
  isMarketModerationAdmin: boolean
  login: (token: string, user: OAuthUser, provider: OAuthProvider, logoutInfo?: OAuthLogoutInfo) => void
  logout: () => boolean
}

const GitCodeAuthContext = createContext<GitCodeAuthState | null>(null)

export function GitCodeAuthProvider({ children }: { children: ReactNode }) {
  /**
   * 首帧即读 sessionStorage，避免刷新 /profile 等页时先渲染「未登录」、误跳 /login，
   * 再在登录页被当成已登录却未写入 postLoginRedirect 而落到默认「/」市场首页。
   */
  const [token, setToken] = useState<string | null>(() => getStoredOAuthToken())
  const [user, setUser] = useState<OAuthUser | null>(() => getStoredOAuthUser())
  const [provider, setProvider] = useState<OAuthProvider>(() => getStoredOAuthProvider())

  /**
   * 避免 React.StrictMode（dev）双挂载导致 /auth/me 请求两次。
   * useRef 在 StrictMode 的 mount → 模拟 unmount → 再 mount 过程中保留值，
   * 第二次 effect 执行时通过 guard 直接跳过；不做 abort，让请求自然完成。
   */
  const didRefreshRef = useRef(false)

  useEffect(() => {
    if (didRefreshRef.current) return
    didRefreshRef.current = true

    const t = getStoredOAuthToken()
    const u = getStoredOAuthUser()
    const p = getStoredOAuthProvider()
    setToken(t)
    setUser(u)
    setProvider(p)
    if (!t) return

    fetchOAuthMe(t, p)
      .then(profile => {
        setUser(profile)
        updateStoredOAuthUser(profile)
      })
      .catch(err => {
        if (axios.isCancel(err)) return
        const name = (err && (err.name as string)) || ''
        if (name === 'CanceledError' || name === 'AbortError') return
        clearOAuthSession()
        setToken(null)
        setUser(null)
        setProvider('gitcode')
      })
  }, [])

  const login = useCallback((t: string, u: OAuthUser, p: OAuthProvider, logoutInfo?: OAuthLogoutInfo) => {
    setOAuthSession(t, u, p, logoutInfo)
    setToken(t)
    setUser(u)
    setProvider(p)
  }, [])

  const logout = useCallback(() => {
    const provider = getStoredOAuthProvider()
    const logoutInfo = getStoredOAuthLogoutInfo()
    clearOAuthSession()
    setToken(null)
    setUser(null)
    setProvider('gitcode')

    if (provider !== 'keycloak' || !logoutInfo) return false
    try {
      const endpoint = new URL(logoutInfo.end_session_endpoint)
      if (endpoint.protocol !== 'https:') return false
      const form = document.createElement('form')
      form.method = 'POST'
      form.action = endpoint.toString()
      form.hidden = true
      for (const [name, value] of Object.entries({
        client_id: logoutInfo.client_id,
        id_token_hint: logoutInfo.id_token_hint,
        post_logout_redirect_uri: logoutInfo.post_logout_redirect_uri,
      })) {
        const input = document.createElement('input')
        input.type = 'hidden'
        input.name = name
        input.value = value
        form.appendChild(input)
      }
      document.body.appendChild(form)
      form.submit()
      return true
    } catch {
      // Local logout has already completed; do not keep the user signed into SkillHub.
      return false
    }
  }, [])

  const value = useMemo(
    () => ({
      token,
      user,
      provider,
      isAuthenticated: Boolean(token),
      isMarketModerationAdmin: Boolean(user?.is_market_moderation_admin),
      login,
      logout,
    }),
    [token, user, provider, login, logout],
  )

  return <GitCodeAuthContext.Provider value={value}>{children}</GitCodeAuthContext.Provider>
}

export function useGitCodeAuth(): GitCodeAuthState {
  const ctx = useContext(GitCodeAuthContext)
  if (!ctx) {
    throw new Error('useGitCodeAuth must be used within GitCodeAuthProvider')
  }
  return ctx
}
