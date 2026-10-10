// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient, type QueryClient } from 'react-query'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { Loader2 } from 'lucide-react'
import {
  createGitSourceAndSync,
  deleteGitSource,
  GitSourceDeleteError,
  GitSourceDuplicateError,
  listMyGitSources,
  syncGitSource,
} from '@/api/plugin'
import type { GitSourceItemDto } from '@/api/plugin'
import { GitHostIcon } from '@/components/Common/GitHostIcon'
import { formatGitHttpsCloneUrlDisplay } from '@/utils/gitSourceDisplay'
import { isPublicGitCloneUrl } from '@/utils/gitUrlSafety'
import { isValidGitSkillsSubpath, normalizeGitSkillsSubpath } from '@/utils/gitSkillsSubpathSafety'

const inputBase =
  'block h-10 w-full rounded-lg border border-[#E2E8F0] bg-white px-3 text-[13.5px] text-[#0F172A] placeholder:text-[#94A3B8] transition-colors hover:border-[#CBD5E1] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

/** 与后端 _STALE_GIT_SYNC_MS 对齐：轮询超过该时长仍 syncing 则停止转圈并提示 */
const GIT_SYNC_POLL_MAX_MS = 30 * 60 * 1000
/** 轮询期间长期非 syncing/终态（如 null）则停止等待 */
const GIT_SYNC_UNKNOWN_STATUS_MAX_MS = 5 * 60 * 1000
/** 轮询中列表始终找不到 activeSyncSourceId（如后台已标 failed）则结束等待 */
const GIT_SYNC_MISSING_SOURCE_MS = 6 * 1000
const GIT_SOURCES_POLL_INTERVAL_MS = 2000

type GitSourcesListData = { items: GitSourceItemDto[] }

function markGitSourceSyncingInCache(
  queryClient: QueryClient,
  queryKey: readonly ['my-git-sources', string | undefined],
  sourceId: string,
): void {
  queryClient.setQueryData<GitSourcesListData | undefined>(queryKey, prev => {
    if (!prev?.items?.length) {
      return prev
    }
    const idx = prev.items.findIndex(g => g.id === sourceId)
    if (idx < 0) {
      return prev
    }
    const items = prev.items.slice()
    items[idx] = {
      ...items[idx],
      last_index_status: 'syncing',
      last_index_error: null,
    }
    return { ...prev, items }
  })
}

export type GitSourcesPanelProps = {
  userId: string | undefined
}

function gitSourceIndexStatusLabel(t: (k: string) => string, status: string | null | undefined): string {
  const s = (status ?? '').trim().toLowerCase()
  if (s === 'syncing') return t('publish.gitSyncIndexStatusSyncing')
  if (s === 'success') return t('publish.gitSyncIndexStatusSuccess')
  if (s === 'partial_failure') return t('publish.gitSyncIndexStatusPartialFailure')
  if (s === 'failed') return t('publish.gitSyncIndexStatusFailed')
  return t('publish.gitSyncIndexStatusUnknown')
}

function isGitSourceSyncing(status: string | null | undefined): boolean {
  return (status ?? '').trim().toLowerCase() === 'syncing'
}

function isGitSourceTerminalStatus(status: string | null | undefined): boolean {
  const s = (status ?? '').trim().toLowerCase()
  return s === 'success' || s === 'partial_failure' || s === 'failed'
}

function formatSyncedAt(
  t: (k: string, o?: Record<string, string>) => string,
  ms: number | null | undefined,
  lang: string,
  indexStatus: string | null | undefined,
): string {
  const syncing = isGitSourceSyncing(indexStatus)
  if (ms == null || ms <= 0) {
    return syncing ? t('publish.gitSyncAwaitingFirstComplete') : t('publish.gitSyncNeverSynced')
  }
  const d = lang.toLowerCase().startsWith('zh') ? dayjs(ms).locale('zh-cn') : dayjs(ms)
  return t('publish.gitSyncLastSynced', { time: d.format('YYYY-MM-DD HH:mm') })
}

function resolveGitSourceDeleteError(
  e: unknown,
  t: (k: string, opts?: Record<string, string>) => string,
): string {
  if (e instanceof GitSourceDeleteError) {
    if (e.reason === 'git_source_sync_in_progress') return t('publish.gitSourceDeleteSyncing')
    if (e.reason === 'git_source_cascade_delete_partial') {
      return t('publish.gitSourceDeletePartial', {
        deleted: String(e.deletedSkillCount ?? 0),
        failed: String(e.failedSkillCount ?? 0),
      })
    }
  }
  return t('publish.gitSourceDeleteFailed')
}

