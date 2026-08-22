// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

const TOKEN_KEY = 'marketplace_oauth_access_token'
const USER_KEY = 'marketplace_oauth_user'
const PROVIDER_KEY = 'marketplace_oauth_provider'

export type OAuthProvider = 'gitcode' | 'github' | 'agentos'

export type OAuthUser = {
  id: string
  name: string
  login: string
  avatar_url?: string | null
  /** GET /auth/me：是否在服务端配置的审核管理员列表中 */
  is_market_moderation_admin?: boolean
}

export type GitCodeUser = OAuthUser

export function getStoredOAuthToken(): string | null {
  try {
    const t = sessionStorage.getItem(TOKEN_KEY)
    return t && t.trim() ? t.trim() : null
  } catch {
    return null
  }
}

export function getStoredOAuthUser(): OAuthUser | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY)
    if (!raw) return null
    const u = JSON.parse(raw) as OAuthUser
    if (!u || typeof u.id !== 'string') return null
    return u
  } catch {
    return null
  }
}

export function getStoredOAuthProvider(): OAuthProvider {
  try {
    const p = (sessionStorage.getItem(PROVIDER_KEY) || '').trim().toLowerCase()
    if (p === 'github') return 'github'
    if (p === 'agentos') return 'agentos'
    return 'gitcode'
  } catch {
    return 'gitcode'
  }
}

export function setOAuthSession(token: string, user: OAuthUser, provider: OAuthProvider): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
  sessionStorage.setItem(PROVIDER_KEY, provider)
}

export function clearOAuthSession(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
  sessionStorage.removeItem(PROVIDER_KEY)
}

// Backward-compatible aliases
export const getStoredGitCodeToken = getStoredOAuthToken
export const getStoredGitCodeUser = getStoredOAuthUser
export const setGitCodeSession = (token: string, user: GitCodeUser): void => setOAuthSession(token, user, 'gitcode')
export const clearGitCodeSession = clearOAuthSession
