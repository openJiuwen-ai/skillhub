// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from 'react-query'
import { ArrowLeft, Download, Heart, Star, Eye, Play } from 'lucide-react'
import { CircularProgress, Tooltip } from '@mui/material'
import axios from 'axios'
import { AppHeader } from '@/components/Common/AppHeader'
import { pluginCardTooltipProps } from '@/components/Common/pluginCardTooltip'
import { getTagColor, buildHotTagSet, TAG_MAX_VISIBLE } from '@/utils/tagColors'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { isTextFile } from '@/utils/fileTypeUtils'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import {
  getPluginArtifactDownload,
  getPluginInteractions,
  getPluginVersionDetail,
  getVersionFileList,
  getPlugins,
  postSkillModeration,
  togglePluginInteract,
  getSkillLikeEffectiveModeration,
  getSkillLikeVersionModerationMap,
  getSkillLikeVersionPublishResultMap,
  normalizeSkillLikeModerationStatus,
  type UserInteractionState,
  type MarketplacePluginItem,
  type VersionFileEntry,
  type PluginVersionDetailData,
  getPluginTagOptions,
} from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { getSiteConfig } from '@/api/playground'
import { PlaygroundDrawer } from '@/components/Playground/PlaygroundDrawer'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'

import {
  formatMarketSkillVersionLabel,
  marketSkillVersionFilenameSegment,
} from '@/utils/formatSkillVersionLabel'
import {
  AGENT_ASSET_QUERY_VALUE,
  SKILL_LIKE_QUERY_VALUE,
  assetDetailPath,
  isAgentAssetPluginType,
  parseAgentAssetPluginType,
  parseSkillLikePluginType,
} from '@/utils/pluginType'

function isCanceledRequest(err: unknown): boolean {
  if (axios.isCancel(err)) return true
  if (!axios.isAxiosError(err)) return false
  return err.code === 'ERR_CANCELED' || err.name === 'CanceledError'
}

