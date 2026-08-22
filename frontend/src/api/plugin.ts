// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import { getStoredGitCodeToken, getStoredOAuthProvider } from '@/auth/gitcodeStorage'
import { getApiClient } from './client'
import { API_CONFIG, API_ENDPOINTS } from './config'

export type MarketplacePluginOrderBy =
  | 'install_count'
  | 'like_count'
  | 'view_count'
  | 'create_time'
  | 'update_time'
  | 'review_count'
  | 'recommend'

export interface MarketplacePluginListRequest {
  page?: number
  page_size?: number
  search_keyword?: string
  /** 与后端 `publisher_id` 一致：筛选指定发布者的插件 */
  publisher_id?: string
  /** 与后端 `asset_id` 一致 */
  asset_id?: string
  /** 与后端 Query 一致：`plugin_type`（如 tools / mcp-stdio / restful-api / skill） */
  plugin_type?: string
  /** 与后端一致：PENDING | APPROVED | REJECTED，常配合 plugin_type=skill */
  moderation_status?: string
  /** 与后端 `plugin_type_exclude`：排除某类型（如与插件列表中排除 skill） */
  plugin_type_exclude?: string
  /** 与后端 `category_id`：按类别筛选（如 software-development / office-productivity） */
  category_id?: string
  /** 与后端 `tags`：逗号分隔多标签精确过滤 */
  tags?: string
  /** 与后端 `tags_match`：all=同时包含全部标签，any=包含任一标签 */
  tags_match?: 'all' | 'any'
  order_by?: MarketplacePluginOrderBy
  desc?: boolean
}

export interface MarketplacePluginItem {
  asset_id: string
  asset_type: string
  name: string
  display_name?: string | null
  /** 部分网关 / 服务可能返回 camelCase */
  displayName?: string | null
  short_desc?: string | null
  shortDesc?: string | null
  detail_desc?: string | null
  detailDesc?: string | null
  icon_uri?: string | null
  publisher_id: string
  publisher_name: string
  tags?: string[] | null
  certification?: string | null
  plugin_type?: string | null
  visibility?: 'public' | 'private' | string | null
  publish_result?: 'reviewing' | 'pending_moderation' | 'publish_success' | 'publish_failed' | string | null
  /** 旧字段名，仅作兼容 */
  run_time?: string | null
  latest_version?: string | null
  /** 对外可展示的已通过审最新版本；他人列表与未指定版本的下载会用它 */
  public_latest_version?: string | null
  /** GET /plugins 列表：当前用户可见的版本号；他人仅含已通过审版本 */
  all_versions?: string[] | null
  /** 仍有版本在审核中（作者/审核员在列表中可见，用于个人中心状态） */
  has_pending_skill_version?: boolean
  /** Skill：仅发布者/审核员；版本号 -> 审核状态，用于版本下拉展示 */
  skill_version_moderation?: Record<string, string> | null
  /** Skill：仅发布者/审核员；版本号 -> 发布结果，用于版本下拉展示 */
  skill_version_publish_result?: Record<string, string> | null
  view_count: number
  install_count: number
  like_count: number
  star_count?: number
  review_count: number
  average_rating: number
  create_time?: number | null
  update_time?: number | null
  createTime?: number | null
  updateTime?: number | null
  /** 置顶顺序：非空表示置顶，数字越小越靠前 */
  pin_order?: number | null
  pinOrder?: number | null
  /** Skill 审核：PENDING | APPROVED | REJECTED */
  moderation_status?: string | null
  moderation_reject_reason?: string | null
  /** 服务端根据当前登录态计算，优先用于展示审核按钮 */
  viewer_is_market_moderation_admin?: boolean
  access_source?: 'public' | 'owner' | 'group' | 'admin' | string | null
  /** Git 且无 SKILL 声明版本时，列表 latest 行展示 commit 短码（与 latest_version 对齐） */
  git_version_display_as_commit?: boolean
  resolved_commit_sha?: string | null
  declared_skill_version?: string | null
  storage_mode?: string | null
}

