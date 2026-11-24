/** Shared extension gateway transport; protocol-specific work stays on the backend. */
import axios from 'axios'
import { API_CONFIG } from '../api/config'
import type { OAuthSessionData } from '../api/auth'
import type { ExtensionProvider } from './providers'

function extensionUrl(provider: ExtensionProvider, action: 'start' | 'session'): string {
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  return `${base}/auth/extensions/${encodeURIComponent(provider)}/${action}`
}

export function getExtensionStartUrl(provider: ExtensionProvider): string {
  return extensionUrl(provider, 'start')
}

export async function exchangeExtensionSession(
  provider: ExtensionProvider,
  sessionId: string,
): Promise<OAuthSessionData> {
  const { data } = await axios.post<{ code: number; message: string; data: OAuthSessionData }>(
    extensionUrl(provider, 'session'),
    { session: sessionId },
    { timeout: API_CONFIG.TIMEOUT, headers: API_CONFIG.HEADERS },
  )
  if (data.code !== 200 || !data.data?.access_token || !data.data.user) {
    throw new Error(data.message || 'OAuth session exchange failed')
  }
  if (data.data.provider !== provider) {
    throw new Error('OAuth provider mismatch')
  }
  return data.data
}
