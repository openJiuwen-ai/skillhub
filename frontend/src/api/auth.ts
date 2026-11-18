// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import { sha256 } from '@noble/hashes/sha2'
import { API_CONFIG } from './config'
import type { OAuthLogoutInfo, OAuthProvider, OAuthUser } from '@/auth/gitcodeStorage'

/** 避免 StrictMode 下重复兑换；统一支持 gitcode / github */
export const OAUTH_PENDING_KEY = '__marketplace_oauth_pending'
export const OAUTH_ACTIVE_PROVIDER_KEY = '__marketplace_oauth_active_provider'

const KEYCLOAK_VERIFIER_KEY = '__marketplace_keycloak_verifier'

export type OAuthSessionData = {
  provider: OAuthProvider
  access_token: string
  token_type?: string
  user: OAuthUser
  logout?: OAuthLogoutInfo
}

type OAuthPendingPayload = {
  provider: OAuthProvider
  session: string
}

export function stringifyOAuthPending(payload: OAuthPendingPayload): string {
  return JSON.stringify(payload)
}

export function parseOAuthPending(raw: string): OAuthPendingPayload | null {
  try {
    const d = JSON.parse(raw) as OAuthPendingPayload
    if (!d || (d.provider !== 'gitcode' && d.provider !== 'github' && d.provider !== 'agentos' && d.provider !== 'keycloak') || !d.session) {
      return null
    }
    return d
  } catch {
    return null
  }
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function getOAuthStartUrl(provider: OAuthProvider): string {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  const url = `${base}/auth/oauth/${provider}/start`
  if (provider !== 'keycloak') return url
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const challenge = base64url(sha256(new TextEncoder().encode(verifier)))
  sessionStorage.setItem(KEYCLOAK_VERIFIER_KEY, verifier)
  return `${url}?browser_challenge=${challenge}`
}

export async function exchangeOAuthSession(provider: OAuthProvider, sessionId: string): Promise<OAuthSessionData> {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  const url = `${base}/auth/oauth/${provider}/session`
  const body: { session: string; browser_verifier?: string } = { session: sessionId }
  if (provider === 'keycloak') {
    const verifier = sessionStorage.getItem(KEYCLOAK_VERIFIER_KEY)
    sessionStorage.removeItem(KEYCLOAK_VERIFIER_KEY)
    if (!verifier) throw new Error('Please restart Keycloak login in this tab.')
    body.browser_verifier = verifier
  }
  const { data } = await axios.post<{ code: number; message: string; data: OAuthSessionData }>(
    url,
    body,
    {
      timeout: API_CONFIG.TIMEOUT,
      headers: API_CONFIG.HEADERS,
    },
  )
  if (data.code !== 200 || !data.data?.access_token || !data.data.user) {
    throw new Error(data.message || 'OAuth session exchange failed')
  }
  if (data.data.provider !== provider) {
    throw new Error('OAuth provider mismatch')
  }
  return data.data
}

export async function fetchOAuthMe(accessToken: string, provider: OAuthProvider): Promise<OAuthUser> {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  const url = `${base}/auth/me`
  const { data } = await axios.get<{ code: number; message: string; data: OAuthUser }>(url, {
    headers: {
      ...API_CONFIG.HEADERS,
      Authorization: `Bearer ${accessToken}`,
      'X-OAuth-Provider': provider,
    },
    timeout: API_CONFIG.TIMEOUT,
  })
  if (data.code !== 200 || !data.data?.id) {
    throw new Error(data.message || 'auth/me failed')
  }
  return data.data
}
