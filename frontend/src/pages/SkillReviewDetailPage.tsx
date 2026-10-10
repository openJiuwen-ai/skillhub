import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { Typography } from '@mui/material'
import { useQuery } from 'react-query'
import { getPluginVersionDetail, MarketplaceApiError } from '@/api/plugin'
import { AppHeader } from '@/components/Common/AppHeader'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { formatSkillVersionLabel } from '@/utils/formatSkillVersionLabel'
import { isSkillLikePluginType } from '@/utils/pluginType'

type Translate = (key: string, options?: Record<string, unknown>) => string

type ReviewFinding = {
  id: string
  sourceType: string
  sourceLabel: string
  title: string
  description: string
  recommendation: string
  severity: 'high' | 'medium' | 'low' | 'info'
  category: string
  capability: string
  confidence: string
  locationLabel: string
  snippet: string
  semanticEvidence: string
}

type ReviewCheck = {
  id: string
  checkId: string
  name: string
  summary: string
  target: string
  scope: string
  status: 'failed' | 'attention' | 'passed'
  findingCount: number
  highRiskCount: number
  sourceLabels: string[]
  findings: ReviewFinding[]
  recommendation: string
  resultSummary: string
  severity: 'high' | 'medium' | 'low' | 'info'
}

type ReviewSection = {
  key: string
  title: string
  status: 'failed' | 'attention' | 'passed'
  checks: ReviewCheck[]
  checkCount: number
  findingCount: number
  highRiskCount: number
  failedCheckCount: number
  attentionCheckCount: number
  passedCheckCount: number
  recommendation: string
}

type SemanticReviewSummary = {
  status: 'completed' | 'fallback' | 'disabled' | 'failed'
  engine: string
  modelName: string
  displayMessage: string
  conclusion: string
  findingCount: number
}

const STATUS_META = {
  failed: { labelKey: 'profile.reviewStatusFailed', pill: 'bg-red-50 text-red-700 border-red-100', symbol: '✗' },
  attention: { labelKey: 'profile.reviewStatusAttention', pill: 'bg-amber-50 text-amber-700 border-amber-100', symbol: '!' },
  passed: { labelKey: 'profile.reviewStatusPassed', pill: 'bg-emerald-50 text-emerald-700 border-emerald-100', symbol: '✓' },
} as const
const SEVERITY_PRIORITY = { high: 3, medium: 2, low: 1, info: 0 } as const
const SOURCE_PRIORITY: Record<string, number> = { rule: 0, scanner: 1, system: 2, agent: 3, ai: 3 }
const CHECK_STATUS_PRIORITY = { failed: 2, attention: 1, passed: 0 } as const

const SEVERITY_META = {
  high: { labelKey: 'profile.reviewSeverityHigh', pill: 'bg-red-50 text-red-700 border-red-100' },
  medium: { labelKey: 'profile.reviewSeverityMedium', pill: 'bg-amber-50 text-amber-700 border-amber-100' },
  low: { labelKey: 'profile.reviewSeverityLow', pill: 'bg-blue-50 text-blue-700 border-blue-100' },
  info: { labelKey: 'profile.reviewSeverityInfo', pill: 'bg-slate-50 text-slate-600 border-slate-100' },
} as const
const PAGE_ALIGN_WITH_HEADER = 'px-4 md:px-[8.33%]'
const CARD_INNER_PAD = 'px-5 py-4 sm:px-6 sm:py-5 md:px-8 md:py-6'

const CATEGORY_LABEL_KEY: Record<string, string> = {
  dangerous_script_or_code: 'profile.reviewCategoryDangerousScriptOrCode',
  prompt_injection: 'profile.reviewCategoryPromptInjection',
  sensitive_info_exposure: 'profile.reviewCategorySensitiveInfoExposure',
  permission_boundary_violation: 'profile.reviewCategoryPermissionBoundaryViolation',
  destructive_action: 'profile.reviewCategoryDestructiveAction',
  data_exfiltration: 'profile.reviewCategoryDataExfiltration',
  suspicious_external_endpoint: 'profile.reviewCategorySuspiciousExternalEndpoint',
  resource_exhaustion: 'profile.reviewCategoryResourceExhaustion',
  filesystem_boundary_violation: 'profile.reviewCategoryFilesystemBoundaryViolation',
  unsafe_deserialization: 'profile.reviewCategoryUnsafeDeserialization',
  abusive_automation: 'profile.reviewCategoryAbusiveAutomation',
  input_interpretation_injection: 'profile.reviewCategoryInputInterpretationInjection',
}