export interface UserInteractionState {
  liked: boolean
  starred: boolean
  like_count: number | null
  star_count: number | null
}

/** 批量交互接口保证返回数值计数（不会为 null）。 */
export interface AssetInteractionState {
  asset_id: string
  liked: boolean
  starred: boolean
  like_count: number
  star_count: number
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface InteractionToggleResult {
  action_type: 'like' | 'star'
  active: boolean
  like_count?: number | null
  star_count?: number | null
}

export interface MarketplacePluginListData {
  page: number
  page_size: number
  total: number
  items: MarketplacePluginItem[]
}

export interface MarketplacePluginListResponse {
  code: number
  message: string
  data: MarketplacePluginListData
}

/** GET /plugins/tags 返回的标签选项 */
export interface PluginTagOption {
  tag: string
  count: number
}

export interface PluginTagOptionsResponse {
  code: number
  message: string
  data: PluginTagOption[]
}

/** 拉取市场标签筛选选项：热门标签自动推荐 + 运营配置优先展示 */
export async function getPluginTagOptions(
  request: { plugin_type?: string; limit?: number; keyword?: string } = {}
): Promise<PluginTagOption[]> {
  const client = getApiClient()
  const { data } = await client.get<PluginTagOptionsResponse>(API_ENDPOINTS.PLUGINS.TAGS, {
    params: {
      plugin_type: request.plugin_type || undefined,
      limit: request.limit ?? 20,
      keyword: request.keyword || undefined,
    },
  })
  if (data == null || typeof data !== 'object') {
    throw new MarketplaceApiError('标签选项响应无效')
  }
  if (data.code !== 200 || !Array.isArray(data.data)) {
    throw new MarketplaceApiError(data.message || '标签选项拉取失败', data.code)
  }
  return data.data
}

/** GET /api/v1/artifacts/{id} 响应 data */
export interface PluginDownloadData {
  download_url: string
  asset_id: string
  name: string
  version: string
  file_size: number
  checksum_sha256: string
}

export interface PluginDownloadResponse {
  code: number
  message: string
  data: PluginDownloadData
}

function apiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const payload = err.response?.data as {
      message?: string
      detail?: string | { message?: string }
    }
    if (payload?.message) return String(payload.message)
    const d = payload?.detail
    if (typeof d === 'string') return d
    if (d && typeof d === 'object' && 'message' in d && d.message != null) return String(d.message)
    if (err.message) return err.message
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

/** Request download metadata (public URL); server increments install count. */
export async function getPluginArtifactDownload(assetId: string, version?: string): Promise<PluginDownloadData> {
  const client = getApiClient()
  const v = version?.trim()
  try {
    const { data } = await client.get<PluginDownloadResponse>(API_ENDPOINTS.ARTIFACTS.download(assetId), {
      params: v ? { version: v } : undefined,
    })
    if (data.code !== 200 || !data.data?.download_url) {
      throw new Error(data.message || 'Download failed')
    }
    return data.data
  } catch (e) {
    throw new Error(apiErrorMessage(e, 'Download failed'))
  }
}

export class MarketplaceApiError extends Error {
  readonly code?: number
  readonly errorType?: string

  constructor(message: string, code?: number, errorType?: string) {
    super(message)
    this.name = 'MarketplaceApiError'
    this.code = code
    this.errorType = errorType
  }
}

/** POST /git-sources 返回 409 且 error=git_repo_already_registered 时抛出，便于前端走 i18n。 */
export class GitSourceDuplicateError extends Error {
  constructor() {
    super('git_repo_already_registered')
    this.name = 'GitSourceDuplicateError'
  }
}

/** DELETE /git-sources/{id} 业务拒绝时抛出，便于前端展示 i18n 说明。 */
export class GitSourceDeleteError extends Error {
  readonly reason: 'git_source_sync_in_progress' | 'git_source_cascade_delete_partial'
  readonly deletedSkillCount?: number
  readonly failedSkillCount?: number