function formatDate(ts: number | null | undefined, lang: string): string {
  if (!ts) return '-'
  const ms = ts > 1_000_000_000_000 ? ts : ts * 1000
  return new Date(ms).toLocaleString(lang.startsWith('zh') ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function firstString(...candidates: Array<string | null | undefined>): string {
  for (const item of candidates) {
    if (item && item.trim()) return item.trim()
  }
  return ''
}

function normalizeTagList(tags: string[] | null | undefined): string[] {
  if (!Array.isArray(tags)) return []
  const out = tags.map(x => String(x).trim()).filter(Boolean)
  return [...new Set(out)]
}


function isIconUrl(icon: string | undefined): boolean {
  if (typeof icon !== 'string' || !icon.trim()) return false
  const t = icon.trim()
  if (t.startsWith('http://') || t.startsWith('https://')) return true
  if (t.startsWith('/') && !t.startsWith('//')) return true
  return t.includes('.')
}

function normalizePublishResult(
  raw: string | null | undefined,
): 'reviewing' | 'pending_moderation' | 'publish_success' | 'publish_failed' {
  const value = String(raw || '').trim().toLowerCase()
  if (value === 'reviewing' || value === 'pending_moderation' || value === 'publish_failed') return value
  return 'publish_success'
}

type SkillDetailQueryItem = MarketplacePluginItem & {
  __versionDetail?: PluginVersionDetailData
}

function versionDetailToListItem(raw: PluginVersionDetailData): SkillDetailQueryItem {
  return {
    asset_id: raw.asset_id,
    asset_type: raw.asset_type,
    name: raw.name,
    display_name: raw.display_name,
    short_desc: raw.short_desc,
    detail_desc: raw.detail_desc,
    icon_uri: raw.icon_uri,
    publisher_id: raw.publisher_id,
    publisher_name: raw.publisher_name,
    tags: raw.tags,
    certification: raw.certification,
    plugin_type: raw.plugin_type,
    publish_result: raw.publish_result,
    latest_version: raw.version,
    public_latest_version: raw.version,
    all_versions: raw.version ? [raw.version] : [],
    view_count: raw.view_count ?? 0,
    install_count: raw.install_count ?? 0,
    like_count: 0,
    star_count: 0,
    review_count: 0,
    average_rating: 0,
    update_time: raw.update_time ?? null,
    moderation_status: raw.version_moderation_status ?? raw.moderation_status,
    moderation_reject_reason: raw.version_moderation_reject_reason ?? raw.moderation_reject_reason,
    viewer_is_market_moderation_admin: raw.viewer_is_market_moderation_admin,
    __versionDetail: raw,
  }
}

function mapSkill(raw: MarketplacePluginItem) {
  const effectiveModeration = getSkillLikeEffectiveModeration(raw)
  return {
    assetId: raw.asset_id,
    publisherId: firstString(raw.publisher_id),
    displayName: firstString(raw.display_name, raw.displayName, raw.name),
    shortDesc: firstString(raw.short_desc, raw.shortDesc),
    detailDesc: firstString(raw.detail_desc, raw.detailDesc),
    iconUri: firstString(raw.icon_uri),
    publisherName: firstString(raw.publisher_name),
    latestVersion: firstString(raw.latest_version),
    tags: normalizeTagList(raw.tags ?? undefined),
    allVersions: Array.isArray(raw.all_versions) ? raw.all_versions : [],
    skillVersionModeration: getSkillLikeVersionModerationMap(raw),
    skillVersionPublishResult: getSkillLikeVersionPublishResultMap(raw),
    installCount: raw.install_count ?? 0,
    viewCount: raw.view_count ?? 0,
    updateTime: raw.update_time ?? raw.updateTime ?? null,
    publishResult: normalizePublishResult(raw.publish_result),
    moderationStatus: effectiveModeration.moderationStatus,
    moderationRejectReason: effectiveModeration.moderationRejectReason,
    gitVersionDisplayAsCommit: Boolean(raw.git_version_display_as_commit),
    resolvedCommitSha: raw.resolved_commit_sha ?? null,
    declaredSkillVersion: raw.declared_skill_version ?? null,
    storageMode: raw.storage_mode ?? null,
  }
}

function skillVersionSelectLabel(
  version: string,
  skill: ReturnType<typeof mapSkill>,
  publishResultMap: Record<string, string>,
  modMap: Record<string, string>,
  t: (k: string) => string,
): string {
  if (!version) return '-'
  const label = formatMarketSkillVersionLabel(version, {
    latestVersion: skill.latestVersion.trim(),
    gitVersionDisplayAsCommit: skill.gitVersionDisplayAsCommit,
    resolvedCommitSha: skill.resolvedCommitSha,
    declaredSkillVersion: skill.declaredSkillVersion ?? undefined,
    storageMode: skill.storageMode ?? undefined,
  })
  const pr = (publishResultMap[version] || '').toString().trim().toLowerCase()
  const u = (modMap[version] || '').toString().toUpperCase()
  if (pr === 'reviewing') return `${label} · ${t('profile.publishResultReviewing')}`
  if (pr === 'pending_moderation') return `${label} · ${t('profile.publishResultPendingModeration')}`
  if (pr === 'publish_failed') {
    return `${label} · ${
      u === 'REJECTED' ? t('profile.publishResultFailedModeration') : t('profile.publishResultFailedSystem')
    }`
  }
  return label
}

function defaultVersionForSkill(skill: ReturnType<typeof mapSkill>): string {
  const versions = skill.allVersions
  const latest = skill.latestVersion.trim()
  if (latest && versions.includes(latest)) return latest
  if (versions.length) return versions[versions.length - 1]
  return latest
}

type ReviewDetailLinkTone = 'indigo' | 'amber' | 'sky' | 'rose'

type SkillSecurityReviewSnapshot = {
  status: string
  failedReason: string
  overall: string
  risk: string
  conclusion: string
  metrics: {
    checkCount: number
    findingCount: number
    failedCheckCount: number
    attentionCheckCount: number
  }
  sections: Array<{
    key: string
    title: string
    status: string
    findingCount: number
    checkCount: number
    failedCheckCount: number
    attentionCheckCount: number
  }>
  mode: string
  engine: string
  modelName: string
  traceId: string
}

const REVIEW_DETAIL_LINK_CLASS_BY_TONE: Record<ReviewDetailLinkTone, string> = {
  indigo: 'rounded-full border border-indigo-200 bg-white px-4 py-2 text-xs font-medium text-indigo-700 shadow-sm hover:bg-indigo-50',
  amber: 'rounded-full border border-amber-200 bg-white px-4 py-2 text-xs font-medium text-amber-700 shadow-sm hover:bg-amber-50',
  sky: 'rounded-full border border-sky-200 bg-white px-4 py-2 text-xs font-medium text-sky-700 shadow-sm hover:bg-sky-50',
  rose: 'rounded-full border border-rose-200 bg-white px-4 py-2 text-xs font-medium text-rose-700 shadow-sm hover:bg-rose-50',
}

function ReviewDetailLink({ path, tone }: { path: string; tone: ReviewDetailLinkTone }) {
  const { t } = useTranslation()
  return (
    <Link to={path} className={REVIEW_DETAIL_LINK_CLASS_BY_TONE[tone]}>
      {t('profile.viewReviewDetail')}
    </Link>
  )
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function asNumber(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function countSectionFindings(section: Record<string, unknown>, metrics: Record<string, unknown>): number {
  const directCount = asNumber(metrics.finding_count, Number.NaN)
  if (Number.isFinite(directCount)) return directCount
  const checks = Array.isArray(section.checks) ? section.checks : []
  return checks.reduce((sum, check) => {
    const findings = asRecord(check).findings
    return sum + (Array.isArray(findings) ? findings.length : 0)
  }, 0)
}

function buildSecurityReviewSnapshot(detail?: PluginVersionDetailData | null): SkillSecurityReviewSnapshot | null {
  if (!detail) return null
  const summary = asRecord(detail.review_summary)
  const metrics = asRecord(summary.metrics)
  const semantic = Object.keys(asRecord(detail.semantic_review)).length
    ? asRecord(detail.semantic_review)
    : asRecord(summary.semantic_review)
  const sections = Array.isArray(detail.review_sections) ? detail.review_sections.map(section => {
    const raw = asRecord(section)
    const sectionMetrics = asRecord(raw.metrics)
    return {
      key: asText(raw.key),
      title: asText(raw.title) || asText(raw.name) || asText(raw.key),
      status: asText(raw.status),
      findingCount: countSectionFindings(raw, sectionMetrics),
      checkCount: asNumber(sectionMetrics.check_count, Array.isArray(raw.checks) ? raw.checks.length : 0),
      failedCheckCount: asNumber(sectionMetrics.failed_check_count),
      attentionCheckCount: asNumber(sectionMetrics.attention_check_count),
    }
  }) : []
  const derivedCheckCount = sections.reduce((sum, section) => sum + section.checkCount, 0)
  const derivedFailedCheckCount = sections.reduce((sum, section) => sum + section.failedCheckCount, 0)
  const derivedAttentionCheckCount = sections.reduce((sum, section) => sum + section.attentionCheckCount, 0)
  const derivedFindingCount = sections.reduce((sum, section) => sum + section.findingCount, 0)
  return {
    status: asText(detail.review_status) || asText(summary.status),
    failedReason: asText(detail.review_failed_reason) || asText(summary.failed_reason),
    overall: asText(summary.overall),
    risk: asText(summary.risk),
    conclusion: asText(summary.conclusion) || asText(semantic.display_message) || asText(semantic.conclusion),
    metrics: {
      checkCount: asNumber(metrics.check_count, asNumber(summary.check_count, derivedCheckCount)),
      findingCount: asNumber(metrics.finding_count, asNumber(summary.finding_count, derivedFindingCount)),
      failedCheckCount: asNumber(metrics.failed_check_count, derivedFailedCheckCount),
      attentionCheckCount: asNumber(metrics.attention_check_count, derivedAttentionCheckCount),
    },
    sections,
    mode: asText(detail.review_mode) || asText(summary.review_mode) || (semantic.engine ? 'hybrid' : ''),
    engine: asText(detail.review_engine) || asText(summary.review_engine) || asText(semantic.engine),
    modelName: asText(detail.model_name) || asText(summary.model_name) || asText(semantic.model_name),
    traceId: asText(detail.trace_id) || asText(summary.trace_id),
  }
}

async function triggerDownload(url: string, fileName: string): Promise<void> {
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.target = '_blank'
  link.rel = 'noopener noreferrer'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function SecurityReviewPanel({
  review,
  detailPath,
  t,
}: {
  review: SkillSecurityReviewSnapshot | null
  detailPath: string
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  const status = review?.status || 'none'
  const risk = review?.risk || 'unknown'
  const hasReview = Boolean(
    review && (review.status || review.overall || review.metrics.checkCount || review.sections.length),
  )
  const failed = status === 'failed' || review?.overall === 'rejected' || Boolean(review?.metrics.failedCheckCount)
  const systemFailed = status === 'system_failed'
  const reviewing = status === 'running' || status === 'queued' || status === 'reviewing'
  const riskBadgeClass = risk === 'high' || failed || systemFailed
    ? 'bg-rose-100 text-rose-600'
    : risk === 'medium' || reviewing
      ? 'bg-orange-100 text-orange-600'
      : risk === 'low'
        ? 'bg-[#F5F5F5] text-[#191919]'
        : 'bg-slate-100 text-slate-500'
  const topSections = [...(review?.sections || [])]
    .sort(
      (a, b) => (b.failedCheckCount - a.failedCheckCount)
        || (b.attentionCheckCount - a.attentionCheckCount)
        || (b.findingCount - a.findingCount),
    )
    .filter(section => section.failedCheckCount || section.attentionCheckCount || section.findingCount)
    .slice(0, 3)

  return (
    <section className="rounded-[12px] border border-[#E6E6E6] bg-white px-5 py-5">
      <div className="flex items-center gap-2">
        <h2 className="text-[16px] font-semibold text-[#191919]">{t('profile.securityReviewPanelTitle')}</h2>
        <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${riskBadgeClass}`}>
          {t(`profile.risk.${risk || 'unknown'}`)}
        </span>
      </div>

      <div className="mt-5 text-sm font-medium text-slate-800">{t('profile.securityReviewContent')}</div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        <SecurityMetric label={t('profile.securityReviewChecks')} value={review?.metrics.checkCount ?? 0} />
        <SecurityMetric label={t('profile.securityReviewFindings')} value={review?.metrics.findingCount ?? 0} />
        <SecurityMetric
          label={t('profile.securityReviewAttention')}
          value={review?.metrics.attentionCheckCount ?? 0}
          tone="orange"
        />
      </div>

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium text-slate-800">{t('profile.securityReviewTopRisks')}</div>
        {topSections.length ? (
          <div className="space-y-2">
            {topSections.map(section => (
              <div key={section.key || section.title} className="rounded-lg bg-slate-50 px-4 py-3">
                <div className="truncate text-sm text-slate-800">{section.title}</div>
                <div className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs text-slate-500">
                  <span>{t('profile.reviewFindingCount', { count: section.findingCount })}</span>
                  <span>·</span>
                  <span>{t('profile.reviewAttentionCheckCount', { count: section.attentionCheckCount })}</span>
                  <span>·</span>
                  <span>{t('profile.reviewFailedCheckCount', { count: section.failedCheckCount })}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-500">
            {hasReview ? t('profile.securityReviewNoTopRisks') : t('profile.securityReviewNoDataHint')}
          </div>
        )}
      </div>

      <div className="mt-3 space-y-2 rounded-lg bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-600">
        {review?.mode ? <div>{t('profile.securityReviewMode', { mode: review.mode })}</div> : null}
        {review?.modelName ? <div>{t('profile.semanticReviewModel', { model: review.modelName })}</div> : null}
        {review?.traceId ? (
          <div className="truncate" title={review.traceId}>
            {t('profile.securityReviewTrace', { trace: review.traceId.slice(0, 12) })}
          </div>
        ) : null}
        {!review?.mode && !review?.modelName && !review?.traceId ? (
          <div>{review?.failedReason || review?.conclusion || t('profile.securityReviewNoSummary')}</div>
        ) : null}
      </div>

      {detailPath ? (
        <Link
          to={detailPath}
          className={[
            'mt-4 flex w-full items-center justify-center rounded-full border border-slate-900 bg-white',
            'px-4 py-2 text-sm font-medium text-slate-900 transition hover:bg-slate-50',
          ].join(' ')}
        >
          {t('profile.viewReviewDetail')}
        </Link>
      ) : null}
    </section>
  )
}

function SecurityMetric({ label, value, tone = 'slate' }: { label: string; value: number; tone?: 'slate' | 'orange' }) {
  const valueClass = tone === 'orange' ? 'text-orange-600' : 'text-slate-950'
  return (
    <div className="rounded-[8px] bg-[#F8F8F8] px-3 py-3 text-center">
      <div className={`text-lg font-semibold tabular-nums ${valueClass}`}>{value}</div>
      <div className="mt-1.5 text-[12px] text-slate-500">{label}</div>
    </div>
  )
}

function SkillHeaderIcon({ displayName, iconUri }: { displayName: string; iconUri: string }) {
  const resolved = resolvePluginIconUrl(iconUri)
  const [imgFailed, setImgFailed] = useState(false)
  const letter = (displayName || 'S').slice(0, 1).toUpperCase()
  const showImg = Boolean(resolved && isIconUrl(resolved) && !imgFailed)
  if (showImg) {
    return (
      <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-[12px] bg-sky-100 ring-1 ring-sky-200/60">
        <img src={resolved} alt="" className="h-full w-full object-cover" onError={() => setImgFailed(true)} />
      </div>
    )
  }
  return (
    <div
      className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[12px] bg-[linear-gradient(135deg,#DDF3FF_0%,#B9E4FF_100%)] text-[20px] font-semibold text-[#0F5FD7] ring-1 ring-[#B5DEFF]"
      aria-hidden
    >
      {letter || '?'}
    </div>
  )
}

export default function SkillDetailPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const { assetId: encodedAssetId = '' } = useParams<{ assetId: string }>()
  const assetId = decodeURIComponent(encodedAssetId)
  const { user, isAuthenticated, isMarketModerationAdmin } = useGitCodeAuth()
  const queryClient = useQueryClient()
  /** 忽略「审核前」发出的旧版本详情响应，避免覆盖刚审核后的状态 */
  const versionDetailFetchGen = useRef(0)
  const { openPublish } = usePublishDrawer()
  const [selectedVersion, setSelectedVersion] = useState('')
  const [downloadLoading, setDownloadLoading] = useState(false)
  const [playgroundOpen, setPlaygroundOpen] = useState(false)
  const [playgroundEnabled, setPlaygroundEnabled] = useState(false)
  const [changelog, setChangelog] = useState<string | null>(null)
  const [changelogLoading, setChangelogLoading] = useState(false)
  const [changelogError, setChangelogError] = useState<string | null>(null)
  /** 版本详情接口返回的 detail_desc（后端已从 OBS 读取版本专属内容） */
  const [detailDescFromApi, setDetailDescFromApi] = useState<string | null>(null)
  /** 来自版本详情接口的 install_count；未拉到前用列表里的 installCount */
  const [installCountFromVersionApi, setInstallCountFromVersionApi] = useState<number | null>(null)
  /** 来自版本详情接口的 view_count（成功拉详情后会递增）；未拉到前用列表里的 viewCount */
  const [viewCountFromVersionApi, setViewCountFromVersionApi] = useState<number | null>(null)
  /** null：未拉到版本详情，用列表 tags；非 null：以版本详情为准（可为 []） */
  const [tagsFromVersionApi, setTagsFromVersionApi] = useState<string[] | null>(null)
  const [updateTimeFromVersionApi, setUpdateTimeFromVersionApi] = useState<number | null>(null)
  const [publishResult, setPublishResult] = useState<'reviewing' | 'pending_moderation' | 'publish_success' | 'publish_failed'>('publish_success')
  const [publishFailedReason, setPublishFailedReason] = useState<string>('')
  const [securityReview, setSecurityReview] = useState<SkillSecurityReviewSnapshot | null>(null)
  const [moderationStatus, setModerationStatus] = useState<'PENDING' | 'APPROVED' | 'REJECTED'>('APPROVED')
  const [moderationRejectReason, setModerationRejectReason] = useState<string>('')
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false)
  const [rejectDraft, setRejectDraft] = useState('')
  const [moderationBusy, setModerationBusy] = useState(false)
  /** null：尚未从版本详情接口拿到 viewer 标记；否则以服务端为准 */
  const [versionDetailViewerModerator, setVersionDetailViewerModerator] = useState<boolean | null>(null)
  const [interactBusy, setInteractBusy] = useState<'like' | 'star' | null>(null)
  const [activeTab, setActiveTab] = useState<'readme' | 'changelog' | 'files'>('readme')
  const [fileList, setFileList] = useState<VersionFileEntry[] | null>(null)
  const [fileListLoading, setFileListLoading] = useState(false)
  const [fileListError, setFileListError] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [fileContentLoading, setFileContentLoading] = useState(false)
  const [fileContentError, setFileContentError] = useState<string | null>(null)
  const downloadRef = useRef(false)
  const fileContentAbortRef = useRef<AbortController | null>(null)
  const locationState = location.state as { fromProfile?: boolean; moderationContext?: 'pending' } | null
  const routeSearchParams = useMemo(() => new URLSearchParams(location.search), [location.search])
  const requestedVersion = routeSearchParams.get('version')?.trim() || ''
  const fromProfileEntry = useMemo(() => {
    const fromState = locationState?.fromProfile === true
    const fromQuery = routeSearchParams.get('from') === 'profile'
    return fromState || fromQuery
  }, [locationState, routeSearchParams])
  const fromPendingModerationEntry =
    locationState?.moderationContext === 'pending' ||
    routeSearchParams.get('moderation_status')?.trim().toUpperCase() === 'PENDING'
  const isAssetRoute = location.pathname.startsWith('/assets/')
  const detailTypeQuery = isAssetRoute ? AGENT_ASSET_QUERY_VALUE : SKILL_LIKE_QUERY_VALUE
  const alternateTypeQuery = isAssetRoute ? SKILL_LIKE_QUERY_VALUE : AGENT_ASSET_QUERY_VALUE

  const detailQuery = useQuery<SkillDetailQueryItem | null>(
    ['skill-detail-raw', assetId, requestedVersion, fromPendingModerationEntry, detailTypeQuery],
    async () => {
      const loadByType = async (pluginType: string) => {
        const response = await getPlugins({
          page: 1,
          page_size: 1,
          asset_id: assetId,
          plugin_type: pluginType,
          moderation_status: fromPendingModerationEntry ? 'PENDING' : undefined,
        })
        return response.data.items.find(item => item.asset_id === assetId) ?? null
      }
      const primary = await loadByType(detailTypeQuery)
      if (primary) return primary
      const alternate = await loadByType(alternateTypeQuery)
      if (alternate) return alternate
      if (!requestedVersion) return null
      const versionDetail = await getPluginVersionDetail(assetId, requestedVersion)
      return versionDetailToListItem(versionDetail)
    },
    { enabled: Boolean(assetId), retry: 1 },
  )

  const skillRaw = detailQuery.data ?? null
  const isAgentDetail = isAgentAssetPluginType(skillRaw?.plugin_type)
  const skill = useMemo(() => (skillRaw ? mapSkill(skillRaw) : null), [skillRaw])
  const versionList = skill?.allVersions?.length ? [...skill.allVersions].reverse() : []

  useEffect(() => {
    if (!assetId || !skillRaw?.plugin_type) return
    const targetPath = assetDetailPath(assetId, skillRaw.plugin_type)
    const currentPath = isAssetRoute
      ? `/assets/${encodeURIComponent(assetId)}`
      : `/skills/${encodeURIComponent(assetId)}`
    if (targetPath === currentPath) return
    navigate(`${targetPath}${location.search}`, { replace: true, state: location.state })
  }, [assetId, isAssetRoute, location.search, location.state, navigate, skillRaw?.plugin_type])

  useEffect(() => {
    if (isAgentDetail) {
      setPlaygroundEnabled(false)
      return
    }
    getSiteConfig()
      .then(cfg => setPlaygroundEnabled(cfg.playground_enabled))
      .catch(() => {})
  }, [isAgentDetail])

  useEffect(() => {
    if (!skill) return
    setInstallCountFromVersionApi(null)
    setViewCountFromVersionApi(null)
    setTagsFromVersionApi(null)
    setUpdateTimeFromVersionApi(null)
    setVersionDetailViewerModerator(null)
    setDetailDescFromApi(null)
    setPublishResult(skill.publishResult)
    setPublishFailedReason('')
    setSecurityReview(buildSecurityReviewSnapshot(skillRaw?.__versionDetail))
    setModerationStatus(skill.moderationStatus)
    setModerationRejectReason(skill.moderationRejectReason)
    setActiveTab('readme')
    setFileList(null)
    setSelectedFile(null)
    setFileContent(null)
    setSelectedVersion(prev => {
      if (requestedVersion && skill.allVersions.includes(requestedVersion)) return requestedVersion
      if (prev && skill.allVersions.includes(prev)) return prev
      return defaultVersionForSkill(skill)
    })
  }, [requestedVersion, skill])

  const applyVersionDetailResult = useCallback((res: PluginVersionDetailData) => {
    setDetailDescFromApi(res.detail_desc?.trim() || null)
    setChangelog(res.changelog?.trim() || null)

    setInstallCountFromVersionApi(res.install_count ?? 0)
    if (res.view_count != null && Number.isFinite(Number(res.view_count))) {
      setViewCountFromVersionApi(Number(res.view_count))
    } else {
      setViewCountFromVersionApi(null)
    }
    setTagsFromVersionApi(normalizeTagList(res.tags ?? undefined))
    setUpdateTimeFromVersionApi(
      res.update_time != null && Number.isFinite(Number(res.update_time)) ? Number(res.update_time) : null,
    )
    setVersionDetailViewerModerator(res.viewer_is_market_moderation_admin === true)
    if (res.publish_result != null && String(res.publish_result).trim()) {
      setPublishResult(normalizePublishResult(res.publish_result))
    }
    setPublishFailedReason(firstString(res.publish_failed_reason))
    setSecurityReview(buildSecurityReviewSnapshot(res))
    const effectiveModeration = getSkillLikeEffectiveModeration(res)
    setModerationStatus(normalizeSkillLikeModerationStatus(effectiveModeration.moderationStatus))
    setModerationRejectReason(effectiveModeration.moderationRejectReason)
    setChangelogLoading(false)
    void queryClient.invalidateQueries({ queryKey: ['plugins'] })
  }, [queryClient])

  useEffect(() => {
    if (!skill || !selectedVersion) {
      setChangelog(null)
      setChangelogLoading(false)
      setChangelogError(null)
      setDetailDescFromApi(null)
      return
    }
    const cachedVersionDetail = detailQuery.data?.__versionDetail
    const gen = ++versionDetailFetchGen.current
    setChangelogLoading(true)
    setChangelogError(null)
    setChangelog(null)
    setDetailDescFromApi(null)
    fileContentAbortRef.current?.abort()
    fileContentAbortRef.current = null
    setFileList(null)
    setFileListLoading(false)
    setFileListError(null)
    setSelectedFile(null)
    setFileContent(null)
    setFileContentLoading(false)
    setFileContentError(null)
    if (cachedVersionDetail && cachedVersionDetail.version === selectedVersion) {
      applyVersionDetailResult(cachedVersionDetail)
      return
    }
    const ac = new AbortController()
    void getPluginVersionDetail(skill.assetId, selectedVersion, { signal: ac.signal })
      .then(res => {
        if (ac.signal.aborted) return
        if (gen !== versionDetailFetchGen.current) return
        applyVersionDetailResult(res)
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return
        if (gen !== versionDetailFetchGen.current) return
        if (isCanceledRequest(err)) return
        setChangelogError(err instanceof Error ? err.message : t('plugins.detail.changelogLoadFailed'))
        setChangelogLoading(false)
      })
    return () => ac.abort()
  }, [applyVersionDetailResult, detailQuery.data, selectedVersion, skill, t])

  const handleFileClick = useCallback(
    (path: string) => {
      if (!skill || !selectedVersion) return
      fileContentAbortRef.current?.abort()
      setSelectedFile(path)
      setFileContent(null)
      setFileContentError(null)
      if (!isTextFile(path)) return
      const ac = new AbortController()
      fileContentAbortRef.current = ac
      setFileContentLoading(true)
      void getVersionFileList(skill.assetId, selectedVersion, { withContent: path, signal: ac.signal })
        .then(data => {
          if (ac.signal.aborted) return
          setFileContent(data.content)
          setFileContentLoading(false)
        })
        .catch((err: unknown) => {
          if (ac.signal.aborted || isCanceledRequest(err)) return
          setFileContentError(err instanceof Error ? err.message : t('plugins.detail.filesContentError'))
          setFileContentLoading(false)
        })
    },
    [skill, selectedVersion],
  )

  useEffect(() => {
    if (activeTab !== 'files' || !skill || !selectedVersion) {
      return
    }
    const ac = new AbortController()
    setFileListLoading(true)
    setFileListError(null)
    setFileContent(null)
    setFileContentError(null)
    void getVersionFileList(skill.assetId, selectedVersion, { withContent: 'workflow.md', signal: ac.signal })
      .then(data => {
        if (ac.signal.aborted) return
        setFileList(data.files)
        setFileListLoading(false)
        if (data.content_path) {
          setSelectedFile(data.content_path)
          setFileContent(data.content)
        } else {
          // SKILL.md 不存在时优先选第一个 .md，否则第一个可预览文件
          const first =
            data.files.find(f => f.path.toLowerCase().endsWith('.md')) ??
            data.files.find(f => isTextFile(f.path))
          if (first) handleFileClick(first.path)
        }
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted || isCanceledRequest(err)) return
        setFileListError(err instanceof Error ? err.message : t('plugins.detail.filesListError'))
        setFileListLoading(false)
      })
    return () => ac.abort()
  }, [activeTab, skill?.assetId, selectedVersion, handleFileClick])

  const displayInstallCount = installCountFromVersionApi ?? skill?.installCount ?? 0
  const isOwnSkill = useMemo(
    () => Boolean(skill?.publisherId && user?.id && String(skill.publisherId) === String(user.id)),
    [skill?.publisherId, user?.id],
  )
  const blockedByModeration = moderationStatus === 'PENDING' || moderationStatus === 'REJECTED'
  const canShowInteractButtons = Boolean(skill) && !blockedByModeration
  const canToggleInteract = canShowInteractButtons && !isOwnSkill
  const canShowStarButton = canShowInteractButtons
  const canShowDownloadActions = !fromProfileEntry

  const interactionsQuery = useQuery(
    ['skill-interactions', skill?.assetId],
    async () => {
      if (!skill?.assetId) return null
      return getPluginInteractions(skill.assetId)
    },
    { enabled: Boolean(skill?.assetId), retry: 1 },
  )
  const hotTagQuery = useQuery(
    ['plugins', 'tag-options', skillRaw?.plugin_type],
    () => getPluginTagOptions({ plugin_type: skillRaw?.plugin_type ?? undefined, limit: 20 }),
    { enabled: Boolean(skillRaw?.plugin_type), staleTime: 60_000 },
  )
  const hotTagSet = useMemo(() => buildHotTagSet(hotTagQuery.data ?? []), [hotTagQuery.data])
  const interactionState: UserInteractionState | null = interactionsQuery.data ?? null
  const liked = interactionState?.liked ?? false
  const starred = interactionState?.starred ?? false
  const displayLikeCount = interactionState?.like_count ?? detailQuery.data?.like_count ?? 0
  const displayStarCount = interactionState?.star_count ?? detailQuery.data?.star_count ?? 0
  const displayViewCount = viewCountFromVersionApi ?? skill?.viewCount ?? 0
  const displayTags = tagsFromVersionApi !== null ? tagsFromVersionApi : skill?.tags ?? []
  const displayUpdateTime = updateTimeFromVersionApi ?? skill?.updateTime ?? null
  const publishType =
    parseSkillLikePluginType(skillRaw?.plugin_type) ??
    parseAgentAssetPluginType(skillRaw?.plugin_type) ??
    null
  const publishReady = publishType !== null

  const handlePublish = useCallback(() => {
    if (!publishType) return
    if (isAuthenticated) {
      openPublish(publishType)
      return
    }
    setPostLoginRedirect(`/profile/publish?kind=${publishType}`)
    navigate('/login')
  }, [isAuthenticated, navigate, openPublish, publishType])

  const canShowModerationPanel = useMemo(() => {
    const inManualStage =
      publishResult === 'pending_moderation' || (publishResult === 'publish_failed' && moderationStatus === 'REJECTED')
    if (!inManualStage) return false
    if (detailQuery.data?.viewer_is_market_moderation_admin === true || isMarketModerationAdmin) return true
    return versionDetailViewerModerator === true
  }, [
    detailQuery.data?.viewer_is_market_moderation_admin,
    isMarketModerationAdmin,
    moderationStatus,
    publishResult,
    versionDetailViewerModerator,
  ])
  const canShowReviewDetailLink = useMemo(() => {
    if (isAgentDetail) return false
    if (!skill || !selectedVersion.trim()) return false
    const hasReviewDetail = Boolean(
      securityReview && (
        securityReview.status
        || securityReview.overall
        || securityReview.metrics.checkCount
        || securityReview.sections.length
      ),
    )
    if (publishResult === 'publish_success' && !hasReviewDetail) return false
    if (isOwnSkill) return true
    if (detailQuery.data?.viewer_is_market_moderation_admin === true || isMarketModerationAdmin) return true
    return versionDetailViewerModerator === true
  }, [
    detailQuery.data?.viewer_is_market_moderation_admin,
    isAgentDetail,
    isMarketModerationAdmin,
    isOwnSkill,
    publishResult,
    securityReview,
    selectedVersion,
    skill,
    versionDetailViewerModerator,
  ])
  const reviewDetailPath = skill && selectedVersion.trim()
    ? `/profile/plugins/${encodeURIComponent(skill.assetId)}/versions/${encodeURIComponent(selectedVersion.trim())}/review`
    : ''

  const publishResultLabel = useMemo(() => {
    if (publishResult === 'reviewing') return t('profile.publishResultReviewing')
    if (publishResult === 'pending_moderation') return t('profile.publishResultPendingModeration')
    if (publishResult === 'publish_failed') {
      return moderationStatus === 'REJECTED'
        ? t('profile.publishResultFailedModeration')
        : t('profile.publishResultFailedSystem')
    }
    return t('profile.publishResultSuccess')
  }, [moderationStatus, publishResult, t])

  const moderationLabel = useMemo(() => {
    if (moderationStatus === 'PENDING') return t('plugins.skillPage.moderationPending')
    if (moderationStatus === 'REJECTED') return t('plugins.skillPage.moderationRejected')
    return t('plugins.skillPage.moderationApproved')
  }, [moderationStatus, t])

  const runApprove = useCallback(async () => {
    if (!skill || moderationBusy) return
    setModerationBusy(true)
    try {
      await postSkillModeration(skill.assetId, {
        action: 'approve',
        version: selectedVersion.trim() || defaultVersionForSkill(skill),
      })
      versionDetailFetchGen.current += 1
      setPublishResult('publish_success')
      setPublishFailedReason('')
      setModerationStatus('APPROVED')
      setModerationRejectReason('')
      void queryClient.invalidateQueries(['skill-detail-raw', assetId])
      void queryClient.invalidateQueries({ queryKey: ['admin-pending-skills'] })
      void queryClient.invalidateQueries({ queryKey: ['skill-moderation-audit-history'] })
      window.alert(t('plugins.skillPage.moderationSuccess'))
    } catch {
      window.alert(t('plugins.skillPage.moderationFailed'))
    } finally {
      setModerationBusy(false)
    }
  }, [assetId, moderationBusy, queryClient, selectedVersion, skill, t])

  const submitReject = useCallback(async () => {
    if (!skill || moderationBusy) return
    const reason = rejectDraft.trim()
    if (!reason) {
      window.alert(t('plugins.skillPage.rejectReasonPlaceholder'))
      return
    }
    setModerationBusy(true)
    try {
      await postSkillModeration(skill.assetId, {
        action: 'reject',
        reason,
        version: selectedVersion.trim() || defaultVersionForSkill(skill),
      })
      versionDetailFetchGen.current += 1
      setPublishResult('publish_failed')
      setPublishFailedReason(reason)
      setModerationStatus('REJECTED')
      setModerationRejectReason(reason)
      setRejectDialogOpen(false)
      setRejectDraft('')
      void queryClient.invalidateQueries(['skill-detail-raw', assetId])
      void queryClient.invalidateQueries({ queryKey: ['admin-pending-skills'] })
      void queryClient.invalidateQueries({ queryKey: ['skill-moderation-audit-history'] })
      window.alert(t('plugins.skillPage.moderationSuccess'))
    } catch {
      window.alert(t('plugins.skillPage.moderationFailed'))
    } finally {
      setModerationBusy(false)
    }
  }, [assetId, moderationBusy, queryClient, rejectDraft, selectedVersion, skill, t])

  const handleDownload = useCallback(async () => {
    if (!skill || downloadRef.current || fromProfileEntry) return
    const version = selectedVersion.trim() || defaultVersionForSkill(skill)
    if (!version) {
      window.alert(t('plugins.actions.downloadFailed'))
      return
    }
    downloadRef.current = true
    setDownloadLoading(true)
    try {
      const data = await getPluginArtifactDownload(skill.assetId, version)
      const base = data.name.trim() || skill.displayName || skill.assetId
      const versionSeg = marketSkillVersionFilenameSegment(data.version, {
        latestVersion: skill.latestVersion,
        gitVersionDisplayAsCommit: skill.gitVersionDisplayAsCommit,
        resolvedCommitSha: skill.resolvedCommitSha,
        declaredSkillVersion: skill.declaredSkillVersion,
        storageMode: skill.storageMode,
      })
      await triggerDownload(data.download_url, `${base.replace(/\s+/g, '-')}_${versionSeg}.zip`)
    } catch {
      window.alert(t('plugins.actions.downloadFailed'))
    } finally {
      downloadRef.current = false
      setDownloadLoading(false)
    }
  }, [fromProfileEntry, selectedVersion, skill, t])

  const handleToggleInteract = useCallback(
    async (actionType: 'like' | 'star') => {
      if (!skill || interactBusy) return
      if (!canToggleInteract) return
      if (!isAuthenticated) {
        setPostLoginRedirect(assetDetailPath(skill.assetId, skillRaw?.plugin_type))
        navigate('/login')
        return
      }
      setInteractBusy(actionType)
      try {
        const result = await togglePluginInteract(skill.assetId, actionType)
        // 用 toggle 返回的最新计数立即更新单个缓存
        const prev = queryClient.getQueryData<UserInteractionState>(['skill-interactions', skill.assetId])
        queryClient.setQueryData<UserInteractionState>(['skill-interactions', skill.assetId], {
          liked: actionType === 'like' ? result.active : (prev?.liked ?? false),
          starred: actionType === 'star' ? result.active : (prev?.starred ?? false),
          like_count: actionType === 'like' ? (result.like_count ?? prev?.like_count ?? null) : (prev?.like_count ?? null),
          star_count: actionType === 'star' ? (result.star_count ?? prev?.star_count ?? null) : (prev?.star_count ?? null),
        })
        // 标脏批量缓存，回到首页时自动重拉
        void queryClient.invalidateQueries(['market-interactions'])
      } catch (e) {
        const msg = e instanceof Error ? e.message : t('plugins.actions.favoritePending')
        window.alert(msg)
      } finally {
        setInteractBusy(null)
      }
    },
    [canToggleInteract, interactBusy, isAuthenticated, navigate, queryClient, skill, skillRaw?.plugin_type, t],
  )

  /** 与 `AppHeader` 内层一致：左与 logo 对齐，右与登录/账号区对齐 */
  const pageAlignWithHeader = 'mx-auto w-full max-w-[1600px] px-4 sm:px-5 xl:px-0'
  /** 白色内容面板内边距：正文、表格和图片均保留稳定留白 */
  const cardInnerPad = 'px-5 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-7'

  return (
    <div className="flex min-h-dvh flex-col overflow-x-hidden bg-white">
      <AppHeader onPublish={publishReady ? handlePublish : undefined} showPublish={publishReady} />
      <main className="flex-1 bg-[radial-gradient(ellipse_at_22%_4%,rgba(224,243,255,0.92)_0%,rgba(239,242,255,0.68)_36%,rgba(252,244,255,0.58)_65%,rgba(255,255,255,0.96)_100%)] pb-10">
        <div className={`${pageAlignWithHeader} py-3 pb-8 sm:py-4 sm:pb-10`}>
          <Breadcrumbs
            className="mb-2 text-left sm:mb-3"
            items={[
              { label: t('common.breadcrumb.home'), to: '/' },
              {
                label: skill?.displayName?.trim()
                  ? skill.displayName
                  : t('common.breadcrumb.skillDetail'),
              },
            ]}
          />

          {detailQuery.isLoading ? (
            <div className="flex min-h-[12rem] items-center justify-center">
              <CircularProgress size={28} />
            </div>
          ) : null}

          {!detailQuery.isLoading && !skill ? (() => {
            const err = detailQuery.error
            const status = axios.isAxiosError(err) ? err.response?.status : undefined
            const isNotFound = status === 404
            const headline = isNotFound
              ? (isAssetRoute ? t('plugins.assetPage.notFoundTitle') : t('plugins.skillPage.notFoundTitle'))
              : t('profile.noDetail')
            const sub = isNotFound
              ? (isAssetRoute ? t('plugins.assetPage.notFoundHint') : t('plugins.skillPage.notFoundHint'))
              : (axios.isAxiosError(err)
                ? `加载详情失败（HTTP ${status ?? '?'}）：${err.message}`
                : null)
            return (
              <div className="w-full rounded-lg border border-rose-200 bg-rose-50/95 px-5 py-4 text-left text-sm text-rose-800 sm:rounded-xl sm:px-6 md:px-8">
                <div className="font-medium text-rose-900">{headline}</div>
                {sub ? <div className="mt-1 text-rose-700">{sub}</div> : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => navigate(-1)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-rose-300 bg-white px-3 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
                    返回上一页
                  </button>
                  <Link
                    to="/"
                    className="inline-flex items-center rounded-full border border-rose-300 bg-white px-3 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50"
                  >
                    返回首页
                  </Link>
                </div>
              </div>
            )
          })() : null}

          {skill ? (
            <article className="w-full">
              <header className="pb-6 pt-1 sm:pb-7">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-[clamp(0.75rem,2vw,1rem)]">
                    <SkillHeaderIcon displayName={skill.displayName} iconUri={skill.iconUri} />
                    <div className="min-w-0 text-left">
                      <h1 className="text-[length:clamp(1.125rem,2.8vw,1.75rem)] font-bold leading-tight text-slate-900">
                        {skill.displayName}
                      </h1>
                      {displayTags.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {displayTags.slice(0, TAG_MAX_VISIBLE).map((tag) => {
                            const c = getTagColor(tag, hotTagSet.has(tag))
                            return (
                              <span
                                key={tag}
                                className="inline-flex items-center rounded-[3px] border border-black/5 px-2 py-0.5 text-[12px] font-medium"
                                style={{ backgroundColor: c.bg, color: c.fg }}
                              >
                                {tag}
                              </span>
                            )
                          })}
                          {displayTags.length > TAG_MAX_VISIBLE && (
                            <Tooltip {...pluginCardTooltipProps} title={displayTags.slice(TAG_MAX_VISIBLE).join(' · ')}>
                              <span className="inline-flex cursor-default items-center rounded-[3px] border border-gray-300/80 bg-gray-200 px-2 py-0.5 text-[12px] font-medium text-gray-700">
                                +{displayTags.length - TAG_MAX_VISIBLE}
                              </span>
                            </Tooltip>
                          )}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center justify-start gap-3 sm:justify-end">
                    {canShowInteractButtons ? (
                      <Tooltip
                        title={isOwnSkill ? t('plugins.actions.selfInteractForbidden') : t('plugins.detail.likeCount')}
                        disableHoverListener={!isOwnSkill}
                      >
                        <span className="inline-flex">
                          <button
                            type="button"
                            onClick={() => void handleToggleInteract('like')}
                            disabled={interactBusy === 'like' || !canToggleInteract}
                            className={`inline-flex items-center gap-1.5 text-xs tabular-nums transition disabled:opacity-60 ${
                              canToggleInteract ? 'text-slate-500 hover:text-rose-500' : 'cursor-default text-slate-500'
                            }`}
                            title={!isOwnSkill ? t('plugins.detail.likeCount') : undefined}
                          >
                            <Heart
                              className={`h-4 w-4 shrink-0 ${
                                liked ? 'fill-rose-500 text-rose-500' : 'text-[#777777]'
                              }`}
                            />
                            {displayLikeCount}
                          </button>
                        </span>
                      </Tooltip>
                    ) : null}
                    {canShowDownloadActions ? (
                      <span
                        className="inline-flex items-center gap-1 text-[length:clamp(0.75rem,1.3vw,0.8125rem)] tabular-nums text-slate-500"
                        title={t('plugins.detail.installCount')}
                      >
                        <Download className="h-4 w-4 shrink-0 text-indigo-500" aria-hidden />
                        {displayInstallCount}
                      </span>
                    ) : null}
                    {canShowStarButton ? (
                      <Tooltip
                        title={isOwnSkill ? t('plugins.actions.selfInteractForbidden') : t('plugins.actions.favorite')}
                        disableHoverListener={!isOwnSkill}
                      >
                        <span className="inline-flex">
                          <button
                            type="button"
                            onClick={() => void handleToggleInteract('star')}
                            disabled={interactBusy === 'star' || !canToggleInteract}
                            className={`inline-flex items-center gap-1.5 text-xs tabular-nums transition disabled:opacity-60 ${
                              canToggleInteract ? 'text-slate-500 hover:text-amber-500' : 'cursor-default text-slate-500'
                            }`}
                            title={!isOwnSkill ? t('plugins.actions.favorite') : undefined}
                          >
                            <Star
                              className={`h-4 w-4 shrink-0 ${
                                starred ? 'fill-amber-400 text-amber-400' : 'text-[#777777]'
                              }`}
                            />
                            {displayStarCount}
                          </button>
                        </span>
                      </Tooltip>
                    ) : null}
                    <span
                      className="inline-flex items-center gap-1 text-[length:clamp(0.75rem,1.3vw,0.8125rem)] tabular-nums text-slate-500"
                      title={t('plugins.detail.viewCount')}
                    >
                      <Eye className="h-4 w-4 shrink-0 text-sky-600" aria-hidden />
                      {displayViewCount}
                    </span>
                    {publishResult !== 'publish_success' ? (
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-medium ${
                          publishResult === 'reviewing'
                            ? 'bg-sky-50 text-sky-800 ring-1 ring-sky-200'
                            : publishResult === 'pending_moderation'
                              ? 'bg-amber-50 text-amber-800 ring-1 ring-amber-200'
                              : 'bg-rose-50 text-rose-800 ring-1 ring-rose-200'
                        }`}
                      >
                        {publishResultLabel}
                      </span>
                    ) : null}
                    {canShowDownloadActions && !isAgentDetail && playgroundEnabled && moderationStatus === 'APPROVED' ? (
                      <button
                        type="button"
                        onClick={() => setPlaygroundOpen(true)}
                        className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[#C9D1FF] bg-white px-4 text-[13px] font-medium text-[#4F46E5] transition hover:bg-[#F7F8FF]"
                      >
                        <Play className="h-4 w-4 shrink-0" aria-hidden />
                        {t('plugins.actions.tryIt')}
                      </button>
                    ) : null}
                    {canShowDownloadActions ? (
                      <button
                        type="button"
                        onClick={() => void handleDownload()}
                        disabled={downloadLoading}
                        className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[linear-gradient(99.61deg,#1E54FA_0%,#842EFD_100%)] px-4 text-[13px] font-medium text-white shadow-[0_4px_12px_rgba(81,64,246,0.18)] transition-opacity hover:opacity-90 disabled:opacity-60"
                      >
                        <Download className="h-4 w-4 shrink-0" aria-hidden />
                        {t('plugins.actions.download')}
                      </button>
                    ) : null}
                  </div>
                </div>
              </header>

              <div className={`rounded-[16px] border border-[#E6E6E6] bg-white text-left ${cardInnerPad}`}>
                {canShowModerationPanel ? (
                  <section className="mb-6 rounded-lg border border-indigo-100 bg-indigo-50/60 px-4 py-3 sm:px-5">
                    <h2 className="text-sm font-semibold text-indigo-900">{t('plugins.skillPage.moderationHeading')}</h2>
                    <p className="mt-1 text-xs text-indigo-800/90">
                      {moderationLabel}
                      {moderationStatus === 'REJECTED' && moderationRejectReason
                        ? ` — ${moderationRejectReason}`
                        : ''}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={moderationBusy || moderationStatus === 'APPROVED'}
                        onClick={() => void runApprove()}
                        className="rounded-full bg-emerald-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-emerald-500 disabled:opacity-50"
                      >
                        {t('plugins.skillPage.approve')}
                      </button>
                      <button
                        type="button"
                        disabled={moderationBusy || moderationStatus === 'APPROVED'}
                        onClick={() => {
                          setRejectDraft('')
                          setRejectDialogOpen(true)
                        }}
                        className="rounded-full bg-rose-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-rose-500 disabled:opacity-50"
                      >
                        {t('plugins.skillPage.reject')}
                      </button>
                      {canShowReviewDetailLink && reviewDetailPath ? (
                        <ReviewDetailLink path={reviewDetailPath} tone="indigo" />
                      ) : null}
                    </div>
                  </section>
                ) : null}
                {publishResult === 'pending_moderation' && !canShowModerationPanel && canShowReviewDetailLink && reviewDetailPath ? (
                  <section className="mb-6 rounded-lg border border-amber-100 bg-amber-50/80 px-4 py-3 text-sm text-amber-900 sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-semibold">{t('profile.table.publishResult')}:</span> {publishResultLabel}
                      </div>
                      <ReviewDetailLink path={reviewDetailPath} tone="amber" />
                    </div>
                  </section>
                ) : null}
                {publishResult === 'reviewing' ? (
                  <section className="mb-6 rounded-lg border border-sky-100 bg-sky-50/80 px-4 py-3 text-sm text-sky-900 sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-semibold">{t('profile.table.publishResult')}:</span> {publishResultLabel}
                      </div>
                      {canShowReviewDetailLink && reviewDetailPath ? (
                        <ReviewDetailLink path={reviewDetailPath} tone="sky" />
                      ) : null}
                    </div>
                  </section>
                ) : null}
                {publishResult === 'publish_failed' && publishFailedReason && moderationStatus !== 'REJECTED' ? (
                  <section className="mb-6 rounded-lg border border-rose-100 bg-rose-50/80 px-4 py-3 text-sm text-rose-900 sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-semibold">{t('profile.table.publishResult')}:</span> {publishResultLabel}
                        <span> — {publishFailedReason}</span>
                      </div>
                      {canShowReviewDetailLink && reviewDetailPath ? (
                        <ReviewDetailLink path={reviewDetailPath} tone="rose" />
                      ) : null}
                    </div>
                  </section>
                ) : null}
                {moderationStatus === 'REJECTED' && moderationRejectReason && !canShowModerationPanel ? (
                  <section className="mb-6 rounded-lg border border-rose-100 bg-rose-50/80 px-4 py-3 text-sm text-rose-900 sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-semibold">{t('plugins.skillPage.rejectReasonLabel')}:</span>
                        {moderationRejectReason}
                      </div>
                      {canShowReviewDetailLink && reviewDetailPath ? (
                        <ReviewDetailLink path={reviewDetailPath} tone="rose" />
                      ) : null}
                    </div>
                  </section>
                ) : null}

                <div className={`grid gap-8 xl:items-start ${isAgentDetail ? '' : 'xl:grid-cols-[minmax(0,1fr)_296px]'}`}>
                  <div className="min-w-0">
                    {/* Basic info */}
                    <section>
                      <h2 className="mb-4 text-[16px] font-semibold leading-[22px] text-[#191919]">
                        {t('plugins.skillPage.basicInfo')}
                      </h2>
                      <div className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-3">
                        <div>
                          <div className="text-[12px] text-[#8C8C8C]">{t('plugins.skillPage.fieldName')}</div>
                          <div className="mt-1 text-[13px] font-medium text-[#404040]">{skill.displayName}</div>
                        </div>
                        <div>
                          <div className="text-[12px] text-[#8C8C8C]">{t('plugins.skillPage.fieldPublisher')}</div>
                          <div className="mt-1 text-[13px] text-[#404040]">{skill.publisherName || '—'}</div>
                        </div>
                        <div>
                          <div className="text-[12px] text-[#8C8C8C]">{t('plugins.skillPage.fieldUpdatedAt')}</div>
                          <div className="mt-1 text-[13px] text-[#404040]">
                            {formatDate(displayUpdateTime, i18n.language)}
                          </div>
                        </div>
                      </div>
                      <div className="mt-5 border-t border-[#F0F0F0] pt-4">
                        <h3 className="mb-2 text-[14px] font-semibold leading-5 text-[#404040]">
                          {t('plugins.skillPage.descriptionHeading')}
                        </h3>
                        <p className="text-[13px] leading-[22px] text-[#666666]">{skill.shortDesc || '—'}</p>
                      </div>
                    </section>

                    {/* Version selector */}
                    <div className="mt-6 flex items-center gap-3">
                      <select
                        value={selectedVersion}
                        onChange={e => setSelectedVersion(e.target.value)}
                        className={[
                          'h-8 min-w-[140px] rounded-[5px] border border-[#E6E6E6] bg-white px-3 text-[13px] text-[#404040]',
                          'outline-none focus:border-[#9DB2FF] focus:ring-2 focus:ring-[#E8EDFF]',
                        ].join(' ')}
                      >
                        {(versionList.length ? versionList : [skill.latestVersion || '']).map(v => (
                          <option key={v || 'empty'} value={v}>
                            {v
                              ? skillVersionSelectLabel(
                                  v,
                                  skill,
                                  skill.skillVersionPublishResult,
                                  skill.skillVersionModeration,
                                  t,
                                )
                              : '-'}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Tab navigation */}
                    <div className="mt-6 border-b border-[#EDEDED]">
                      <nav className="-mb-px flex" role="tablist">
                        {(['readme', 'files', 'changelog'] as const).map(tab => {
                          const isZh = i18n.language.startsWith('zh')
                          const label =
                            tab === 'readme'
                              ? isZh ? '详细说明' : 'Readme'
                              : tab === 'files'
                              ? isZh ? '文件' : 'Files'
                              : t('plugins.skillPage.versionChangelog')
                          return (
                            <button
                              key={tab}
                              role="tab"
                              aria-selected={activeTab === tab}
                              onClick={() => setActiveTab(tab)}
                              className={`mr-7 border-b-2 py-2.5 text-[14px] font-medium transition ${
                                activeTab === tab
                                  ? 'border-[#4F46E5] text-[#4F46E5]'
                                  : 'border-transparent text-[#808080] hover:border-[#DCDCDC] hover:text-[#404040]'
                              }`}
                            >
                              {label}
                            </button>
                          )
                        })}
                      </nav>
                    </div>

                    {/* Tab panels */}
                    <div className="mt-5">
                  {activeTab === 'readme' && (
                    <div className="prose prose-slate max-w-none text-sm prose-headings:font-semibold prose-a:text-indigo-600 prose-pre:bg-slate-100 prose-pre:text-slate-800 prose-table:text-sm prose-img:rounded-lg sm:text-base">
                      {(() => {
                        const desc = detailDescFromApi ?? skill.detailDesc
                        return desc ? (
                          <PluginMarkdown source={desc} variant="detail" className="leading-relaxed text-slate-700" />
                        ) : (
                          <p className="text-sm text-slate-500">{t('plugins.noDescription')}</p>
                        )
                      })()}
                    </div>
                  )}

                  {activeTab === 'changelog' && (
                    <div className="rounded-lg border border-slate-100 bg-slate-50/80 px-4 py-3 text-sm text-slate-600 sm:px-5">
                      {changelogLoading ? (
                        <span>{t('plugins.detail.changelogLoading')}</span>
                      ) : changelogError ? (
                        <span className="text-rose-600">{changelogError}</span>
                      ) : changelog ? (
                        <PluginMarkdown source={changelog} variant="detail" className="prose prose-sm max-w-none text-slate-700" />
                      ) : (
                        <span>{t('plugins.detail.changelogEmpty')}</span>
                      )}
                    </div>
                  )}

                      {activeTab === 'files' && (
                        <div className="flex h-[480px] overflow-hidden rounded-lg border border-slate-200">
                      {/* File list */}
                      <div className="w-64 shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50">
                        {fileListLoading ? (
                          <div className="flex h-full items-center justify-center">
                            <CircularProgress size={20} />
                          </div>
                        ) : fileListError ? (
                          <p className="p-3 text-xs text-rose-600">{fileListError}</p>
                        ) : !fileList ? (
                          <p className="p-3 text-xs text-slate-400">{t('plugins.detail.filesLoading')}</p>
                        ) : fileList.length === 0 ? (
                          <p className="p-3 text-xs text-slate-400">{t('plugins.detail.filesEmpty')}</p>
                        ) : (
                          <ul className="py-1">
                            {fileList.map(f => (
                              <li key={f.path}>
                                <button
                                  type="button"
                                  onClick={() => handleFileClick(f.path)}
                                  className={`w-full truncate px-3 py-1.5 text-left text-xs transition ${
                                    selectedFile === f.path
                                      ? 'bg-indigo-50 font-medium text-indigo-700'
                                      : 'text-slate-700 hover:bg-slate-100'
                                  }`}
                                  title={f.path}
                                >
                                  {f.path}
                                </button>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>

                      {/* File content */}
                      <div className="min-w-0 flex-1 overflow-auto bg-white">
                        {!selectedFile ? (
                          <div className="flex h-full items-center justify-center text-sm text-slate-400">
                            {t('plugins.detail.filesSelectHint')}
                          </div>
                        ) : !isTextFile(selectedFile) ? (
                          <div className="flex h-full items-center justify-center text-sm text-slate-400">
                            {t('plugins.detail.filesBinaryHint')}
                          </div>
                        ) : fileContentLoading ? (
                          <div className="flex h-full items-center justify-center">
                            <CircularProgress size={20} />
                          </div>
                        ) : fileContentError ? (
                          <div className="p-4 text-sm text-rose-600">{fileContentError}</div>
                        ) : selectedFile.toLowerCase().endsWith('.md') ? (
                          <div className="p-4">
                            <PluginMarkdown
                              source={fileContent}
                              variant="detail"
                              mermaid
                              className="prose prose-sm max-w-none text-slate-700"
                            />
                          </div>
                        ) : (
                          <pre className="whitespace-pre-wrap break-words p-4 font-mono text-xs text-slate-800">
                            {fileContent}
                          </pre>
                        )}
                      </div>
                    </div>
                      )}
                    </div>
                  </div>
                  {!isAgentDetail ? (
                    <aside className="xl:sticky xl:top-6">
                      <SecurityReviewPanel
                        review={securityReview}
                        detailPath={canShowReviewDetailLink ? reviewDetailPath : ''}
                        t={t}
                      />
                    </aside>
                  ) : null}
                </div>
              </div>
            </article>
          ) : null}
        </div>
      </main>
      {rejectDialogOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
            <h3 className="text-base font-semibold text-slate-900">{t('plugins.skillPage.rejectDialogTitle')}</h3>
            <textarea
              value={rejectDraft}
              onChange={e => setRejectDraft(e.target.value)}
              rows={4}
              className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-200"
              placeholder={t('plugins.skillPage.rejectReasonPlaceholder')}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-full border border-slate-200 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => {
                  setRejectDialogOpen(false)
                  setRejectDraft('')
                }}
                disabled={moderationBusy}
              >
                {t('plugins.skillPage.cancel')}
              </button>
              <button
                type="button"
                className="rounded-full bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-50"
                onClick={() => void submitReject()}
                disabled={moderationBusy}
              >
                {t('plugins.skillPage.submitModeration')}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {!isAgentDetail && playgroundEnabled && skill ? (
        <PlaygroundDrawer
          open={playgroundOpen}
          skillId={skill.assetId}
          version={selectedVersion || skill.latestVersion || 'latest'}
          skillType={skillRaw?.plugin_type === 'swarmskill' ? 'swarm' : 'ordinary'}
          skillName={skill.displayName}
          onClose={() => setPlaygroundOpen(false)}
        />
      ) : null}
    </div>
  )
}
