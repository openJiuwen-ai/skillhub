// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import { API_CONFIG } from './config'
import type { OAuthProvider, OAuthUser } from '@/auth/gitcodeStorage'

/** 避免 StrictMode 下重复兑换；统一支持 gitcode / github */
export const OAUTH_PENDING_KEY = '__marketplace_oauth_pending'
export const OAUTH_ACTIVE_PROVIDER_KEY = '__marketplace_oauth_active_provider'

export type OAuthSessionData = {
  provider: OAuthProvider
  access_token: string
  token_type?: string
  user: OAuthUser
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
    if (!d || (d.provider !== 'gitcode' && d.provider !== 'github' && d.provider !== 'agentos') || !d.session) {
      return null
    }
    return d
  } catch {
    return null
  }
}

export function getOAuthStartUrl(provider: OAuthProvider): string {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  return `${base}/auth/oauth/${provider}/start`
}

export async function exchangeOAuthSession(provider: OAuthProvider, sessionId: string): Promise<OAuthSessionData> {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  const url = `${base}/auth/oauth/${provider}/session`
  const { data } = await axios.post<{ code: number; message: string; data: OAuthSessionData }>(
    url,
    { session: sessionId },
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