  constructor(
    reason: 'git_source_sync_in_progress' | 'git_source_cascade_delete_partial',
    opts?: { deletedSkillCount?: number; failedSkillCount?: number },
  ) {
    super(reason)
    this.name = 'GitSourceDeleteError'
    this.reason = reason
    this.deletedSkillCount = opts?.deletedSkillCount
    this.failedSkillCount = opts?.failedSkillCount
  }
}

/** GET /plugins/audit/skill-moderation 列表项（审核员本人的审核审计） */
export interface SkillModerationAuditItem {
  event_id: string
  asset_id: string
  skill_name: string
  skill_display_name?: string | null
  version: string
  moderation_action: 'APPROVE' | 'REJECT'
  reject_reason?: string | null
  created_at_ms: number
}

export interface SkillModerationAuditListData {
  page: number
  page_size: number
  total: number
  items: SkillModerationAuditItem[]
}

export interface SkillModerationAuditListResponse {
  code: number
  message: string
  data: SkillModerationAuditListData
}

export async function getSkillModerationAuditHistory(request: {
  page?: number
  page_size?: number
}): Promise<SkillModerationAuditListResponse> {
  const client = getApiClient()
  const { data } = await client.get<SkillModerationAuditListResponse>(API_ENDPOINTS.PLUGINS.MODERATION_AUDIT, {
    params: {
      page: request.page ?? 1,
      page_size: request.page_size ?? 20,
    },
  })
  if (data == null || typeof data !== 'object') {
    throw new MarketplaceApiError('审核历史响应无效')
  }
  if (data.code !== 200) {
    throw new MarketplaceApiError(data.message || `审核历史加载失败（code ${data.code}）`, data.code)
  }
  const body = data.data
  if (body == null || typeof body !== 'object' || !Array.isArray(body.items)) {
    throw new MarketplaceApiError(data.message || '审核历史 data 无效')
  }
  return data
}

export async function postSkillModeration(
  assetId: string,
  body: { action: 'approve' | 'reject'; reason?: string; version?: string },
): Promise<SkillModerationResultData> {
  const client = getApiClient()
  try {
    const { data } = await client.post<SkillModerationResponse>(API_ENDPOINTS.PLUGINS.moderation(assetId), body)
    if (data.code !== 200 || !data.data?.asset_id) {
      throw new MarketplaceApiError(data.message || 'Moderation failed', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Moderation failed'))
  }
}

export async function getPlugins(
  request: MarketplacePluginListRequest = {}
): Promise<MarketplacePluginListResponse> {
  const client = getApiClient()
  const { data } = await client.get<MarketplacePluginListResponse>(API_ENDPOINTS.PLUGINS.LIST, {
    params: {
      page: request.page ?? 1,
      page_size: request.page_size ?? 20,
      search_keyword: request.search_keyword || undefined,
      publisher_id: request.publisher_id || undefined,
      asset_id: request.asset_id || undefined,
      plugin_type: request.plugin_type || undefined,
      moderation_status: request.moderation_status || undefined,
      plugin_type_exclude: request.plugin_type_exclude || undefined,
      category_id: request.category_id || undefined,
      tags: request.tags || undefined,
      tags_match: request.tags_match || undefined,
      order_by: request.order_by ?? 'install_count',
      desc: request.desc ?? true,
    },
  })

  if (data == null || typeof data !== 'object') {
    throw new MarketplaceApiError('插件列表响应无效')
  }
  if (data.code !== 200) {
    throw new MarketplaceApiError(data.message || `插件列表失败（code ${data.code}）`, data.code)
  }
  const body = data.data
  if (body == null || typeof body !== 'object') {
    throw new MarketplaceApiError(data.message || '插件列表 data 为空')
  }
  if (!Array.isArray(body.items)) {
    throw new MarketplaceApiError(data.message || '插件列表缺少 items')
  }

  return data
}

export async function getMyStars(request: { page?: number; page_size?: number } = {}): Promise<MarketplacePluginListResponse> {
  const client = getApiClient()
  const { data } = await client.get<MarketplacePluginListResponse>(API_ENDPOINTS.PLUGINS.MY_STARS, {
    params: {
      page: request.page ?? 1,
      page_size: request.page_size ?? 20,
    },
  })
  if (data == null || typeof data !== 'object') {
    throw new MarketplaceApiError('我的收藏响应无效')
  }
  if (data.code !== 200) {
    throw new MarketplaceApiError(data.message || `我的收藏加载失败（code ${data.code}）`, data.code)
  }
  const body = data.data
  if (body == null || typeof body !== 'object' || !Array.isArray(body.items)) {
    throw new MarketplaceApiError(data.message || '我的收藏 data 无效')
  }
  return data
}

export async function getMyLikes(request: { page?: number; page_size?: number } = {}): Promise<MarketplacePluginListResponse> {
  const client = getApiClient()
  const { data } = await client.get<MarketplacePluginListResponse>(API_ENDPOINTS.PLUGINS.MY_LIKES, {
    params: {
      page: request.page ?? 1,
      page_size: request.page_size ?? 20,
    },
  })
  if (data == null || typeof data !== 'object') {
    throw new MarketplaceApiError('我的点赞响应无效')
  }
  if (data.code !== 200) {
    throw new MarketplaceApiError(data.message || `我的点赞加载失败（code ${data.code}）`, data.code)
  }
  const body = data.data
  if (body == null || typeof body !== 'object' || !Array.isArray(body.items)) {
    throw new MarketplaceApiError(data.message || '我的点赞 data 无效')
  }
  return data
}

export async function getPluginInteractions(assetId: string): Promise<UserInteractionState> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<UserInteractionState>>(API_ENDPOINTS.PLUGINS.interactions(assetId))
    if (data.code !== 200 || !data.data) {
      throw new MarketplaceApiError(data.message || '获取交互状态失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, '获取交互状态失败'))
  }
}

export async function getPluginInteractionsBatch(assetIds: string[]): Promise<AssetInteractionState[]> {
  const ids = [...new Set(assetIds.map(x => x.trim()).filter(Boolean))]
  if (ids.length === 0) return []
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<{ items: AssetInteractionState[] }>>(
      API_ENDPOINTS.PLUGINS.interactionsBatch,
      { params: { asset_ids: ids } },
    )
    if (data.code !== 200 || !data.data?.items) {
      throw new MarketplaceApiError(data.message || '批量获取交互状态失败', data.code)
    }
    return data.data.items.map(item => ({
      asset_id: item.asset_id,
      liked: item.liked === true,
      starred: item.starred === true,
      like_count: Number.isFinite(Number(item.like_count)) ? Number(item.like_count) : 0,
      star_count: Number.isFinite(Number(item.star_count)) ? Number(item.star_count) : 0,
    }))
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, '批量获取交互状态失败'))
  }
}

