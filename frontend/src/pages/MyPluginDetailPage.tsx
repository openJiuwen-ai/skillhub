// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Typography,
} from '@mui/material'
import { AppHeader } from '@/components/Common/AppHeader'
import { useQuery, useQueryClient } from 'react-query'
import {
  deletePluginAllVersions,
  deletePluginVersion,
  getPluginVersionDetail,
  getPlugins,
  getSkillLikeEffectiveModeration,
  getSkillLikeVersionModerationMap,
  getSkillLikeVersionPublishResultMap,
  MarketplaceApiError,
  type MarketplacePluginItem,
} from '@/api/plugin'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { formatSkillVersionLabel } from '@/utils/formatSkillVersionLabel'
import { SKILL_LIKE_QUERY_VALUE, isSkillLikePluginType } from '@/utils/pluginType'

type Translate = (key: string, options?: Record<string, unknown>) => string

function skillPublishStatusText(
  publishResult: string | null | undefined,
  moderationStatus: string | null | undefined,
  t: Translate,
): string {
  const pr = String(publishResult || '').trim().toLowerCase()
  const ms = (moderationStatus || '').toString().toUpperCase()
  if (pr === 'reviewing') return t('profile.publishResultReviewing')
  if (pr === 'pending_moderation') return t('profile.publishResultPendingModeration')
  if (pr === 'publish_failed') {
    return ms === 'REJECTED' ? t('profile.publishResultFailedModeration') : t('profile.publishResultFailedSystem')
  }
  return t('profile.publishResultSuccess')
}

function profileVersionMenuLabel(
  version: string,
  summaryItem: MarketplacePluginItem | undefined,
  publishResultMap: Record<string, string> | null | undefined,
  modMap: Record<string, string> | null | undefined,
  t: Translate,
): string {
  const label = formatSkillVersionLabel(version, {
    gitVersionDisplayAsCommit:
      Boolean(summaryItem?.git_version_display_as_commit) &&
      version.trim() === (summaryItem?.latest_version || '').trim(),
    resolvedCommitSha: summaryItem?.resolved_commit_sha,
    declaredSkillVersion: summaryItem?.declared_skill_version,
    storageMode: summaryItem?.storage_mode,
  })
  const pr = (publishResultMap?.[version] || '').toString().trim().toLowerCase()
  const u = (modMap?.[version] || '').toString().toUpperCase()
  if (pr === 'reviewing') return `${label} · ${t('profile.publishResultReviewing')}`
  if (pr === 'pending_moderation') return `${label} · ${t('profile.publishResultPendingModeration')}`
  if (pr === 'publish_failed') {
    return `${label} · ${
      u === 'REJECTED' ? t('profile.publishResultFailedModeration') : t('profile.publishResultFailedSystem')
    }`
  }
  return label
}