export default function SkillReviewDetailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { assetId: assetIdParam, version: versionParam } = useParams<{ assetId: string; version: string }>()
  const assetId = assetIdParam ? decodeURIComponent(assetIdParam) : ''
  const version = versionParam ? decodeURIComponent(versionParam) : ''
  const { user, isAuthenticated } = useGitCodeAuth()

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/profile/plugins/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(version)}/review`)
    navigate('/login', { replace: true })
  }, [isAuthenticated, navigate, assetId, version])

  const { data: detail, isLoading, error } = useQuery(
    ['skill-review-detail', assetId, version],
    () => getPluginVersionDetail(assetId, version),
    { enabled: Boolean(assetId && version && user?.id), staleTime: 0 },
  )

  const reviewResult = useMemo(() => {
    const summary = (detail?.review_summary || {}) as Record<string, any>
    return {
      status: String(summary.status || ''),
      failedReason:
        typeof summary.failed_reason === 'string' && summary.failed_reason.trim()
          ? summary.failed_reason.trim()
          : typeof detail?.publish_failed_reason === 'string' && detail.publish_failed_reason.trim()
            ? detail.publish_failed_reason.trim()
            : '',
      overall: summary.overall ? String(summary.overall) : '',
      risk: summary.risk ? String(summary.risk) : '',
      conclusion: String(summary.conclusion || ''),
      score: typeof summary.score === 'number' ? summary.score : null,
      reviewedAt: summary.finished_at || null,
      sections: Array.isArray(detail?.review_sections) ? detail.review_sections : [],
      semanticReview: summary.semantic_review || null,
    }
  }, [detail])

  const evidenceState = useMemo(() => buildEvidenceState(reviewResult, t), [reviewResult, t])
  const semanticReview = useMemo(() => normalizeSemanticReview(reviewResult.semanticReview, t), [reviewResult.semanticReview, t])
  const hasTerminalReview = isTerminalReviewStatus(reviewResult.status) && evidenceState.sections.length > 0
  const reviewDetailUnavailable =
    isSkillLikePluginType(detail?.plugin_type) &&
    !hasTerminalReview &&
    !reviewResult.status &&
    reviewResult.sections.length === 0 &&
    detail?.publish_result !== 'reviewing'
  const [activeSectionKey, setActiveSectionKey] = useState(evidenceState.defaultSectionKey)
  const [activeCheckId, setActiveCheckId] = useState(evidenceState.defaultCheckId)
  const sectionScrollRef = useRef<HTMLDivElement | null>(null)
  const checkScrollRef = useRef<HTMLDivElement | null>(null)
  const detailScrollRef = useRef<HTMLDivElement | null>(null)
  const isDetailOwner = Boolean(detail?.publisher_id && user?.id && String(detail.publisher_id) === String(user.id))
  const encodedAssetId = encodeURIComponent(assetId)
  const encodedVersion = encodeURIComponent(version)
  const skillDetailQuery = detail?.version_moderation_status === 'PENDING' || detail?.publish_result === 'pending_moderation'
    ? `?version=${encodedVersion}&moderation_status=PENDING`
    : `?version=${encodedVersion}`
  const skillDetailPath = isDetailOwner
    ? `/profile/plugins/${encodedAssetId}?version=${encodedVersion}`
    : `/skills/${encodedAssetId}${skillDetailQuery}`
  const reviewTitleName = detail?.display_name || detail?.name || (isLoading ? t('plugins.loading') : t('profile.reviewDetail'))

  useEffect(() => {
    setActiveSectionKey(evidenceState.defaultSectionKey)
    setActiveCheckId(evidenceState.defaultCheckId)
    scrollToTop(sectionScrollRef)
    scrollToTop(checkScrollRef)
    scrollToTop(detailScrollRef)
  }, [evidenceState.defaultSectionKey, evidenceState.defaultCheckId])

  const activeSection = evidenceState.sections.find(section => section.key === activeSectionKey) || evidenceState.sections[0] || null
  const activeCheck = activeSection?.checks.find(check => check.id === activeCheckId) || activeSection?.checks[0] || null

  const errMsg = useMemo(() => {
    if (!error) return ''
    if (error instanceof MarketplaceApiError) return error.message
    return error instanceof Error ? error.message : String(error)
  }, [error])

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
    <div className="flex min-h-dvh flex-col overflow-hidden bg-white">
      <AppHeader showPublish={false} />
      <div className="min-h-0 flex-1 overflow-y-auto bg-gradient-to-br from-[#E3F2FD] to-[#F3E9FF]">
        <div className={`w-full ${PAGE_ALIGN_WITH_HEADER} py-2 pb-6 sm:py-3 sm:pb-8`}>
          <Breadcrumbs
            className="mb-2 text-left sm:mb-3"
            items={[
              { label: t('common.breadcrumb.home'), to: '/' },
              { label: t('common.breadcrumb.profile'), to: '/profile' },
              { label: t('common.breadcrumb.skillDetail'), to: skillDetailPath },
              { label: t('profile.reviewDetail') },
            ]}
          />

          <article className="w-full overflow-hidden rounded-lg border border-slate-200/80 bg-white shadow-sm sm:rounded-xl">
            <header className={`border-b border-slate-100 ${CARD_INNER_PAD}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <Typography variant="h5" className="break-words font-bold text-slate-900">
                    {reviewTitleName} · {t('profile.reviewDetail')}
                  </Typography>
                  <Typography variant="body2" className="mt-1 text-slate-500">
                    {formatSkillVersionLabel(version, {
                      gitVersionDisplayAsCommit: detail?.git_version_display_as_commit,
                      resolvedCommitSha: detail?.resolved_commit_sha,
                      declaredSkillVersion: detail?.declared_skill_version,
                      storageMode: detail?.storage_mode,
                    })}
                  </Typography>
                  {isSkillLikePluginType(detail?.plugin_type) && hasTerminalReview ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <StatusPill status={reviewResult.overall === 'approved' ? 'passed' : 'failed'} label={reviewResult.overall === 'approved' ? t('profile.reviewApproved') : t('profile.reviewRejected')} />
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
                        {t('profile.reviewDimensionCount', { count: evidenceState.sections.length })}
                      </span>
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
                        {t('profile.reviewFailedDimensionCount', {
                          count: countSectionsByStatus(evidenceState.sections, 'failed'),
                        })}
                      </span>
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
                        {t('profile.reviewAttentionDimensionCount', {
                          count: countSectionsByStatus(evidenceState.sections, 'attention'),
                        })}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            </header>

            <div className={CARD_INNER_PAD}>
              {errMsg ? (
                <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errMsg}</div>
              ) : null}
              {isLoading ? (
                <Typography variant="body2" className="text-slate-500">
                  {t('plugins.loading')}
                </Typography>
              ) : null}
              {detail && !isSkillLikePluginType(detail.plugin_type) ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {t('profile.reviewOnlySkill')}
                </div>
              ) : null}

              {isSkillLikePluginType(detail?.plugin_type) ? (
                <>
                  {semanticReview ? <SemanticReviewCard review={semanticReview} t={t} /> : null}
                  {reviewDetailUnavailable ? (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      {t('profile.reviewDetailDisabled')}
                    </div>
                  ) : !hasTerminalReview ? (
                    reviewResult.status === 'system_failed' || detail?.publish_result === 'publish_failed' ? (
                      <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                        {reviewResult.failedReason || t('profile.reviewSystemFailedGeneric')}
                      </div>
                    ) : (
                      <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                        {t('profile.publishResultReviewing')}
                      </div>
                    )
                  ) : evidenceState.sections.length === 0 ? (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      {t('profile.reviewNoStructuredEvidence')}
                    </div>
                  ) : (
                    <div className="grid min-h-[620px] grid-cols-1 gap-4 xl:grid-cols-[minmax(0,220px)_minmax(0,340px)_minmax(0,1fr)]">
                      <aside className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)] rounded-xl border border-slate-200 bg-white p-4">
                        <div className="mb-3 text-xs font-bold uppercase tracking-[0.08em] text-slate-500">{t('profile.reviewWorkbenchSection')}</div>
                        <div ref={sectionScrollRef} className="min-h-0 space-y-3 overflow-y-auto overflow-x-hidden pr-1">
                      {evidenceState.sections.map(section => (
                        <button
                          key={section.key}
                          type="button"
                          className={`w-full min-w-0 max-w-full overflow-hidden rounded-xl border p-3 text-left transition ${section.key === activeSection?.key ? 'border-sky-500 bg-sky-50 shadow-sm' : 'border-slate-200 bg-slate-50 hover:border-sky-300'}`}
                          onClick={() => {
                            setActiveSectionKey(section.key)
                            setActiveCheckId(section.checks[0]?.id || '')
                            scrollToTop(checkScrollRef)
                            scrollToTop(detailScrollRef)
                          }}
                        >
                          <div className="mb-2 flex min-w-0 items-start justify-between gap-2">
                            <div className="min-w-0 break-words font-semibold text-slate-900">{section.title}</div>
                            <div className="shrink-0">
                              <StatusPill status={section.status} label={t(STATUS_META[section.status].labelKey)} />
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                            <span>{t('profile.reviewCheckCount', { count: section.checkCount })}</span>
                            <span>{t('profile.reviewPassedCheckCount', { count: section.passedCheckCount })}</span>
                            <span>{t('profile.reviewFailedCheckCount', { count: section.failedCheckCount })}</span>
                            <span>{t('profile.reviewAttentionCheckCount', { count: section.attentionCheckCount })}</span>
                          </div>
                        </button>
                      ))}
                        </div>
                      </aside>

                      <section className="grid min-h-0 min-w-0 grid-rows-[auto_auto_minmax(0,1fr)] rounded-xl border border-slate-200 bg-white p-4">
                        <div className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">{t('profile.reviewWorkbenchCheck')}</div>
                        {activeSection ? (
                          <div className="my-3 flex min-w-0 flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="break-words font-semibold text-slate-900">{activeSection.title}</div>
                              <div className="mt-1 break-words text-sm text-slate-500">{formatSectionSummary(activeSection, t)}</div>
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2">
                              <MetaTag>{t('profile.reviewCheckCountShort', { count: activeSection.checkCount })}</MetaTag>
                              <MetaTag tone="success">{t('profile.reviewPassedCheckCount', { count: activeSection.passedCheckCount })}</MetaTag>
                              <MetaTag tone="danger">{t('profile.reviewFailedCheckCount', { count: activeSection.failedCheckCount })}</MetaTag>
                              <MetaTag tone="warning">{t('profile.reviewAttentionCheckCount', { count: activeSection.attentionCheckCount })}</MetaTag>
                            </div>
                          </div>
                        ) : null}
                        <div ref={checkScrollRef} className="min-h-0 space-y-3 overflow-y-auto overflow-x-hidden pr-1">
                      {activeSection?.checks.map(check => (
                        <button
                          key={check.id}
                          type="button"
                          className={`w-full min-w-0 max-w-full overflow-hidden rounded-xl border p-3 text-left transition ${check.id === activeCheck?.id ? 'border-sky-500 bg-sky-50 shadow-sm' : 'border-slate-200 bg-slate-50 hover:border-sky-300'}`}
                          onClick={() => {
                            setActiveCheckId(check.id)
                            scrollToTop(detailScrollRef)
                          }}
                        >
                          <div className="flex items-center gap-3">
                            <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-bold ${STATUS_META[check.status].pill}`}>
                              {STATUS_META[check.status].symbol}
                            </div>
                            <div className="min-w-0 flex-1 overflow-hidden">
                              <div className="break-words font-semibold text-slate-900">{check.name}</div>
                              <div className="mt-1 break-words text-sm text-slate-500">{check.resultSummary}</div>
                            </div>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {buildCheckMeta(check, t).map(item => (
                              <MetaTag key={`${check.id}-${item}`}>{item}</MetaTag>
                            ))}
                          </div>
                        </button>
                      ))}
                        </div>
                      </section>

                      <section className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)] rounded-xl border border-slate-200 bg-white p-4">
                        <div className="mb-3 text-xs font-bold uppercase tracking-[0.08em] text-slate-500">{t('profile.reviewWorkbenchDetail')}</div>
                        <div ref={detailScrollRef} className="min-h-0 overflow-y-auto overflow-x-hidden pr-1">
                      {activeCheck ? (
                        <div className="space-y-4">
                          <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="break-words font-semibold text-slate-900">{activeCheck.name}</div>
                              <div className="mt-1 break-words text-sm text-slate-500">{activeSection?.title}</div>
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2">
                              <StatusPill status={activeCheck.status} label={t(STATUS_META[activeCheck.status].labelKey)} />
                              {activeCheck.sourceLabels.map(sourceLabel => (
                                <span key={sourceLabel} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">
                                  {sourceLabel}
                                </span>
                              ))}
                            </div>
                          </div>

                          <div className="grid gap-3 md:grid-cols-2">
                            <MetaText label={t('profile.reviewCheckTarget')} value={activeCheck.target || '-'} />
                            <MetaText label={t('profile.reviewCheckResult')} value={activeCheck.resultSummary || '-'} />
                            <MetaText label={t('profile.reviewCheckScope')} value={activeCheck.scope || '-'} />
                            <MetaText label={t('profile.reviewCheckAdvice')} value={activeCheck.recommendation || activeSection?.recommendation || '-'} />
                          </div>

                          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                            <div className="mb-2 text-xs font-bold uppercase tracking-[0.06em] text-slate-500">{t('profile.reviewCheckSummary')}</div>
                            <div className="text-sm leading-6 text-slate-700">{activeCheck.summary || '-'}</div>
                          </div>

                          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                            <div className="mb-3 text-xs font-bold uppercase tracking-[0.06em] text-slate-500">{t('profile.reviewEvidence')}</div>
                            {activeCheck.findings.length > 0 ? (
                              <div className="space-y-3">
                                {activeCheck.findings.map(finding => (
                                  <EvidenceCard key={finding.id} finding={finding} t={t} />
                                ))}
                              </div>
                            ) : (
                              <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                                {t('profile.reviewNoEvidence')}
                              </div>
                            )}
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                          {t('profile.reviewChooseCheck')}
                        </div>
                      )}
                        </div>
                      </section>
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </article>
        </div>
      </div>
    </div>
  )
}

function StatusPill({ status, label }: { status: 'failed' | 'attention' | 'passed'; label: string }) {
  return <span className={`rounded-full border px-3 py-1 text-xs font-medium ${STATUS_META[status].pill}`}>{label}</span>
}

function SemanticReviewCard({ review, t }: { review: SemanticReviewSummary; t: Translate }) {
  const badge = semanticReviewBadge(review, t)
  const tone = semanticReviewTone(review)
  return (
    <section className={`mb-4 rounded-xl border p-4 ${tone.card}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-bold uppercase tracking-[0.08em] text-slate-500">{t('profile.semanticReviewTitle')}</div>
          <div className="mt-1 break-words text-sm text-slate-500">{t('profile.semanticReviewSubtitle')}</div>
        </div>
        <span className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${tone.badge}`}>{badge}</span>
      </div>
      <div className="mt-4 rounded-xl border border-white/70 bg-white/75 p-4">
        <div className="mb-2 text-xs font-bold uppercase tracking-[0.06em] text-slate-500">{t('profile.semanticReviewConclusion')}</div>
        <div className="break-words text-sm leading-6 text-slate-800">{review.displayMessage}</div>
      </div>
      {review.modelName || review.engine ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {review.modelName ? <MetaTag>{t('profile.semanticReviewModel', { model: review.modelName })}</MetaTag> : null}
          {review.engine ? <MetaTag>{t('profile.semanticReviewEngine', { engine: review.engine })}</MetaTag> : null}
        </div>
      ) : null}
    </section>
  )
}

function MetaTag({ children, tone = 'default' }: { children: ReactNode; tone?: 'default' | 'success' | 'warning' | 'danger' }) {
  const classes = {
    default: 'border-slate-200 bg-white text-slate-600',
    success: 'border-emerald-100 bg-emerald-50 text-emerald-700',
    warning: 'border-amber-100 bg-amber-50 text-amber-700',
    danger: 'border-red-100 bg-red-50 text-red-700',
  } as const
  return <span className={`rounded-full border px-3 py-1 text-xs font-medium ${classes[tone]}`}>{children}</span>
}

function MetaText({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="mb-1 text-xs font-bold uppercase tracking-[0.06em] text-slate-500">{label}</div>
      <div className="break-words text-sm leading-6 text-slate-700">{value}</div>
    </div>
  )
}

function EvidenceCard({ finding, t }: { finding: ReviewFinding; t: Translate }) {
  const severityMeta = SEVERITY_META[finding.severity] || SEVERITY_META.info
  const categoryLabelKey = finding.category ? CATEGORY_LABEL_KEY[finding.category] : ''
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 break-words font-semibold text-slate-900">{finding.title}</div>
        <span className={`rounded-full border px-3 py-1 text-xs font-medium ${severityMeta.pill}`}>{t(severityMeta.labelKey)}</span>
      </div>
      <div className="mb-2 flex flex-wrap gap-2 text-xs text-slate-600">
        <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">{finding.sourceLabel}</span>
        {finding.category ? (
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
            {categoryLabelKey ? t(categoryLabelKey) : finding.category}
          </span>
        ) : null}
        {finding.locationLabel ? <code className="rounded-full bg-slate-100 px-3 py-1 not-italic">{finding.locationLabel}</code> : null}
      </div>
      <div className="break-words text-sm leading-6 text-slate-700">{finding.description || t('profile.reviewNoDescription')}</div>
      {finding.snippet ? (
        <div className="mt-3">
          {finding.locationLabel ? <div className="mb-2 text-xs font-semibold text-slate-500"><code>{finding.locationLabel}</code></div> : null}
          <pre className="overflow-auto rounded-xl bg-slate-900 p-3 text-xs leading-6 text-slate-100"><code>{finding.snippet}</code></pre>
        </div>
      ) : (
        <div className="mt-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-600">
          {finding.semanticEvidence || ((finding.sourceType === 'agent' || finding.sourceType === 'ai') ? t('profile.reviewOnlySemanticHint') : t('profile.reviewNoSourceSnippet'))}
        </div>
      )}
    </div>
  )
}

function scrollToTop(scrollRef: React.RefObject<HTMLDivElement | null>) {
  scrollRef.current?.scrollTo?.({ top: 0 })
}

function countSectionsByStatus(sections: ReviewSection[], status: 'failed' | 'attention' | 'passed') {
  return sections.filter(section => section.status === status).length
}

function isTerminalReviewStatus(value?: string | null) {
  const status = String(value || '').trim().toLowerCase()
  return status === 'passed' || status === 'failed' || status === 'system_failed'
}

function buildEvidenceState(reviewResult: any, t: Translate) {
  const sections = normalizeSections(reviewResult.sections || [], t)
  const defaultSectionKey = selectDefaultSectionKey(sections)
  const activeSection = sections.find(section => section.key === defaultSectionKey) || sections[0] || null
  const rankedChecks = activeSection ? [...activeSection.checks].sort(compareChecks) : []
  return {
    sections,
    defaultSectionKey,
    defaultCheckId: rankedChecks[0]?.id || activeSection?.checks[0]?.id || '',
  }
}

function normalizeSemanticReview(value: unknown, t: Translate): SemanticReviewSummary | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, any>
  const hasReviewPayload =
    'status' in raw ||
    'finding_count' in raw ||
    'findings' in raw ||
    'display_message' in raw ||
    'conclusion' in raw ||
    'engine' in raw ||
    'model_name' in raw
  if (!hasReviewPayload) return null
  const status = normalizeSemanticReviewStatus(raw.status)
  const findingCount = asNumber(raw.finding_count, Array.isArray(raw.findings) ? raw.findings.length : 0)
  const conclusion = String(raw.conclusion || '').trim()
  const displayMessage = String(raw.display_message || conclusion || semanticReviewDefaultMessage(status, findingCount, t)).trim()
  return {
    status,
    engine: String(raw.engine || '').trim(),
    modelName: String(raw.model_name || '').trim(),
    displayMessage,
    conclusion,
    findingCount,
  }
}

function normalizeSemanticReviewStatus(value: unknown): SemanticReviewSummary['status'] {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'fallback' || text === 'disabled' || text === 'failed') return text
  return 'completed'
}

function semanticReviewDefaultMessage(status: SemanticReviewSummary['status'], findingCount: number, t: Translate) {
  if (status === 'fallback') return t('profile.semanticReviewFallbackConclusion')
  if (status === 'disabled') return t('profile.semanticReviewDisabledConclusion')
  if (status === 'failed') return t('profile.reviewSystemFailedGeneric')
  return findingCount > 0 ? t('profile.semanticReviewHasFindingsConclusion') : t('profile.semanticReviewNoRiskConclusion')
}

function semanticReviewBadge(review: SemanticReviewSummary, t: Translate) {
  if (review.status === 'fallback') return t('profile.semanticReviewFallbackBadge')
  if (review.status === 'disabled') return t('profile.semanticReviewDisabledBadge')
  if (review.status === 'failed') return t('profile.reviewStatusFailed')
  return review.findingCount > 0 ? t('profile.semanticReviewFindingCount', { count: review.findingCount }) : t('profile.semanticReviewNoRisk')
}

function semanticReviewTone(review: SemanticReviewSummary) {
  if (review.status === 'failed') {
    return { card: 'border-rose-200 bg-rose-50', badge: 'border-rose-200 bg-white text-rose-700' }
  }
  if (review.status === 'fallback' || review.status === 'disabled') {
    return { card: 'border-slate-200 bg-slate-50', badge: 'border-slate-200 bg-white text-slate-600' }
  }
  if (review.findingCount > 0) {
    return { card: 'border-amber-200 bg-amber-50', badge: 'border-amber-200 bg-white text-amber-700' }
  }
  return { card: 'border-emerald-200 bg-emerald-50', badge: 'border-emerald-200 bg-white text-emerald-700' }
}

function normalizeSections(sections: any[], t: Translate): ReviewSection[] {
  return sections.map((section, sectionIndex) => {
    const checks = normalizeChecks(section, section.checks || [], t)
    const metrics = section.metrics || {}
    const failedCheckCount = asNumber(metrics.failed_check_count, checks.filter(check => check.status === 'failed').length)
    const attentionCheckCount = asNumber(metrics.attention_check_count, checks.filter(check => check.status === 'attention').length)
    const passedCheckCount = asNumber(metrics.passed_check_count, checks.filter(check => check.status === 'passed').length)
    return {
      key: section.key || `section-${sectionIndex + 1}`,
      title: section.title || t('profile.reviewUnnamedSection'),
      status: normalizeStatus(section.display_status || section.gate_status || section.status),
      checks,
      checkCount: asNumber(metrics.check_count, checks.length),
      findingCount: asNumber(metrics.finding_count, checks.reduce((sum, check) => sum + check.findings.length, 0)),
      highRiskCount: asNumber(metrics.high_risk_finding_count, checks.reduce((sum, check) => sum + check.highRiskCount, 0)),
      failedCheckCount,
      attentionCheckCount,
      passedCheckCount,
      recommendation: section.recommendation || '',
    }
  })
}

function normalizeChecks(section: any, checks: any[], t: Translate): ReviewCheck[] {
  return checks.map((check, checkIndex) => {
    const findings = sortFindings(normalizeFindings(section, check, check.findings || [], t))
    const highRiskCount = findings.filter(finding => finding.severity === 'high').length
    const sourceKinds = [...new Set(findings.map(finding => finding.sourceType).filter(Boolean))]
    const sourceLabels = sourceKinds
      .sort((left, right) => (SOURCE_PRIORITY[left] ?? 99) - (SOURCE_PRIORITY[right] ?? 99))
      .map(kind => sourceLabel(kind, t))
      .filter(Boolean)
    return {
      id: `${section.key || 'section'}-check-${checkIndex + 1}-${check.check_id || 'unknown'}`,
      checkId: check.check_id || `check-${checkIndex + 1}`,
      name: check.name || t('profile.reviewUnnamedCheck'),
      summary: check.result_summary || check.target || '',
      target: check.target || '',
      scope: check.scope || '',
      status: normalizeStatus(check.status),
      findingCount: findings.length,
      highRiskCount,
      sourceLabels,
      findings,
      recommendation: check.recommendation || section.recommendation || '',
      resultSummary: check.result_summary || defaultCheckSummary(check.status, findings.length, t),
      severity: findings[0]?.severity || 'info',
    }
  })
}

function normalizeFindings(section: any, check: any, findings: any[], t: Translate): ReviewFinding[] {
  return [...findings].map((finding, findingIndex) => {
    const evidence = Array.isArray(finding.evidence) ? finding.evidence : []
    const sourceEvidence = evidence.find((item: any) => item.evidence_type === 'source')
    const semanticEvidence = evidence.find((item: any) => item.evidence_type === 'semantic')
    const systemEvidence = evidence.find((item: any) => item.evidence_type === 'system')
    return {
      id: finding.finding_id || `${section.key || 'section'}-${check.check_id || 'check'}-${findingIndex + 1}`,
      sourceType: finding.source_type || 'system',
      sourceLabel: sourceLabel(finding.source_type || 'system', t),
      title: finding.title || check.name || t('profile.reviewUnnamedFinding'),
      description: finding.description || '',
      recommendation: finding.recommendation || check.recommendation || section.recommendation || '',
      severity: normalizeSeverity(finding.severity),
      category: finding.category || '',
      capability: finding.capability || check.check_id || '',
      confidence: String(finding.confidence || '').trim().toLowerCase(),
      locationLabel: formatLocationLabel(sourceEvidence?.location),
      snippet: sourceEvidence?.text || '',
      semanticEvidence: semanticEvidence?.text || systemEvidence?.text || '',
    }
  }).sort((left, right) => severityRank(right.severity) - severityRank(left.severity))
}

function selectDefaultSectionKey(sections: ReviewSection[]) {
  const rankedSections = [...sections].sort((left, right) => {
    const statusDelta = sectionRank(right) - sectionRank(left)
    if (statusDelta !== 0) return statusDelta
    const severityDelta = right.highRiskCount - left.highRiskCount
    if (severityDelta !== 0) return severityDelta
    return right.findingCount - left.findingCount
  })
  return rankedSections[0]?.key || ''
}

function compareChecks(left: ReviewCheck, right: ReviewCheck) {
  const statusDelta = (CHECK_STATUS_PRIORITY[right.status] || 0) - (CHECK_STATUS_PRIORITY[left.status] || 0)
  if (statusDelta !== 0) return statusDelta
  const severityDelta = (SEVERITY_PRIORITY[right.severity] || 0) - (SEVERITY_PRIORITY[left.severity] || 0)
  if (severityDelta !== 0) return severityDelta
  return right.findingCount - left.findingCount
}

function sortFindings(findings: ReviewFinding[]) {
  return [...findings].sort((left, right) => {
    const severityDelta = (SEVERITY_PRIORITY[right.severity] || 0) - (SEVERITY_PRIORITY[left.severity] || 0)
    if (severityDelta !== 0) return severityDelta
    if (left.sourceType !== right.sourceType) {
      return (SOURCE_PRIORITY[left.sourceType] ?? 99) - (SOURCE_PRIORITY[right.sourceType] ?? 99)
    }
    return left.id.localeCompare(right.id)
  })
}

function sectionRank(section: ReviewSection) {
  if (section.status === 'failed') return 2
  if (section.status === 'attention') return 1
  return 0
}

function buildCheckMeta(check: ReviewCheck, t: Translate) {
  const items = [t('profile.reviewFindingCount', { count: check.findingCount })]
  if (check.highRiskCount > 0) items.push(t('profile.reviewHighRiskFindingCount', { count: check.highRiskCount }))
  if (check.sourceLabels.length > 0) items.push(check.sourceLabels.join(' · '))
  else if (check.findingCount > 0) items.push(t('profile.reviewMixedSources'))
  else items.push(t('profile.reviewCompletedCheck'))
  return items
}

function formatSectionSummary(section: ReviewSection, t: Translate) {
  const stats = [
    t('profile.reviewCheckCount', { count: section.checkCount }),
    t('profile.reviewPassedCheckCount', { count: section.passedCheckCount }),
  ]
  if (section.failedCheckCount > 0) stats.push(t('profile.reviewFailedCheckCount', { count: section.failedCheckCount }))
  if (section.attentionCheckCount > 0) stats.push(t('profile.reviewAttentionCheckCount', { count: section.attentionCheckCount }))
  return stats.join(' · ')
}

function formatLocationLabel(location: any) {
  if (!location?.file) return ''
  return location.line ? `${location.file}:${location.line}` : location.file
}

function sourceLabel(type: string, t: Translate) {
  if (type === 'rule') return t('profile.reviewSourceRule')
  if (type === 'scanner') return t('profile.reviewSourceScanner')
  if (type === 'agent' || type === 'ai') return t('profile.reviewSourceAi')
  return t('profile.reviewSourceSystem')
}

function normalizeStatus(value: unknown): 'failed' | 'attention' | 'passed' {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'failed' || text === 'attention' || text === 'passed') return text
  return 'passed'
}

function normalizeSeverity(value: unknown): 'high' | 'medium' | 'low' | 'info' {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'high' || text === 'medium' || text === 'low' || text === 'info') return text
  return 'info'
}

function severityRank(value: 'high' | 'medium' | 'low' | 'info') {
  return { high: 3, medium: 2, low: 1, info: 0 }[value] ?? 0
}

function defaultCheckSummary(status: unknown, findingsCount: number, t: Translate) {
  const normalized = normalizeStatus(status)
  if (normalized === 'passed') return t('profile.reviewPassedSummaryDefault')
  if (normalized === 'attention') return t('profile.reviewAttentionSummaryDefault', { count: findingsCount })
  return t('profile.reviewFailedSummaryDefault', { count: findingsCount })
}

function asNumber(value: unknown, fallback: number) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}
