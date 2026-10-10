// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Select, Typography } from '@mui/material'
import { CalendarClock, Check, Copy, Plus, Search, Shield, Users, X } from 'lucide-react'
import { AppHeader } from '@/components/Common/AppHeader'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { Pagination } from '@/components/Common/common-table'
import { createGroup, discoverGroups, listMyGroups, type GroupItem, type GroupVisibility } from '@/api/groups'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import emptyDataIllustration from '@/assets/empty-data.svg'

const GROUP_PAGE_SIZE_OPTIONS = [10, 20, 50] as const

type MyGroupFilter = 'all' | 'owner' | 'member'
type DiscoverGroupFilter = 'all' | 'joined' | 'pending' | 'available'
type GroupSortKey = 'updated' | 'members' | 'skills' | 'name'

function formatTime(ms?: number | null): string {
  if (!ms) return '-'
  try {
    return new Date(ms).toLocaleString()
  } catch {
    return '-'
  }
}

function roleLabel(role: GroupItem['viewer_role'], t: ReturnType<typeof useTranslation>['t']): string {
  if (role === 'owner') return t('groups.role.owner')
  if (role === 'member') return t('groups.role.member')
  return '-'
}

function visibilityLabel(visibility: GroupVisibility, t: ReturnType<typeof useTranslation>['t']): string {
  return visibility === 'listed' ? t('groups.visibility.listedShort') : t('groups.visibility.private')
}

function groupStatusLabel(group: GroupItem, t: ReturnType<typeof useTranslation>['t']): string {
  if (group.viewer_role) return roleLabel(group.viewer_role, t)
  if (group.join_request_status === 'pending') return t('groups.joinStatus.pending')
  if (group.join_request_status === 'approved') return t('groups.joinStatus.approved')
  if (group.join_request_status === 'rejected') return t('groups.joinStatus.rejected')
  return t('groups.availableToJoin')
}

function groupStatusClass(group: GroupItem): string {
  if (group.viewer_role === 'owner') return 'bg-[#FEF3C7] text-[#92400E]'
  if (group.viewer_role === 'member') return 'bg-[#ECFDF5] text-[#047857]'
  if (group.join_request_status === 'pending') return 'bg-[#F3F4F6] text-[#4B5563]'
  return 'bg-[#EFF6FF] text-[#1D4ED8]'
}

function GroupIdCopy({ groupId, t }: { groupId: string; t: ReturnType<typeof useTranslation>['t'] }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    const done = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(groupId).then(done, () => {})
    } else {
      done()
    }
  }
  return (
    <div className="mt-1.5 flex min-w-0 items-center gap-1">
      <span
        className="min-w-0 truncate font-mono text-[11px] text-[#6B7280]"
        title={groupId}
      >
        {groupId}
      </span>
      <span
        role="button"
        tabIndex={0}
        onClick={handleCopy}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleCopy(e as unknown as React.MouseEvent) } }}
        title={copied ? t('common.copied') : t('groups.copyGroupId')}
        aria-label={t('groups.copyGroupId')}
        className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-[#9CA3AF] transition-colors hover:bg-[#F3F4F6] hover:text-[#111827]"
      >
        {copied ? (
          <Check className="h-4 w-4 text-emerald-600" aria-hidden />
        ) : (
          <Copy className="h-4 w-4" aria-hidden />
        )}
      </span>
    </div>
  )
}

