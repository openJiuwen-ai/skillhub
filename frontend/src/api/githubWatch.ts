// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import { getApiClient } from './client'
import { API_ENDPOINTS } from './config'

type GithubWatchEnvelope<T> = { code: number; message: string; data: T }

function apiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const payload = err.response?.data as { message?: string; detail?: string | { message?: string } }
    if (payload?.message) return String(payload.message)
    const d = payload?.detail
    if (typeof d === 'string') return d
    if (d && typeof d === 'object' && 'message' in d && d.message != null) return String(d.message)
    if (err.message) return err.message
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

/** GitHub 代理调用错误：携带 HTTP 状态码，便于前端区分 401/403/429 等场景 */
export class GithubWatchError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'GithubWatchError'
    this.status = status
  }
}

/** 一键标星全部 openJiuwen-ai 组织仓库（POST /github/watch 不传 repos）。
 *  后端 fire-and-forget：立即返回 202，后台串行标星（≈20s），前端无需等待。
 *  实际标星结果由 GET /watch/status 查询 Redis 状态决定，本函数无返回值。 */
export async function starAllRepos(): Promise<void> {
  const client = getApiClient()
  try {
    const { data } = await client.post<GithubWatchEnvelope<{ status: string }>>(
      API_ENDPOINTS.GITHUB.WATCH,
      { repos: [] },
    )
    if (data.code !== 202 && data.code !== 200) {
      throw new GithubWatchError(data.message || '标星失败', data.code)
    }
  } catch (e) {
    if (e instanceof GithubWatchError) throw e
    const status = axios.isAxiosError(e) ? e.response?.status : undefined
    throw new GithubWatchError(apiErrorMessage(e, '标星失败'), status)
  }
}

/** 查询当前用户是否已标星（GET /github/watch/status，状态存 Redis，跨设备同步） */
export async function getStarStatus(): Promise<boolean> {
  const client = getApiClient()
  try {
    const { data } = await client.get<GithubWatchEnvelope<{ starred: boolean }>>(
      API_ENDPOINTS.GITHUB.WATCH_STATUS,
    )
    if (data.code !== 200 || !data.data) {
      throw new GithubWatchError(data.message || '查询标星状态失败', data.code)
    }
    return Boolean(data.data.starred)
  } catch (e) {
    if (e instanceof GithubWatchError) throw e
    const status = axios.isAxiosError(e) ? e.response?.status : undefined
    throw new GithubWatchError(apiErrorMessage(e, '查询标星状态失败'), status)
  }
}