export async function togglePluginInteract(
  assetId: string,
  actionType: 'like' | 'star',
): Promise<InteractionToggleResult> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<InteractionToggleResult>>(
      API_ENDPOINTS.PLUGINS.interact(assetId),
      { action_type: actionType },
    )
    if (data.code !== 200 || !data.data) {
      throw new MarketplaceApiError(data.message || '交互操作失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, '交互操作失败'))
  }
}

/** GET /api/v1/plugins/{asset_id}/versions/{version} 响应 data */
export interface PluginVersionDetailData {
  asset_id: string
  version: string
  asset_type: string
  plugin_type?: string | null
  name: string
  display_name: string
  short_desc?: string | null
  detail_desc?: string | null
  publisher_id: string
  publisher_name: string
  tags?: string[] | null
  certification?: string | null
  changelog?: string | null
  file_path?: string | null
  icon_uri?: string | null
  publish_result?: 'reviewing' | 'pending_moderation' | 'publish_success' | 'publish_failed' | string | null
  publish_failed_reason?: string | null
  review_status?: string | null
  review_failed_reason?: string | null
  review_summary?: Record<string, unknown> | null
  review_sections?: Array<Record<string, unknown>> | null
  semantic_review?: Record<string, unknown> | null
  review_mode?: string | null
  review_engine?: string | null
  model_name?: string | null
  trace_id?: string | null
  /** 资产累计下载次数；旧后端可能无此字段 */
  install_count?: number | null
  /** 资产累计浏览次数（版本详情成功返回时递增）；旧后端可能无此字段 */
  view_count?: number | null
  /** 最新版本对应版本记录的上传时间 create_time（毫秒）；旧后端可能无此字段 */
  update_time?: number | null
  moderation_status?: string | null
  moderation_reject_reason?: string | null
  /** 当前查看版本的审核状态（Skill 版本级） */
  version_moderation_status?: string | null
  version_moderation_reject_reason?: string | null
  viewer_is_market_moderation_admin?: boolean
  access_source?: 'public' | 'owner' | 'group' | 'admin' | string | null
  git_version_display_as_commit?: boolean
  resolved_commit_sha?: string | null
  declared_skill_version?: string | null
  storage_mode?: string | null
}