export default function GroupsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { isAuthenticated, user } = useGitCodeAuth()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<'my' | 'discover'>('my')
  const [myFilter, setMyFilter] = useState<MyGroupFilter>('all')
  const [discoverFilter, setDiscoverFilter] = useState<DiscoverGroupFilter>('all')
  const [sortKey, setSortKey] = useState<GroupSortKey>('updated')
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [visibility, setVisibility] = useState<GroupVisibility>('private')
  const searchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/groups${location.search || ''}`)
    navigate('/login', { replace: true })
  }, [isAuthenticated, location.search, navigate])

  // `/` 聚焦搜索框（GitHub 风格），输入框聚焦时不触发
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/') return
      const el = document.activeElement
      const tag = el?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || (el as HTMLElement | null)?.isContentEditable) return
      e.preventDefault()
      searchInputRef.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const groupsQuery = useQuery(
    ['groups', tab, page, pageSize, search.trim(), myFilter, discoverFilter, sortKey],
    () => tab === 'discover'
      ? discoverGroups({ page, page_size: pageSize, keyword: search.trim() || undefined, filter_by: discoverFilter === 'all' ? undefined : discoverFilter, sort: sortKey })
      : listMyGroups({ page, page_size: pageSize, keyword: search.trim() || undefined, role: myFilter === 'all' ? undefined : myFilter, sort: sortKey }),
    {
      enabled: isAuthenticated,
      keepPreviousData: true,
      refetchOnMount: 'always',
    },
  )

  const createMutation = useMutation(
    () => createGroup({ name: name.trim(), description: description.trim() || null, visibility }),
    {
      onSuccess: group => {
        setCreateOpen(false)
        setName('')
        setDescription('')
        queryClient.invalidateQueries({ queryKey: ['groups'] })
        navigate(`/groups/${encodeURIComponent(group.group_id)}`)
      },
    },
  )

  const items = groupsQuery.data?.items ?? []
  const serverTotal = groupsQuery.data?.total ?? 0
  const q = search.trim().toLowerCase()
  const displayedTotal = serverTotal
  const displayedItems = items
  const summary = useMemo(() => ({
    owned: items.filter(group => group.viewer_role === 'owner').length,
    skills: items.reduce((sum, group) => sum + (group.skill_count || 0), 0),
  }), [items])

  useEffect(() => {
    setPage(1)
  }, [search, myFilter, discoverFilter, sortKey])

  useEffect(() => {
    if (displayedTotal <= 0 || !groupsQuery.data) return
    const totalPages = Math.max(1, Math.ceil(displayedTotal / pageSize))
    if (page > totalPages) setPage(totalPages)
  }, [groupsQuery.data, displayedTotal, pageSize, page])

  const errMsg = groupsQuery.error instanceof Error ? groupsQuery.error.message : ''
  const createErr = createMutation.error instanceof Error ? createMutation.error.message : ''

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-white">
        <Typography variant="body2" color="text.secondary">
          {t('profile.redirecting')}
        </Typography>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col bg-white">
      <AppHeader showPublish={false} />
      <div className="px-4 pt-4 md:px-[8.33%]">
        <Breadcrumbs items={[{ label: t('common.breadcrumb.home'), to: '/' }, { label: t('groups.breadcrumb') }]} />
      </div>

      <main className="flex flex-1 flex-col px-4 pb-10 pt-4 md:px-[8.33%]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-[#111827]">{t('groups.title')}</h1>
            <p className="mt-1 text-sm text-[#6B7280]">{t('groups.subtitle')}</p>
          </div>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="inline-flex h-9 w-fit items-center justify-center gap-1.5 rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-90"
          >
            <Plus className="h-4 w-4" aria-hidden />
            <span>{t('groups.create')}</span>
          </button>
        </div>

        {errMsg ? <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errMsg}</div> : null}

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-2xl border border-[#E5E7EB] bg-[#F9FAFB] p-4">
            <div className="text-xs text-[#6B7280]">{tab === 'my' ? t('groups.summary.myTotal') : t('groups.summary.discoverTotal')}</div>
            <div className="mt-1 text-2xl font-semibold text-[#111827]">{displayedTotal}</div>
          </div>
          <div className="rounded-2xl border border-[#E5E7EB] bg-[#F9FAFB] p-4">
            <div className="text-xs text-[#6B7280]">{t('groups.summary.owned')}</div>
            <div className="mt-1 text-2xl font-semibold text-[#111827]">{summary.owned}</div>
          </div>
          <div className="rounded-2xl border border-[#E5E7EB] bg-[#F9FAFB] p-4">
            <div className="text-xs text-[#6B7280]">{t('groups.summary.skills')}</div>
            <div className="mt-1 text-2xl font-semibold text-[#111827]">{summary.skills}</div>
          </div>
        </div>

        <div className="mt-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="grid w-full max-w-[360px] grid-cols-2 rounded-xl border border-[#E5E7EB] bg-[#F7F8FA] p-1 text-sm">
            <button
              type="button"
              onClick={() => { setTab('my'); setPage(1) }}
              className={`rounded-lg px-4 py-2 text-[13px] font-normal leading-5 !text-[#191919] transition-colors hover:!text-[#191919] ${tab === 'my' ? 'bg-white shadow-[0_1px_2px_rgba(16,24,40,0.05)]' : 'hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'}`}
            >
              {t('groups.myGroups')}{tab === 'my' ? ` · ${serverTotal}` : ''}
            </button>
            <button
              type="button"
              onClick={() => { setTab('discover'); setPage(1) }}
              className={`rounded-lg px-4 py-2 text-[13px] font-normal leading-5 !text-[#191919] transition-colors hover:!text-[#191919] ${tab === 'discover' ? 'bg-white shadow-[0_1px_2px_rgba(16,24,40,0.05)]' : 'hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'}`}
            >
              {t('groups.discover')}{tab === 'discover' ? ` · ${serverTotal}` : ''}
            </button>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative w-[min(46vw,460px)] min-w-[300px]">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9CA3AF]" aria-hidden />
              <input
                ref={searchInputRef}
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                onKeyDown={e => { if (e.key === 'Escape' && search) { setSearch(''); e.preventDefault() } }}
                placeholder={t('groups.searchPlaceholder')}
                className="h-10 w-full rounded-lg border border-[#E5E7EB] bg-white pl-10 pr-9 font-mono text-sm tracking-tight text-[#111827] placeholder:font-sans placeholder:text-[#9CA3AF] focus:border-[#4F46E5] focus:outline-none focus:ring-2 focus:ring-[#E0E7FF]"
              />
              {search ? (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#9CA3AF] transition-colors hover:text-[#4B5563]"
                  aria-label={t('common.clear')}
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              ) : (
                <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 select-none rounded border border-[#E5E7EB] bg-[#F9FAFB] px-1.5 py-0.5 font-sans text-[10px] font-medium text-[#9CA3AF] sm:inline-block">/</kbd>
              )}
            </div>
            <Select
              size="small"
              value={tab === 'my' ? myFilter : discoverFilter}
              onChange={e => tab === 'my' ? setMyFilter(e.target.value as MyGroupFilter) : setDiscoverFilter(e.target.value as DiscoverGroupFilter)}
              displayEmpty
              className="min-w-[150px] bg-white"
            >
              {tab === 'my' ? [
                <MenuItem key="all" value="all">{t('groups.filter.all')}</MenuItem>,
                <MenuItem key="owner" value="owner">{t('groups.filter.owner')}</MenuItem>,
                <MenuItem key="member" value="member">{t('groups.filter.member')}</MenuItem>,
              ] : [
                <MenuItem key="all" value="all">{t('groups.filter.all')}</MenuItem>,
                <MenuItem key="joined" value="joined">{t('groups.filter.joined')}</MenuItem>,
                <MenuItem key="pending" value="pending">{t('groups.filter.pending')}</MenuItem>,
                <MenuItem key="available" value="available">{t('groups.filter.available')}</MenuItem>,
              ]}
            </Select>
            <Select size="small" value={sortKey} onChange={e => setSortKey(e.target.value as GroupSortKey)} className="min-w-[150px] bg-white">
              <MenuItem value="updated">{t('groups.sort.updated')}</MenuItem>
              <MenuItem value="members">{t('groups.sort.members')}</MenuItem>
              <MenuItem value="skills">{t('groups.sort.skills')}</MenuItem>
              <MenuItem value="name">{t('groups.sort.name')}</MenuItem>
            </Select>
          </div>
        </div>

        <div className="mt-6 flex flex-1 flex-col">
          {groupsQuery.isLoading && !groupsQuery.data ? (
            <Typography variant="body2" className="text-slate-500">
              {t('plugins.loading')}
            </Typography>
          ) : displayedItems.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center py-16">
              <img src={emptyDataIllustration} alt="" aria-hidden className="h-32 w-32 select-none" draggable={false} />
              <div className="mt-4 text-sm text-[#6B7280]">{q || myFilter !== 'all' || discoverFilter !== 'all' ? t('groups.emptySearch') : t('groups.empty')}</div>
              {tab === 'my' && !q && myFilter === 'all' ? (
                <button type="button" onClick={() => setCreateOpen(true)} className="mt-3 text-sm font-medium text-[#0950DE] hover:underline">
                  {t('groups.createFirst')}
                </button>
              ) : q || myFilter !== 'all' || discoverFilter !== 'all' ? (
                <button type="button" onClick={() => { setSearch(''); setMyFilter('all'); setDiscoverFilter('all') }} className="mt-3 text-sm font-medium text-[#0950DE] hover:underline">
                  {t('groups.clearFilters')}
                </button>
              ) : null}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {displayedItems.map(group => (
                <button
                  type="button"
                  key={group.group_id}
                  onClick={() => navigate(`/groups/${encodeURIComponent(group.group_id)}`)}
                  className="rounded-2xl border border-[#E5E7EB] bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-[#C7D2FE] hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <h2 className="truncate text-base font-semibold text-[#111827]" title={group.name}>{group.name}</h2>
                        <span className="shrink-0 rounded-full bg-[#F3F4F6] px-2 py-0.5 text-xs font-medium text-[#6B7280]">
                          {visibilityLabel(group.visibility, t)}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-2 min-h-10 text-sm leading-5 text-[#6B7280]">
                        {group.description || t('groups.noDescription')}
                      </p>
                      <GroupIdCopy groupId={group.group_id} t={t} />
                    </div>
                    <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${groupStatusClass(group)}`}>
                      {groupStatusLabel(group, t)}
                    </span>
                  </div>
                  {group.viewer_can_manage ? (
                    <div className="mt-4 rounded-xl bg-[#EEF2FF] px-3 py-2 text-xs font-medium text-[#3730A3]">
                      {t('groups.manageHint')}
                    </div>
                  ) : null}
                  <div className="mt-4 grid grid-cols-3 gap-2 text-xs text-[#6B7280]">
                    <div className="flex items-center gap-1.5">
                      <Users className="h-3.5 w-3.5" aria-hidden />
                      <span>{t('groups.memberCount', { count: group.member_count })}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Shield className="h-3.5 w-3.5" aria-hidden />
                      <span>{t('groups.skillCount', { count: group.skill_count })}</span>
                    </div>
                    <div className="flex min-w-0 items-center gap-1.5">
                      <CalendarClock className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      <span className="truncate">{formatTime(group.update_time)}</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {displayedTotal > 0 && groupsQuery.data ? (
          <div className="mt-4 border-t border-[#e5e7eb] pt-4">
            <Pagination
              pager={{ total: displayedTotal, currentPage: page, pageSize, pageSizeOptions: [...GROUP_PAGE_SIZE_OPTIONS] }}
              loading={false}
              onPagerChange={(nextPage, nextSize) => {
                setPageSize(nextSize)
                setPage(nextPage)
              }}
            />
          </div>
        ) : null}
      </main>

      <Dialog open={createOpen} onClose={() => !createMutation.isLoading && setCreateOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>{t('groups.create')}</DialogTitle>
        <DialogContent>
          <div className="mt-2 flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm text-[#374151]">
              <span>{t('groups.form.name')}</span>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                maxLength={128}
                className="h-10 rounded-lg border border-[#E5E7EB] px-3 text-sm outline-none focus:border-[#4F46E5] focus:ring-2 focus:ring-[#E0E7FF]"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-[#374151]">
              <span>{t('groups.form.description')}</span>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={4}
                maxLength={4096}
                className="rounded-lg border border-[#E5E7EB] px-3 py-2 text-sm outline-none focus:border-[#4F46E5] focus:ring-2 focus:ring-[#E0E7FF]"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-[#374151]">
              <span>{t('groups.form.visibility')}</span>
              <Select size="small" value={visibility} onChange={e => setVisibility(e.target.value as GroupVisibility)}>
                <MenuItem value="private">{t('groups.visibility.private')}</MenuItem>
                <MenuItem value="listed">{t('groups.visibility.listed')}</MenuItem>
              </Select>
            </label>
            {createErr ? <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{createErr}</div> : null}
          </div>
        </DialogContent>
        <DialogActions>
          <button type="button" disabled={createMutation.isLoading} onClick={() => setCreateOpen(false)} className="rounded-lg px-3 py-2 text-sm text-[#6B7280] hover:bg-slate-50">
            {t('groups.cancel')}
          </button>
          <button
            type="button"
            disabled={!name.trim() || createMutation.isLoading}
            onClick={() => createMutation.mutate()}
            className="rounded-lg bg-[#1E54F9] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {createMutation.isLoading ? t('groups.saving') : t('groups.confirm')}
          </button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
