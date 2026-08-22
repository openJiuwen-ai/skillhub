// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useMemo } from 'react'
import { usePluginListQuery, type MarketplacePluginItem, type MarketplacePluginListRequest } from '@/api'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'
import { isSkillLikePluginType } from '@/utils/pluginType'

export interface MarketPlugin {
  assetId: string
  assetType: string
  name: string
  displayName: string
  shortDesc: string
  detailDesc: string
  iconUri: string
  publisherId: string
  publisherName: string
  tags: string[]
  certification: string
  runTime: string
  latestVersion: string
  /** 全部版本号（列表接口 all_versions） */
  allVersions: string[]
  viewCount: number
  installCount: number
  likeCount: number
  starCount: number
  reviewCount: number
  averageRating: number
  createTime?: number | null
  updateTime?: number | null
  /** 与后端 pin_order 一致；非空表示置顶 */
  pinOrder: number | null
  moderationStatus: 'APPROVED' | 'PENDING' | 'REJECTED'
  /** Git 且无 SKILL 声明版本时，与 latestVersion 对齐的行展示 commit 短码 */
  gitVersionDisplayAsCommit?: boolean
  resolvedCommitSha?: string | null
  declaredSkillVersion?: string | null
  storageMode?: string | null
  accessSource?: string | null
}

export interface UsePluginMarketConfigsParams {
  page: number
  pageSize: number
  searchKeyword?: string
  pluginType?: string
  pluginTypeExclude?: string
  /** 类别 ID（如 software-development / office-productivity） */
  categoryId?: string
  /** 逗号分隔多标签精确过滤（与后端 tags 参数一致） */
  tags?: string
  /** 标签匹配模式：all=同时包含全部，any=任一命中 */
  tagsMatch?: 'all' | 'any'
  orderBy?: MarketplacePluginListRequest['order_by']
  desc?: boolean
}

export interface UsePluginMarketConfigsReturn {
  marketPlugins: MarketPlugin[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  fetching: boolean
  error: string | null
  refreshMarketPlugins: () => Promise<unknown>
}

function firstString(...candidates: Array<string | null | undefined>): string {
  for (const c of candidates) {
    if (c != null && String(c).length > 0) return String(c)
  }
  return ''
}

function normalizeModerationStatus(raw: string | null | undefined): 'APPROVED' | 'PENDING' | 'REJECTED' {
  const u = (raw || 'APPROVED').toString().toUpperCase()
  if (u === 'PENDING' || u === 'REJECTED') return u
  return 'APPROVED'
}

function mapPlugin(item: MarketplacePluginItem): MarketPlugin {
  const accessSource = item.access_source || 'public'
  return {
    assetId: item.asset_id,
    assetType: item.asset_type,
    name: item.name,
    displayName: firstString(item.display_name, item.displayName) || item.name,
    shortDesc: firstString(item.short_desc, item.shortDesc),
    detailDesc: firstString(item.detail_desc, item.detailDesc),
    iconUri: resolvePluginIconUrl(item.icon_uri || ''),
    publisherId: item.publisher_id,
    publisherName: item.publisher_name,
    tags: item.tags || [],
    certification: item.certification || '',
    runTime: firstString(item.plugin_type, item.run_time),
    latestVersion: isSkillLikePluginType(item.plugin_type)
      ? (accessSource === 'group' || accessSource === 'owner' || accessSource === 'admin'
        ? firstString(item.latest_version, item.public_latest_version)
        : firstString(item.public_latest_version, item.latest_version))
      : item.latest_version || '',
    allVersions: Array.isArray(item.all_versions) ? item.all_versions : [],
    viewCount: item.view_count,
    installCount: item.install_count,
    likeCount: item.like_count,
    starCount: item.star_count ?? 0,
    reviewCount: item.review_count,
    averageRating: item.average_rating,
    createTime: item.create_time ?? item.createTime ?? null,
    updateTime: item.update_time ?? item.updateTime ?? null,
    pinOrder: item.pin_order ?? item.pinOrder ?? null,
    moderationStatus: normalizeModerationStatus(item.moderation_status),
    gitVersionDisplayAsCommit: Boolean(item.git_version_display_as_commit),
    resolvedCommitSha: item.resolved_commit_sha ?? null,
    declaredSkillVersion: item.declared_skill_version ?? null,
    storageMode: item.storage_mode ?? null,
    accessSource,
  }
}

export function usePluginMarketConfigs(params: UsePluginMarketConfigsParams): UsePluginMarketConfigsReturn {
  const query = usePluginListQuery({
    page: params.page,
    page_size: params.pageSize,
    search_keyword: params.searchKeyword || undefined,
    plugin_type: params.pluginType || undefined,
    plugin_type_exclude: params.pluginTypeExclude || undefined,
    category_id: params.categoryId || undefined,
    tags: params.tags || undefined,
    tags_match: params.tags ? (params.tagsMatch ?? 'all') : undefined,
    order_by: params.orderBy ?? 'install_count',
    desc: params.desc ?? true,
  })

  const listPayload = query.data?.data

  const marketPlugins = useMemo(() => {
    return (listPayload?.items ?? [])
      .map(mapPlugin)
      .filter(p => p.moderationStatus !== 'PENDING' && p.moderationStatus !== 'REJECTED')
  }, [listPayload])

  return {
    marketPlugins,
    total: listPayload?.total ?? 0,
    page: listPayload?.page ?? params.page,
    pageSize: listPayload?.page_size ?? params.pageSize,
    loading: query.isLoading,
    fetching: query.isFetching,
    error: query.error instanceof Error ? query.error.message : null,
    refreshMarketPlugins: query.refetch,
  }
}