export interface SkillModerationResultData {
  asset_id: string
  moderation_status: string
  moderation_reject_reason?: string | null
  publish_result?: string | null
  /** 本次审核针对的版本 */
  version?: string | null
}

export interface SkillModerationResponse {
  code: number
  message: string
  data: SkillModerationResultData
}

export interface PluginVersionDetailResponse {
  code: number
  message: string
  data: PluginVersionDetailData
}

export function normalizeSkillLikeModerationStatus(raw: string | null | undefined): 'PENDING' | 'APPROVED' | 'REJECTED' {
  const value = (raw || 'APPROVED').toString().toUpperCase()
  if (value === 'PENDING' || value === 'REJECTED') return value
  return 'APPROVED'
}

function firstNonEmptyString(...candidates: Array<string | null | undefined>): string {
  for (const item of candidates) {
    if (item && item.trim()) return item.trim()
  }
  return ''
}

export function getSkillLikeEffectiveModeration(input: {
  moderation_status?: string | null
  moderation_reject_reason?: string | null
  version_moderation_status?: string | null
  version_moderation_reject_reason?: string | null
}): {
  moderationStatus: 'PENDING' | 'APPROVED' | 'REJECTED'
  moderationRejectReason: string
} {
  return {
    moderationStatus: normalizeSkillLikeModerationStatus(
      firstNonEmptyString(input.version_moderation_status, input.moderation_status),
    ),
    moderationRejectReason: firstNonEmptyString(
      input.version_moderation_reject_reason,
      input.moderation_reject_reason,
    ),
  }
}

export function getSkillLikeVersionModerationMap(item: MarketplacePluginItem | null | undefined): Record<string, string> {
  const map = item?.skill_version_moderation
  return map && typeof map === 'object' ? map : {}
}

export function getSkillLikeVersionPublishResultMap(item: MarketplacePluginItem | null | undefined): Record<string, string> {
  const map = item?.skill_version_publish_result
  return map && typeof map === 'object' ? map : {}
}

export async function getPluginVersionDetail(
  assetId: string,
  version: string,
  options?: { signal?: AbortSignal },
): Promise<PluginVersionDetailData> {
  const client = getApiClient()
  const { data } = await client.get<PluginVersionDetailResponse>(
    API_ENDPOINTS.PLUGINS.versionDetail(assetId, version),
    { signal: options?.signal },
  )
  if (data.code !== 200 || !data.data?.asset_id) {
    throw new MarketplaceApiError(data.message || '插件版本详情失败', data.code)
  }
  return data.data
}

export interface PluginVersionDeleteResult {
  asset_id: string
  version: string
}

export interface PluginVersionDeleteResponse {
  code: number
  message: string
  data: PluginVersionDeleteResult
}

/** GET /api/v1/plugins/publish-template 响应 data */
export interface PluginTemplatePresignData {
  download_url: string
  expires_in: number
  filename: string
}

export interface PluginTemplatePresignResponse {
  code: number
  message: string
  data: PluginTemplatePresignData
}