/** 个人中心：注册 Git 仓库并同步 / 再次同步（与发布页共用文案键 publish.git*）。 */
export function GitSourcesPanel({ userId }: GitSourcesPanelProps) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [gitRepoUrl, setGitRepoUrl] = useState('')
  const [gitRef, setGitRef] = useState('main')
  const [gitSkillsSubpath, setGitSkillsSubpath] = useState('')
  const [gitBusy, setGitBusy] = useState(false)
  const [gitBanner, setGitBanner] = useState('')
  const [resyncingId, setResyncingId] = useState<string | null>(null)
  const [pollGitSources, setPollGitSources] = useState(false)
  const [pollSyncAccepted, setPollSyncAccepted] = useState(false)
  const [activeSyncSourceId, setActiveSyncSourceId] = useState<string | null>(null)
  const pollStartedAtRef = useRef<number | null>(null)
  const pollFinishStartedRef = useRef(false)
  /** 再次同步时列表缓存常为 success/failed，须至少见过一次 syncing（或超时）再因终态停轮询 */
  const pollSawActiveSyncingRef = useRef(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteErrorBySourceId, setDeleteErrorBySourceId] = useState<Record<string, string>>({})

  const gitSourcesQueryKey = useMemo(() => ['my-git-sources', userId] as const, [userId])

  /** 全局 staleTime=∞ 时须显式 setQueryData，否则轮询结果可能不触发列表重绘。 */
  const fetchAndCacheGitSources = useCallback(async () => {
    const data = await listMyGitSources()
    if (userId) {
      queryClient.setQueryData(gitSourcesQueryKey, data)
    }
    return data
  }, [userId, queryClient, gitSourcesQueryKey])

  const pullGitSources = useCallback(async () => {
    if (!userId) {
      return undefined
    }
    return fetchAndCacheGitSources()
  }, [userId, fetchAndCacheGitSources])

  const { data: gitSourcesRes } = useQuery(gitSourcesQueryKey, fetchAndCacheGitSources, {
    enabled: Boolean(userId),
    staleTime: pollGitSources ? 0 : Number.POSITIVE_INFINITY,
    refetchInterval: pollGitSources ? GIT_SOURCES_POLL_INTERVAL_MS : false,
    structuralSharing: false,
  })

  const gitSourceItems = gitSourcesRes?.items ?? []

  const optimisticMarkSourceSyncing = useCallback(
    (sourceId: string) => {
      if (!userId) {
        return
      }
      markGitSourceSyncingInCache(queryClient, gitSourcesQueryKey, sourceId)
      pollSawActiveSyncingRef.current = true
    },
    [userId, queryClient, gitSourcesQueryKey],
  )

  const anySourceSyncing = useMemo(
    () => gitSourceItems.some(g => isGitSourceSyncing(g.last_index_status)),
    [gitSourceItems],
  )

  const resetGitSourcePoll = useCallback(() => {
    setPollGitSources(false)
    setPollSyncAccepted(false)
    setGitBusy(false)
    setResyncingId(null)
    setActiveSyncSourceId(null)
    pollStartedAtRef.current = null
    pollSawActiveSyncingRef.current = false
  }, [])

  const finishGitSourcePoll = useCallback(
    (watched: GitSourceItemDto) => {
      if (pollFinishStartedRef.current) {
        return
      }
      pollFinishStartedRef.current = true
      resetGitSourcePoll()
      const st = (watched.last_index_status ?? '').trim().toLowerCase()
      if (st === 'success') {
        setGitBanner(t('publish.gitSyncFinishedSuccess'))
      } else if (st === 'partial_failure') {
        setGitBanner(watched.last_index_error || t('publish.gitSyncFinishedPartial'))
      } else if (st === 'failed') {
        setGitBanner(watched.last_index_error || t('publish.gitSyncFinishedFailed'))
      }
      pollFinishStartedRef.current = false
    },
    [resetGitSourcePoll, t],
  )

  /** 列表数据变更时检测终态，避免仅依赖 ref 导致后台已 success 但 UI 仍 syncing。 */
  useEffect(() => {
    if (!pollGitSources || !pollSyncAccepted) {
      return
    }

    const started = pollStartedAtRef.current
    if (started != null && Date.now() - started > GIT_SYNC_POLL_MAX_MS) {
      const watched = activeSyncSourceId
        ? gitSourceItems.find(g => g.id === activeSyncSourceId)
        : undefined
      const stillBusy = activeSyncSourceId
        ? watched
          ? isGitSourceSyncing(watched.last_index_status)
          : true
        : anySourceSyncing
      if (
        !stillBusy &&
        watched &&
        isGitSourceTerminalStatus(watched.last_index_status)
      ) {
        finishGitSourcePoll(watched)
        return
      }
      if (stillBusy) {
        setGitBanner(t('publish.gitSyncPollTimeout'))
      }
      resetGitSourcePoll()
      void pullGitSources()
      return
    }

    if (activeSyncSourceId && !gitSourceItems.some(g => g.id === activeSyncSourceId)) {
      if (started != null && Date.now() - started > GIT_SYNC_MISSING_SOURCE_MS) {
        resetGitSourcePoll()
        setGitBanner(t('publish.gitSyncFinishedFailed'))
        void pullGitSources()
      }
      return
    }

    if (!activeSyncSourceId) {
      return
    }

    const watched = gitSourceItems.find(g => g.id === activeSyncSourceId)
    if (!watched) {
      return
    }

    if (isGitSourceSyncing(watched.last_index_status)) {
      pollSawActiveSyncingRef.current = true
      return
    }

    if (!isGitSourceTerminalStatus(watched.last_index_status)) {
      if (started != null && Date.now() - started > GIT_SYNC_UNKNOWN_STATUS_MAX_MS) {
        resetGitSourcePoll()
        setGitBanner(t('publish.gitSyncPollTimeout'))
        void pullGitSources()
      }
      return
    }

    if (activeSyncSourceId && !pollSawActiveSyncingRef.current) {
      if (started != null && Date.now() - started < 8000) {
        return
      }
    }

    finishGitSourcePoll(watched)
  }, [
    pollGitSources,
    pollSyncAccepted,
    gitSourceItems,
    activeSyncSourceId,
    anySourceSyncing,
    pullGitSources,
    resetGitSourcePoll,
    finishGitSourcePoll,
    t,
  ])

  const gitSources = useMemo(() => {
    const items = gitSourcesRes?.items ?? []
    return [...items].sort((a, b) =>
      formatGitHttpsCloneUrlDisplay(a.repo_url || '').localeCompare(
        formatGitHttpsCloneUrlDisplay(b.repo_url || ''),
        undefined,
        { sensitivity: 'base' },
      ),
    )
  }, [gitSourcesRes])

  /** 标题区图标仅随「新建接入」表单中的 URL 变化，不沿用已注册源，避免切换输入后仍显示上一托管商图标。 */
  const headerRepoUrlForIcon = useMemo(
    () => formatGitHttpsCloneUrlDisplay(gitRepoUrl.trim()),
    [gitRepoUrl],
  )

  const afterSync = () => {
    void pullGitSources()
    void queryClient.invalidateQueries({ queryKey: ['my-published-skills'] })
    void queryClient.invalidateQueries({ queryKey: ['publish-my-plugins'] })
  }

  const handleGitCreateSync = async () => {
    setGitBanner('')
    const url = gitRepoUrl.trim()
    if (!url) {
      setGitBanner(t('publish.gitSyncNeedUrl'))
      return
    }
    if (!isPublicGitCloneUrl(url)) {
      setGitBanner(t('publish.gitRepoUrlBlocked'))
      return
    }
    const subpath = normalizeGitSkillsSubpath(gitSkillsSubpath)
    if (subpath && !isValidGitSkillsSubpath(subpath)) {
      setGitBanner(t('publish.gitSkillsSubpathInvalid'))
      return
    }
    setGitBusy(true)
    pollFinishStartedRef.current = false
    setPollSyncAccepted(false)
    pollSawActiveSyncingRef.current = false
    pollStartedAtRef.current = null
    setActiveSyncSourceId(null)
    setPollGitSources(false)
    try {
      const accepted = await createGitSourceAndSync({
        repo_url: url,
        ref: gitRef.trim() || 'main',
        skills_subpath: subpath || undefined,
      })
      pollStartedAtRef.current = Date.now()
      setActiveSyncSourceId(accepted.source_id)
      optimisticMarkSourceSyncing(accepted.source_id)
      setPollGitSources(true)
      setPollSyncAccepted(true)
      setGitBanner(t('publish.gitSyncInProgress'))
      afterSync()
    } catch (e) {
      resetGitSourcePoll()
      if (e instanceof GitSourceDuplicateError) {
        setGitBanner(t('publish.gitRepoAlreadyRegistered'))
      } else {
        setGitBanner(e instanceof Error ? e.message : t('publish.uploadFailed'))
      }
    }
  }

  const handleGitResync = async (sourceId: string) => {
    const sid = sourceId.trim()
    if (!sid) return
    setGitBanner('')
    setResyncingId(sid)
    pollFinishStartedRef.current = false
    setPollSyncAccepted(false)
    pollSawActiveSyncingRef.current = false
    pollStartedAtRef.current = null
    setActiveSyncSourceId(null)
    setPollGitSources(false)
    try {
      await syncGitSource(sid)
      pollStartedAtRef.current = Date.now()
      setActiveSyncSourceId(sid)
      optimisticMarkSourceSyncing(sid)
      setPollGitSources(true)
      setPollSyncAccepted(true)
      setGitBanner(t('publish.gitSyncInProgress'))
      afterSync()
    } catch (e) {
      resetGitSourcePoll()
      void pullGitSources()
      setGitBanner(e instanceof Error ? e.message : t('publish.uploadFailed'))
    }
  }

  const handleGitDelete = async (sourceId: string) => {
    const sid = sourceId.trim()
    if (!sid) return
    if (!window.confirm(t('publish.gitSourceDeleteConfirm'))) {
      return
    }
    setDeletingId(sid)
    setDeleteErrorBySourceId(prev => {
      const next = { ...prev }
      delete next[sid]
      return next
    })
    try {
      const result = await deleteGitSource(sid)
      const n = result?.deleted_skill_count ?? 0
      setGitBanner(
        n > 0
          ? t('publish.gitSourceDeletedWithSkills', { count: n })
          : t('publish.gitSourceDeleted'),
      )
      afterSync()
    } catch (e) {
      setDeleteErrorBySourceId(prev => ({
        ...prev,
        [sid]: resolveGitSourceDeleteError(e, t),
      }))
      // 部分 Skill 可能已删：刷新列表与「我的技能」
      afterSync()
    } finally {
      setDeletingId(null)
    }
  }

  if (!userId) {
    return null
  }

  return (
    <div className="rounded-xl border border-[#E5E7EB] bg-[#FAFBFF] p-4 md:p-6">
      <div className="mb-2 flex items-center gap-2 text-[15px] font-semibold text-[#111827]">
        <GitHostIcon
          repoUrl={headerRepoUrlForIcon}
          className="h-5 w-5 shrink-0"
          fallbackClassName="h-5 w-5 shrink-0 text-[#1E54F9]"
        />
        {t('publish.gitSyncTitle')}
      </div>
      <ul className="mb-4 list-disc space-y-1.5 pl-5 text-[13px] leading-6 text-[#64748B]">
        {(t('publish.gitSyncIntroBullets', { returnObjects: true }) as unknown[]).filter(
          (x): x is string => typeof x === 'string',
        ).map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {gitBanner ? (
        <div className="mb-4 whitespace-pre-line rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-[13px] leading-5 text-[#334155]">
          {gitBanner}
        </div>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-[13px] font-medium text-[#374151] sm:col-span-2">
          {t('publish.gitFieldUrl')}
          <input
            type="url"
            value={gitRepoUrl}
            onChange={e => setGitRepoUrl(e.target.value)}
            className={`${inputBase} mt-1.5`}
            placeholder="https://example.com/group/repo.git"
            disabled={gitBusy}
          />
        </label>
        <label className="block text-[13px] font-medium text-[#374151]">
          {t('publish.gitFieldRef')}
          <input
            type="text"
            value={gitRef}
            onChange={e => setGitRef(e.target.value)}
            className={`${inputBase} mt-1.5`}
            disabled={gitBusy}
          />
        </label>
        <label className="block text-[13px] font-medium text-[#374151]">
          {t('publish.gitFieldSubpath')}
          <input
            type="text"
            value={gitSkillsSubpath}
            onChange={e => setGitSkillsSubpath(e.target.value)}
            className={`${inputBase} mt-1.5`}
            placeholder="skills"
            disabled={gitBusy}
          />
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void handleGitCreateSync()}
          disabled={gitBusy}
          className="inline-flex h-10 items-center justify-center rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-5 text-[14px] font-medium text-white shadow-sm transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {gitBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
          {t('publish.gitSyncSubmit')}
        </button>
      </div>
      <div className="mt-6 border-t border-slate-200 pt-5">
        <div className="mb-3 text-[13px] font-medium text-[#374151]">{t('publish.gitSyncSourceLabel')}</div>
        {gitSources.length === 0 ? (
          <p className="text-[13px] leading-6 text-[#64748B]">{t('publish.gitSyncNoSources')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {gitSources.map((g: GitSourceItemDto) => (
              <GitSourceRow
                key={g.id}
                item={g}
                locale={i18n.language}
                busy={gitBusy || resyncingId === g.id || deletingId === g.id}
                showSyncSpinner={
                  isGitSourceSyncing(g.last_index_status) ||
                  resyncingId === g.id ||
                  (activeSyncSourceId === g.id &&
                    !isGitSourceTerminalStatus(g.last_index_status))
                }
                deleteError={deleteErrorBySourceId[g.id]}
                onResync={() => void handleGitResync(g.id)}
                onDelete={() => void handleGitDelete(g.id)}
                t={t}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function GitSourceRow({
  item,
  locale,
  busy,
  showSyncSpinner,
  deleteError,
  onResync,
  onDelete,
  t,
}: {
  item: GitSourceItemDto
  locale: string
  busy: boolean
  showSyncSpinner: boolean
  deleteError?: string
  onResync: () => void
  onDelete: () => void
  t: (k: string, o?: Record<string, string>) => string
}) {
  const sub = (item.skills_subpath ?? '').trim()
  const displayRepoUrl = formatGitHttpsCloneUrlDisplay(item.repo_url)
  const status = (item.last_index_status ?? '').trim().toLowerCase()
  const statusColor =
    status === 'syncing'
      ? 'text-blue-700'
      : status === 'success'
        ? 'text-emerald-700'
        : status === 'partial_failure'
          ? 'text-amber-700'
          : status === 'failed'
            ? 'text-red-700'
            : 'text-slate-600'

  return (
    <li className="rounded-lg border border-[#E2E8F0] bg-white px-3 py-3 sm:px-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-start gap-2">
            <GitHostIcon
              repoUrl={displayRepoUrl}
              className="mt-0.5 h-4 w-4 shrink-0"
              fallbackClassName="mt-0.5 h-4 w-4 shrink-0 text-[#1E54F9]"
            />
            <span className="break-all text-[13px] font-medium text-[#0F172A]">{displayRepoUrl}</span>
          </div>
          <div className="pl-6 text-[12px] leading-5 text-[#64748B]">
            {item.ref}
            {sub ? ` · ${sub}` : null}
          </div>
          <div className={`pl-6 text-[12px] leading-5 ${statusColor}`}>
            {formatSyncedAt(t, item.last_indexed_at_ms, locale, item.last_index_status)} ·{' '}
            {gitSourceIndexStatusLabel(t, item.last_index_status)}
          </div>
          {item.last_index_error && (status === 'failed' || status === 'partial_failure') ? (
            <div className="whitespace-pre-line pl-6 text-[12px] leading-5 text-red-600/90">{item.last_index_error}</div>
          ) : null}
          {deleteError ? (
            <div
              className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-[12px] leading-5 text-red-800"
              role="alert"
            >
              {deleteError}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2 self-start sm:self-center">
          <button
            type="button"
            onClick={onResync}
            disabled={busy || status === 'syncing'}
            className="inline-flex h-9 items-center justify-center rounded-full border border-[#CBD5E1] bg-white px-4 text-[13px] font-medium text-[#111827] hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {showSyncSpinner ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
            ) : null}
            {t('publish.gitSyncResync')}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={busy || status === 'syncing'}
            className="inline-flex h-9 items-center justify-center rounded-full border border-red-200 bg-white px-4 text-[13px] font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('publish.gitSourceDelete')}
          </button>
        </div>
      </div>
    </li>
  )
}