export default function MyPluginDetailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { assetId: assetIdParam } = useParams<{ assetId: string }>()
  const assetId = assetIdParam ? decodeURIComponent(assetIdParam) : ''
  const { user, isAuthenticated, logout } = useGitCodeAuth()
  const stateVersion = (location.state as { latestVersion?: string } | null)?.latestVersion
  const queryVersion = useMemo(() => new URLSearchParams(location.search).get('version')?.trim() || null, [location.search])

  const [selectedVersion, setSelectedVersion] = useState<string | null>(null)
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [deleteOneOpen, setDeleteOneOpen] = useState(false)
  const [versionToDelete, setVersionToDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/profile/plugins/${encodeURIComponent(assetId)}`)
    navigate('/login', { replace: true })
  }, [isAuthenticated, navigate, assetId])

  const {
    data: summaryRes,
    isLoading: summaryLoading,
    error: summaryError,
    refetch: refetchSummary,
  } = useQuery(
    ['my-plugin-summary', assetId, user?.id],
    () =>
      getPlugins({
        publisher_id: user!.id,
        asset_id: assetId,
        page: 1,
        page_size: 1,
        plugin_type: SKILL_LIKE_QUERY_VALUE,
      }),
    {
      enabled: Boolean(assetId && user?.id),
    },
  )

  const summaryItem = summaryRes?.data?.items?.[0]
  const skillVersionModMap = getSkillLikeVersionModerationMap(summaryItem)
  const skillVersionPublishResultMap = getSkillLikeVersionPublishResultMap(summaryItem)
  const allVersions = useMemo(() => {
    const raw = summaryItem?.all_versions
    if (Array.isArray(raw) && raw.length > 0) return raw
    const lv = summaryItem?.latest_version?.trim()
    return lv ? [lv] : []
  }, [summaryItem])

  /** 与列表同步：保持选中版本在 all_versions 内；初次用路由 state / latest 兜底 */
  useEffect(() => {
    if (allVersions.length === 0) {
      setSelectedVersion(null)
      return
    }
    setSelectedVersion(prev => {
      if (prev && allVersions.includes(prev)) return prev
      if (queryVersion && allVersions.includes(queryVersion)) return queryVersion
      const hint = stateVersion?.trim()
      if (hint && allVersions.includes(hint)) return hint
      const latest = summaryItem?.latest_version?.trim()
      if (latest && allVersions.includes(latest)) return latest
      return allVersions[allVersions.length - 1]
    })
  }, [allVersions, summaryItem?.latest_version, summaryItem?.asset_id, stateVersion, queryVersion])

  const { data: detail, isLoading: detailLoading, error } = useQuery(
    ['my-plugin-version', assetId, selectedVersion],
    () => getPluginVersionDetail(assetId, selectedVersion!),
    {
      enabled: Boolean(assetId && selectedVersion && user?.id),
    },
  )

  const reviewSummary = useMemo(() => {
    const summary = (detail?.review_summary ?? {}) as {
      status?: string | null
      risk?: string | null
      overall?: string | null
      conclusion?: string | null
      finished_at?: number | null
      failed_count?: number | null
      attention_count?: number | null
      dimension_count?: number | null
    }
    return {
      status: String(summary.status || ''),
      overall: summary.overall ? String(summary.overall) : '',
      risk: summary.risk ? String(summary.risk) : '',
      conclusion: String(summary.conclusion || ''),
      reviewedAt: summary.finished_at || null,
      failedCount: typeof summary.failed_count === 'number' ? summary.failed_count : 0,
      attentionCount: typeof summary.attention_count === 'number' ? summary.attention_count : 0,
      dimensionCount: typeof summary.dimension_count === 'number' ? summary.dimension_count : 0,
      sections: Array.isArray(detail?.review_sections) ? detail.review_sections : [],
    }
  }, [detail])

  const hasTerminalReview = useMemo(
    () => isTerminalReviewStatus(reviewSummary.status) && reviewSummary.sections.length > 0,
    [reviewSummary.sections.length, reviewSummary.status],
  )

  /**
   * GET 插件版本详情为公开接口，非所有者仍能拿到 detail；此处仅用于隐藏删除等写操作。
   * 若日后对 GET 加鉴权并返回 403，需走错误态文案。
   */
  const forbidden =
    detail && user?.id && detail.publisher_id !== user.id ? true : false

  const summaryErrMsg = useMemo(() => {
    if (!summaryError) return ''
    if (summaryError instanceof MarketplaceApiError) return summaryError.message
    return summaryError instanceof Error ? summaryError.message : String(summaryError)
  }, [summaryError])

  const errMsg = useMemo(() => {
    if (!error) return ''
    if (error instanceof MarketplaceApiError) return error.message
    return error instanceof Error ? error.message : String(error)
  }, [error])

  const notFound = !summaryLoading && !summaryItem && !summaryErrMsg

  const invalidateSkillCaches = async (assetId: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['my-published-skills'] }),
      queryClient.invalidateQueries({ queryKey: ['my-group-skills'] }),
      queryClient.invalidateQueries({ queryKey: ['plugins'] }),
      queryClient.invalidateQueries({ queryKey: ['my-plugin-summary'] }),
      queryClient.invalidateQueries({ queryKey: ['group'] }),
      queryClient.invalidateQueries({ queryKey: ['group-grants'] }),
      queryClient.invalidateQueries({ queryKey: ['group-grantable-skills'] }),
      queryClient.invalidateQueries({ queryKey: ['group-grant-states'] }),
      queryClient.invalidateQueries({ queryKey: ['asset-detail-raw', assetId] }),
    ])
  }

  const handleDeleteAll = async () => {
    if (!assetId || !user?.id) return
    setDeleting(true)
    try {
      await deletePluginAllVersions(assetId)
      setDeleteAllOpen(false)
      await invalidateSkillCaches(assetId)
      queryClient.removeQueries({ queryKey: ['my-plugin-version', assetId] })
      navigate('/profile', { replace: true })
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('profile.deleteFailed')
      window.alert(msg)
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteOne = async () => {
    if (!assetId || !user?.id || !versionToDelete) return
    setDeleting(true)
    const deleted = versionToDelete
    try {
      await deletePluginVersion(assetId, deleted)
      setDeleteOneOpen(false)
      setVersionToDelete(null)

      queryClient.removeQueries({ queryKey: ['my-plugin-version', assetId, deleted], exact: true })

      const rest = allVersions.filter(v => v !== deleted)
      if (rest.length === 0) {
        await invalidateSkillCaches(assetId)
        navigate('/profile', { replace: true })
        return
      }

      const nextSel =
        selectedVersion && selectedVersion !== deleted && rest.includes(selectedVersion)
          ? selectedVersion
          : rest[rest.length - 1]
      setSelectedVersion(nextSel)

      const [sumResult] = await Promise.all([
        refetchSummary(),
        queryClient.refetchQueries({ queryKey: ['my-plugin-version', assetId, nextSel], exact: true }),
      ])
      await invalidateSkillCaches(assetId)

      if (!sumResult.data?.data?.items?.[0]) {
        navigate('/profile', { replace: true })
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('profile.deleteFailed')
      window.alert(msg)
    } finally {
      setDeleting(false)
    }
  }

  const openDeleteOne = useCallback((v: string) => {
    setVersionToDelete(v)
    setDeleteOneOpen(true)
  }, [])

  const versionsNewestFirst = useMemo(() => [...allVersions].reverse(), [allVersions])
  const reviewSummaryPanel =
    isSkillLikePluginType(detail?.plugin_type) && hasTerminalReview ? (
      <div className="mb-4 rounded-xl border border-slate-200/90 bg-slate-50/90 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <Typography variant="subtitle2" className="font-bold text-slate-900">
              {t('profile.reviewSummary')}
            </Typography>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-medium ${
                  reviewSummary.overall === 'approved'
                    ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
                    : 'border-red-100 bg-red-50 text-red-700'
                }`}
              >
                {reviewSummary.overall === 'approved' ? t('profile.reviewApproved') : t('profile.reviewRejected')}
              </span>
              <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
                {reviewSummary.risk ? t(`profile.risk.${reviewSummary.risk}`, reviewSummary.risk) : t('profile.risk.unknown')}
              </span>
            </div>
          </div>
          <Button
            size="small"
            variant="outlined"
            disabled={!selectedVersion}
            onClick={() =>
              selectedVersion &&
              navigate(
                `/profile/plugins/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(selectedVersion)}/review`,
              )
            }
            sx={{ textTransform: 'none' }}
          >
            {t('profile.viewReviewDetail')}
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <ReviewMetaItem
            label={t('profile.reviewDimensions')}
            value={t('profile.reviewCheckCountShort', {
              count: reviewSummary.dimensionCount || reviewSummary.sections.length,
            })}
          />
          <ReviewMetaItem
            label={t('profile.reviewStatusFailed')}
            value={t('profile.reviewCheckCountShort', { count: reviewSummary.failedCount })}
          />
          <ReviewMetaItem
            label={t('profile.reviewStatusAttention')}
            value={t('profile.reviewCheckCountShort', { count: reviewSummary.attentionCount })}
          />
        </div>
      </div>
    ) : null

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-gradient-to-br from-[#f8fbff] via-[#f6faff] to-[#eef4ff]">
        <Typography variant="body2" color="text.secondary">
          {t('profile.redirecting')}
        </Typography>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col overflow-x-hidden bg-white">
      <AppHeader />

      <div className="border-b border-slate-100 bg-white px-[8.33%] py-3">
        <Breadcrumbs
          items={[
            { label: t('common.breadcrumb.home'), to: '/' },
            { label: t('common.breadcrumb.profile'), to: '/profile' },
            {
              label:
                (summaryItem?.display_name || summaryItem?.displayName || summaryItem?.name || '').trim() ||
                t('common.breadcrumb.pluginDetail'),
            },
          ]}
        />
      </div>

      <main className="relative z-10 flex-1 px-[8.33%] py-6 pb-12">
        {summaryErrMsg ? (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{summaryErrMsg}</div>
        ) : null}
        {notFound ? (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {t('profile.pluginNotFound')}
          </div>
        ) : null}
        {summaryLoading ? (
          <Typography variant="body2" className="text-slate-500">
            {t('plugins.loading')}
          </Typography>
        ) : summaryItem && allVersions.length === 0 ? (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {t('profile.missingVersion')}
          </div>
        ) : null}

        {summaryItem && allVersions.length > 0 ? (
          <div className="rounded-2xl border border-slate-200/80 bg-white/95 p-6 shadow-sm">
            <Typography variant="h5" className="mb-1 font-bold text-slate-900">
              {summaryItem.display_name || summaryItem.displayName || summaryItem.name}
            </Typography>

            <div className="mb-4 mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
              <FormControl size="small" className="min-w-[220px] flex-1" sx={{ maxWidth: 360 }}>
                <InputLabel id="profile-plugin-version-label">{t('profile.selectVersion')}</InputLabel>
                <Select
                  labelId="profile-plugin-version-label"
                  label={t('profile.selectVersion')}
                  value={selectedVersion ?? ''}
                  onChange={e => setSelectedVersion(String(e.target.value))}
                >
                  {versionsNewestFirst.map(v => (
                    <MenuItem key={v} value={v}>
                      {profileVersionMenuLabel(v, summaryItem, skillVersionPublishResultMap, skillVersionModMap, t)}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              {!forbidden ? (
                <Button
                  color="error"
                  variant="outlined"
                  size="small"
                  disabled={!selectedVersion}
                  startIcon={<Trash2 className="h-4 w-4" aria-hidden />}
                  onClick={() => selectedVersion && openDeleteOne(selectedVersion)}
                  sx={{ textTransform: 'none', flexShrink: 0 }}
                >
                  {t('profile.deleteVersion')}
                </Button>
              ) : null}
            </div>

            {errMsg ? (
              <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errMsg}</div>
            ) : null}
            {forbidden ? (
              <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {t('profile.notOwner')}
              </div>
            ) : null}

            {detailLoading && selectedVersion ? (
              <Typography variant="body2" className="mb-4 text-slate-500">
                {t('plugins.loading')}
              </Typography>
            ) : null}

            {detail ? (
              <>
                <div className="mb-4 space-y-1 text-sm text-slate-700">
                  <div>
                    <span className="font-medium text-slate-900">{t('plugins.detail.publisher')}: </span>
                    {detail.publisher_name}
                  </div>
                  {detail.plugin_type ? (
                    <div>
                      <span className="font-medium text-slate-900">{t('plugins.detail.runtime')}: </span>
                      {detail.plugin_type}
                    </div>
                  ) : null}
                  {(() => {
                    const effectiveModeration = getSkillLikeEffectiveModeration(detail)
                    return isSkillLikePluginType(detail.plugin_type) ? (
                      <div>
                        <span className="font-medium text-slate-900">{t('profile.moderationStatusLabel')}: </span>
                        {skillPublishStatusText(detail.publish_result, effectiveModeration.moderationStatus, t)}
                        {effectiveModeration.moderationStatus === 'REJECTED' && effectiveModeration.moderationRejectReason ? (
                          <span className="text-rose-700"> — {effectiveModeration.moderationRejectReason}</span>
                        ) : detail.publish_result === 'publish_failed' && detail.publish_failed_reason?.trim() ? (
                          <span className="text-rose-700"> — {detail.publish_failed_reason.trim()}</span>
                        ) : null}
                      </div>
                    ) : null
                  })()}

                </div>

                {reviewSummaryPanel}

                {detail.short_desc ? (
                  <div className="mb-4">
                    <Typography variant="subtitle2" className="mb-1 font-bold text-slate-900">
                      {t('plugins.detail.summary')}
                    </Typography>
                    <PluginMarkdown
                      source={detail.short_desc}
                      className="prose prose-sm prose-neutral max-w-none text-slate-800 prose-p:my-1"
                    />
                  </div>
                ) : null}

                {detail.detail_desc ? (
                  <div className="mb-4">
                    <Typography variant="subtitle2" className="mb-1 font-bold text-slate-900">
                      {t('plugins.detail.description')}
                    </Typography>
                    <PluginMarkdown
                      source={detail.detail_desc}
                      className="prose prose-sm prose-neutral max-w-none text-slate-800 prose-p:my-1"
                    />
                  </div>
                ) : null}

                <div className="mb-4 rounded-xl border border-slate-200/90 bg-slate-50/90 p-4">
                  <Typography variant="subtitle2" className="mb-2 font-bold text-slate-900">
                    {t('profile.changelog')} ·{' '}
                    {formatSkillVersionLabel(detail.version, {
                      gitVersionDisplayAsCommit: detail.git_version_display_as_commit,
                      resolvedCommitSha: detail.resolved_commit_sha,
                      declaredSkillVersion: detail.declared_skill_version,
                      storageMode: detail.storage_mode,
                    })}
                  </Typography>
                  {detail.changelog?.trim() ? (
                    <PluginMarkdown
                      source={detail.changelog.trim()}
                      className="prose prose-sm prose-neutral max-w-none max-h-64 overflow-y-auto text-slate-800 prose-p:my-1 prose-headings:my-2 prose-headings:scroll-mt-2 [&_p]:text-[0.9375rem]"
                    />
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {t('profile.changelogEmpty')}
                    </Typography>
                  )}
                </div>
              </>
            ) : !detailLoading && selectedVersion && !errMsg ? (
              <Typography variant="body2" className="mb-4 text-slate-500">
                {t('profile.noDetail')}
              </Typography>
            ) : null}

            {!forbidden ? (
              <Button
                color="error"
                variant="outlined"
                onClick={() => setDeleteAllOpen(true)}
                sx={{ textTransform: 'none' }}
              >
                {t('profile.deleteAll')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </main>

      <Dialog open={deleteAllOpen} onClose={() => !deleting && setDeleteAllOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('profile.deleteConfirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" className="text-slate-700">
            {t('profile.deleteConfirmBody')}
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteAllOpen(false)} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('common.buttons.close')}
          </Button>
          <Button color="error" variant="contained" onClick={() => void handleDeleteAll()} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('profile.deleteConfirmAction')}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteOneOpen} onClose={() => !deleting && setDeleteOneOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('profile.deleteVersionConfirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" className="text-slate-700">
            {t('profile.deleteVersionConfirmBody', {
              version: formatSkillVersionLabel(versionToDelete ?? '', {
                gitVersionDisplayAsCommit:
                  Boolean(detail?.git_version_display_as_commit) &&
                  (versionToDelete ?? '').trim() === (detail?.version || '').trim(),
                resolvedCommitSha: detail?.resolved_commit_sha,
                declaredSkillVersion: detail?.declared_skill_version,
                storageMode: detail?.storage_mode,
              }),
            })}
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteOneOpen(false)} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('common.buttons.close')}
          </Button>
          <Button color="error" variant="contained" onClick={() => void handleDeleteOne()} disabled={deleting} sx={{ textTransform: 'none' }}>
            {t('profile.deleteVersionConfirmAction')}
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}

function normalizeReviewConclusion(value?: string | null) {
  const text = String(value || '').trim()
  if (!text) return ''
  if (text === '审查通过，当前提交可以进入发布流程。') return ''
  return text.replace('建议确认后再发布。', '建议确认。')
}

function isTerminalReviewStatus(value?: string | null) {
  const status = String(value || '').trim().toLowerCase()
  return status === 'passed' || status === 'failed' || status === 'system_failed'
}

function ReviewMetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
      <div className="mb-1 text-xs font-bold uppercase tracking-[0.06em] text-slate-500">{label}</div>
      <div className="text-sm font-medium leading-6 text-slate-800">{value}</div>
    </div>
  )
}