/**
 * 获取发布页模板 zip 的预签名下载 URL（私有桶对象，需登录 Bearer）。
 */
export async function getPublishTemplatePresigned(options?: { kind?: 'plugin' | 'skill' | 'swarmskill' }): Promise<PluginTemplatePresignData> {
  const token = getStoredGitCodeToken()
  if (!token) {
    throw new Error('请先登录后再下载模板')
  }
  const client = getApiClient()
  const kind = options?.kind === 'skill' || options?.kind === 'swarmskill' ? options.kind : undefined
  try {
    const { data } = await client.get<PluginTemplatePresignResponse>(API_ENDPOINTS.PLUGINS.PUBLISH_TEMPLATE, {
      params: kind ? { kind } : undefined,
    })
    if (data.code !== 200 || !data.data?.download_url) {
      throw new MarketplaceApiError(data.message || '获取模板链接失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(publishErrorMessage(e, '获取模板链接失败'))
  }
}

/** POST /api/v1/plugins 成功时 data */
export interface PluginPublishResultData {
  plugin_id: string
  name: string
  version: string
  status: string
  published_at: string
  storage_url: string
  publish_result?: 'reviewing' | 'pending_moderation' | 'publish_success' | 'publish_failed' | string | null
  visibility?: 'public' | 'private' | string | null
}

export interface PluginPublishResponse {
  code: number
  message: string
  data: PluginPublishResultData
}

function publishErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const payload = err.response?.data as {
      message?: string
      detail?: string | { message?: string }
    }
    if (payload?.message) return String(payload.message)
    const d = payload?.detail
    if (typeof d === 'string') return d
    if (d && typeof d === 'object' && 'message' in d && d.message != null) return String(d.message)
    if (err.message) return err.message
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

/**
 * POST /api/v1/plugins，multipart/form-data。
 * 使用独立 axios 请求，避免带默认 `Content-Type: application/json` 的实例破坏 multipart。
 */
export async function publishPlugin(params: {
  file: File
  checksumSha256Hex: string
  pluginId?: string
  pluginVersion?: string
  versionDesc?: string
  force?: boolean
  visibility?: 'public' | 'private'
}): Promise<PluginPublishResultData> {
  const token = getStoredGitCodeToken()
  const provider = getStoredOAuthProvider()
  if (!token) {
    throw new Error('请先登录后再发布插件')
  }
  const base = (API_CONFIG.BASE_URL || '/api/v1').replace(/\/$/, '')
  const form = new FormData()
  form.append('file', params.file)
  if (params.pluginId?.trim()) form.append('plugin_id', params.pluginId.trim())
  if (params.pluginVersion?.trim()) form.append('plugin_version', params.pluginVersion.trim())
  if (params.versionDesc?.trim()) form.append('version_desc', params.versionDesc.trim())
  if (params.force) form.append('force', 'true')
  if (params.visibility) form.append('visibility', params.visibility)

  try {
    const { data } = await axios.post<PluginPublishResponse>(`${base}${API_ENDPOINTS.PLUGINS.LIST}`, form, {
      headers: {
        Authorization: `Bearer ${token}`,
        'X-OAuth-Provider': provider,
        'X-Checksum-SHA256': params.checksumSha256Hex.toLowerCase(),
      },
      timeout: API_CONFIG.TIMEOUT,
    })
    if (data.code !== 200 || !data.data?.plugin_id) {
      throw new MarketplaceApiError(data.message || '发布失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    if (axios.isAxiosError(e)) {
      const detail = (e.response?.data as { detail?: { message?: string; error?: string } })?.detail
      const msg = detail?.message || e.message || '发布失败'
      const errorType = detail?.error
      throw new MarketplaceApiError(msg, e.response?.status, errorType)
    }
    throw new Error(publishErrorMessage(e, '发布失败'))
  }
}

/** 版本文件列表项 */
export interface VersionFileEntry {
  path: string
  size: number
}

/** GET /api/v1/plugins/{asset_id}/versions/{version}/files 响应 data */
export interface VersionFilesData {
  files: VersionFileEntry[]
  /** with_content 文件的文本内容；未请求或二进制时为 null */
  content: string | null
  /** 实际返回内容的文件路径 */
  content_path: string | null
}

/** GET /api/v1/plugins/{asset_id}/versions/{version}/files?with_content=<path> */
export async function getVersionFileList(
  assetId: string,
  version: string,
  options?: { withContent?: string; signal?: AbortSignal },
): Promise<VersionFilesData> {
  const client = getApiClient()
  const { data } = await client.get<ApiResponse<VersionFilesData>>(
    API_ENDPOINTS.PLUGINS.versionFiles(assetId, version),
    {
      params: options?.withContent ? { with_content: options.withContent } : undefined,
      signal: options?.signal,
    },
  )
  if (data.code !== 200 || !data.data) {
    throw new MarketplaceApiError(data.message || '获取文件列表失败', data.code)
  }
  return data.data
}

/** DELETE /api/v1/plugins/{asset_id}/versions/{version} — 需 Bearer；删除指定版本（非字面量 `all`） */
export async function deletePluginVersion(assetId: string, version: string): Promise<PluginVersionDeleteResult> {
  const v = version.trim()
  if (!v || v.toLowerCase() === 'all') {
    throw new Error('Invalid version for single-version delete')
  }
  const client = getApiClient()
  try {
    const { data } = await client.delete<PluginVersionDeleteResponse>(
      API_ENDPOINTS.PLUGINS.versionDetail(assetId, v),
    )
    if (data.code !== 200 || !data.data?.asset_id) {
      throw new MarketplaceApiError(data.message || '删除失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, '删除失败'))
  }
}

/** DELETE /api/v1/plugins/{asset_id}/versions/all — 需 Bearer，删除资产及全部版本 */
export async function deletePluginAllVersions(assetId: string): Promise<PluginVersionDeleteResult> {
  const client = getApiClient()
  try {
    const { data } = await client.delete<PluginVersionDeleteResponse>(
      API_ENDPOINTS.PLUGINS.versionDetail(assetId, 'all'),
    )
    if (data.code !== 200 || !data.data?.asset_id) {
      throw new MarketplaceApiError(data.message || '删除失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, '删除失败'))
  }
}

// ----- Git 源批量接入 -----

export interface GitSourceItemDto {
  id: string
  name: string
  repo_url: string
  ref: string
  skills_subpath?: string | null
  /** 与库表 uk_git_source_dedup_key 一致；列表合并时优先按此分组 */
  git_source_dedup_key?: string | null
  /** 部分网关返回 camelCase */
  gitSourceDedupKey?: string | null
  created_by_user_id: string
  create_time_ms: number
  update_time_ms: number
  last_index_status?: string | null
  last_index_error?: string | null
  last_indexed_at_ms?: number | null
}

export interface SkillImportItemResultDto {
  entry: string
  status: 'ok' | 'error' | 'skipped'
  plugin_id?: string | null
  name?: string | null
  version?: string | null
  error?: string | null
  message?: string | null
}

export interface GitSyncAcceptedResponseData {
  source_id: string
  status: string
  message: string
}

export interface GitSyncRunResponseData {
  source_id: string
  resolved_commit_sha: string
  skill_import: {
    summary: {
      total: number
      ok: number
      failed: number
      skipped: number
    }
    results: SkillImportItemResultDto[]
  }
}

interface GitSourceListApiResponse {
  code: number
  message: string
  data: { items: GitSourceItemDto[] }
}

interface GitSyncAcceptedApiResponse {
  code: number
  message: string
  data: GitSyncAcceptedResponseData
}

export async function listMyGitSources(): Promise<GitSourceListApiResponse['data']> {
  const client = getApiClient()
  const { data } = await client.get<GitSourceListApiResponse>(API_ENDPOINTS.PLUGINS.GIT_SOURCES)
  if (data.code !== 200 || !data.data) {
    throw new MarketplaceApiError(data.message || '获取 Git 源失败', data.code)
  }
  return data.data
}

export async function createGitSourceAndSync(params: {
  repo_url: string
  ref?: string
  skills_subpath?: string
  fail_fast?: boolean
}): Promise<GitSyncAcceptedResponseData> {
  const client = getApiClient()
  try {
    const { data } = await client.post<GitSyncAcceptedApiResponse>(
      API_ENDPOINTS.PLUGINS.GIT_SOURCES,
      {
        // 显式传空串：兼容仍把 name 标为必填的旧后端（缺键会 422 Field required）
        name: '',
        repo_url: params.repo_url,
        ref: params.ref ?? 'main',
        skills_subpath: params.skills_subpath,
      },
      {
        params: params.fail_fast ? { fail_fast: true } : undefined,
        timeout: API_CONFIG.GIT_SYNC_ACCEPT_TIMEOUT,
      },
    )
    if (data.code !== 200 || !data.data?.source_id) {
      throw new MarketplaceApiError(data.message || 'Git 同步失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    if (axios.isAxiosError(e)) {
      const detail = (e.response?.data as { detail?: { message?: string; error?: string } })?.detail
      if (typeof detail === 'object' && detail?.error === 'git_repo_already_registered') {
        throw new GitSourceDuplicateError()
      }
      const msg = typeof detail === 'object' && detail?.message ? String(detail.message) : apiErrorMessage(e, 'Git 同步失败')
      throw new Error(msg)
    }
    throw new Error(apiErrorMessage(e, 'Git 同步失败'))
  }
}

export async function syncGitSource(sourceId: string, fail_fast?: boolean): Promise<GitSyncAcceptedResponseData> {
  const client = getApiClient()
  try {
    const { data } = await client.post<GitSyncAcceptedApiResponse>(
      API_ENDPOINTS.PLUGINS.gitSourceSync(sourceId),
      {},
      {
        params: fail_fast ? { fail_fast: true } : undefined,
        timeout: API_CONFIG.GIT_SYNC_ACCEPT_TIMEOUT,
      },
    )
    if (data.code !== 200 || !data.data?.source_id) {
      throw new MarketplaceApiError(data.message || 'Git 同步失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    if (axios.isAxiosError(e)) {
      const detail = e.response?.data as { detail?: { message?: string } } | undefined
      const msg =
        detail?.detail && typeof detail.detail === 'object' && 'message' in detail.detail
          ? String((detail.detail as { message?: string }).message)
          : apiErrorMessage(e, 'Git 同步失败')
      throw new Error(msg)
    }
    throw new Error(apiErrorMessage(e, 'Git 同步失败'))
  }
}

export async function deleteGitSource(
  sourceId: string,
): Promise<{ deleted: boolean; deleted_skill_count?: number }> {
  const client = getApiClient()
  try {
    const { data } = await client.delete<
      ApiResponse<{ deleted: boolean; deleted_skill_count?: number }>
    >(API_ENDPOINTS.PLUGINS.gitSourceDetail(sourceId))
    if (data.code !== 200 || !data.data) {
      throw new MarketplaceApiError(data.message || '删除 Git 源失败', data.code)
    }
    return data.data
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    if (axios.isAxiosError(e)) {
      const detail = e.response?.data as {
        detail?: {
          error?: string
          message?: string
          data?: {
            deleted_skill_count?: number
            failed_skill_count?: number
          }
        }
      } | undefined
      const block = detail?.detail
      if (block && typeof block === 'object') {
        const code = (block.error ?? '').trim()
        if (code === 'git_source_sync_in_progress') {
          throw new GitSourceDeleteError('git_source_sync_in_progress')
        }
        if (code === 'git_source_cascade_delete_partial') {
          const payload = block.data
          throw new GitSourceDeleteError('git_source_cascade_delete_partial', {
            deletedSkillCount: Number(payload?.deleted_skill_count ?? 0),
            failedSkillCount: Number(payload?.failed_skill_count ?? 0),
          })
        }
      }
    }
    throw new Error(apiErrorMessage(e, '删除 Git 源失败'))
  }
}
