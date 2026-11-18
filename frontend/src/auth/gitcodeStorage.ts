// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

const TOKEN_KEY = 'marketplace_oauth_access_token'
const USER_KEY = 'marketplace_oauth_user'
const PROVIDER_KEY = 'marketplace_oauth_provider'
const LOGOUT_KEY = 'marketplace_oidc_logout'

export type OAuthProvider = 'gitcode' | 'github' | 'agentos' | 'keycloak'

export type OAuthUser = {
  id: string
  name: string
  login: string
  avatar_url?: string | null
  /** GET /auth/me：是否在服务端配置的审核管理员列表中 */
  is_market_moderation_admin?: boolean
}

export type GitCodeUser = OAuthUser

export type OAuthLogoutInfo = {
  end_session_endpoint: string
  client_id: string
  post_logout_redirect_uri: string
  id_token_hint: string
}

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
    if (p === 'keycloak') return 'keycloak'
    return 'gitcode'
  } catch {
    return 'gitcode'
  }
}

export function getStoredOAuthLogoutInfo(): OAuthLogoutInfo | null {
  try {
    const raw = sessionStorage.getItem(LOGOUT_KEY)
    if (!raw) return null
    const info = JSON.parse(raw) as OAuthLogoutInfo
    if (
      !info ||
      typeof info.end_session_endpoint !== 'string' ||
      typeof info.client_id !== 'string' ||
      typeof info.post_logout_redirect_uri !== 'string' ||
      typeof info.id_token_hint !== 'string'
    ) return null
    return info
  } catch {
    return null
  }
}

export function setOAuthSession(
  token: string,
  user: OAuthUser,
  provider: OAuthProvider,
  logoutInfo?: OAuthLogoutInfo,
): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
  sessionStorage.setItem(PROVIDER_KEY, provider)
  if (provider === 'keycloak' && logoutInfo) {
    sessionStorage.setItem(LOGOUT_KEY, JSON.stringify(logoutInfo))
  } else {
    sessionStorage.removeItem(LOGOUT_KEY)
  }
}

export function updateStoredOAuthUser(user: OAuthUser): void {
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearOAuthSession(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
  sessionStorage.removeItem(PROVIDER_KEY)
  sessionStorage.removeItem(LOGOUT_KEY)
}

// Backward-compatible aliases
export const getStoredGitCodeToken = getStoredOAuthToken
export const getStoredGitCodeUser = getStoredOAuthUser
export const setGitCodeSession = (token: string, user: GitCodeUser): void => setOAuthSession(token, user, 'gitcode')
export const clearGitCodeSession = clearOAuthSession
