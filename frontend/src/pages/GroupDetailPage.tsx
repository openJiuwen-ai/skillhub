// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { Autocomplete, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Select, TextField, Typography } from '@mui/material'
import { ArrowLeft, CalendarClock, Check, ClipboardList, Copy, Pencil, Shield, Trash2, UserMinus, Users, X } from 'lucide-react'
import { getPlugins } from '@/api/plugin'
import { AppHeader } from '@/components/Common/AppHeader'
import { resolvePluginIconUrl } from '@/utils/resolvePluginIconUrl'
import { Breadcrumbs } from '@/components/Common/Breadcrumbs'
import { Pagination } from '@/components/Common/common-table'
import {
  createJoinRequest,
  decideGroupSkillGrant,
  decideJoinRequest,
  deleteGroup,
  getGroup,
  grantSkillToGroup,
  listGroupGrants,
  listGroupMembers,
  listJoinRequests,
  removeGroupMember,
  revokeSkillFromGroup,
  searchGrantableSkills,
  updateGroup,
  type GrantableSkillItem,
  type GroupItem,
  type GroupSkillGrantItem,
  type GroupSkillGrantListData,
  type GroupMemberItem,
  type GroupMemberRole,
  type GroupVisibility,
  type GrantStatus,
  type JoinRequestStatus,
} from '@/api/groups'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import emptyDataIllustration from '@/assets/empty-data.svg'

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const
type DetailTab = 'members' | 'grants' | 'grant-requests' | 'join-requests'

function tabFromHash(hash: string, manageable: boolean): DetailTab {
  const id = hash.replace(/^#/, '')
  if (id === 'grants') return 'grants'
  if (id === 'grant-requests' && manageable) return 'grant-requests'
  if (id === 'join-requests' && manageable) return 'join-requests'
  return 'members'
}

function formatTime(ms?: number | null): string {
  if (!ms) return '-'
  try {
    return new Date(ms).toLocaleString()
  } catch {
    return '-'
  }
}

function roleLabel(role: GroupMemberRole | null | undefined, t: ReturnType<typeof useTranslation>['t']): string {
  if (role === 'owner') return t('groups.role.owner')
  if (role === 'member') return t('groups.role.member')
  return '-'
}

function statusLabel(status: JoinRequestStatus, t: ReturnType<typeof useTranslation>['t']): string {
  if (status === 'approved') return t('groups.joinStatus.approved')
  if (status === 'rejected') return t('groups.joinStatus.rejected')
  return t('groups.joinStatus.pending')
}

function grantStatusLabel(status: GrantStatus, t: ReturnType<typeof useTranslation>['t']): string {
  if (status === 'active') return t('groups.grantStatus.active')
  if (status === 'rejected') return t('groups.grantStatus.rejected')
  if (status === 'revoked') return t('groups.grantStatus.revoked')
  return t('groups.grantStatus.pending')
}

function grantAccessSourceLabel(source: GroupSkillGrantItem['viewer_access_source'], t: ReturnType<typeof useTranslation>['t']): string | null {
  if (source === 'owner') return t('groups.grantAccessSource.owner')
  if (source === 'group') return t('groups.grantAccessSource.group')
  if (source === 'public') return t('groups.grantAccessSource.public')
  if (source === 'admin') return t('groups.grantAccessSource.admin')
  return null
}

function canManage(group?: GroupItem | null): boolean {
  return Boolean(group?.viewer_can_manage) || group?.viewer_role === 'owner'
}

function canDelete(group?: GroupItem | null): boolean {
  return Boolean(group?.viewer_can_manage) || group?.viewer_role === 'owner'
}

export default function GroupDetailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const params = useParams()
  const queryClient = useQueryClient()
  const { isAuthenticated, user } = useGitCodeAuth()
  const groupId = params.groupId || ''
  const [memberPage, setMemberPage] = useState(1)
  const [memberPageSize, setMemberPageSize] = useState(20)
  const [requestPage, setRequestPage] = useState(1)
  const [requestPageSize, setRequestPageSize] = useState(20)
  const [requestStatus, setRequestStatus] = useState<JoinRequestStatus | 'all'>('pending')
  const [grantPage, setGrantPage] = useState(1)
  const [grantPageSize, setGrantPageSize] = useState(20)
  const [pendingGrantPage, setPendingGrantPage] = useState(1)
  const [pendingGrantPageSize, setPendingGrantPageSize] = useState(20)
  const [grantRequestStatus, setGrantRequestStatus] = useState<GrantStatus | 'all'>('pending')
  const [editOpen, setEditOpen] = useState(false)
  const [joinOpen, setJoinOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editVisibility, setEditVisibility] = useState<GroupVisibility>('private')
  const [idCopied, setIdCopied] = useState(false)
  const [grantKeyword, setGrantKeyword] = useState('')
  const [selectedSkill, setSelectedSkill] = useState<GrantableSkillItem | null>(null)
  const [joinMessage, setJoinMessage] = useState('')
  const [activeTab, setActiveTab] = useState<DetailTab>(() => tabFromHash(location.hash, true))
  const [removingMemberId, setRemovingMemberId] = useState<string | null>(null)
  const [decidingRequestId, setDecidingRequestId] = useState<string | null>(null)
  const [decidingGrantAssetId, setDecidingGrantAssetId] = useState<string | null>(null)
  const [revokingAssetId, setRevokingAssetId] = useState<string | null>(null)

  useEffect(() => {
    if (isAuthenticated) return
    setPostLoginRedirect(`/groups/${encodeURIComponent(groupId)}${location.search || ''}${location.hash || ''}`)
    navigate('/login', { replace: true })
  }, [groupId, isAuthenticated, location.search, navigate])

  const groupQuery = useQuery(['group', groupId], () => getGroup(groupId), {
    enabled: isAuthenticated && Boolean(groupId),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  })

  const group = groupQuery.data
  const manageable = canManage(group)
  const owner = canDelete(group)
  const member = Boolean(group?.viewer_role)
  const canLeave = member && !owner
  const canViewGroupDetails = member || manageable
  const canGrant = Boolean(group) && (member || manageable || group?.visibility === 'listed')

  const membersQuery = useQuery(
    ['group-members', groupId, memberPage, memberPageSize],
    () => listGroupMembers(groupId, { page: memberPage, page_size: memberPageSize }),
    { enabled: isAuthenticated && Boolean(groupId) && (member || manageable) && activeTab === 'members', keepPreviousData: true },
  )

  const requestsQuery = useQuery(
    ['group-join-requests', groupId, requestPage, requestPageSize, requestStatus],
    () => listJoinRequests(groupId, { page: requestPage, page_size: requestPageSize, status: requestStatus }),
    { enabled: isAuthenticated && manageable && activeTab === 'join-requests', keepPreviousData: true },
  )

  const pendingRequestsQuery = useQuery(
    ['group-join-requests-pending-count', groupId],
    () => listJoinRequests(groupId, { page: 1, page_size: 1, status: 'pending' }),
    { enabled: isAuthenticated && manageable, keepPreviousData: true, staleTime: 30_000 },
  )

  const grantsQuery = useQuery(
    ['group-grants', groupId, grantPage, grantPageSize],
    () => listGroupGrants(groupId, { page: grantPage, page_size: grantPageSize, status: 'active' }),
    { enabled: isAuthenticated && Boolean(groupId) && canViewGroupDetails && activeTab === 'grants', keepPreviousData: true, refetchOnMount: 'always', staleTime: 0 },
  )

  const pendingGrantCountQuery = useQuery(
    ['group-grants-pending-count', groupId],
    () => listGroupGrants(groupId, { page: 1, page_size: 1, status: 'pending' }),
    { enabled: isAuthenticated && manageable, keepPreviousData: true, staleTime: 30_000 },
  )

  const pendingGrantsQuery = useQuery(
    ['group-grants-pending', groupId, pendingGrantPage, pendingGrantPageSize, grantRequestStatus],
    () => listGroupGrants(groupId, { page: pendingGrantPage, page_size: pendingGrantPageSize, status: grantRequestStatus }),
    { enabled: isAuthenticated && manageable && activeTab === 'grant-requests', keepPreviousData: true },
  )

  const skillsQuery = useQuery(
    ['group-grantable-skills', groupId, grantKeyword.trim()],
    () => searchGrantableSkills({ keyword: grantKeyword.trim() || undefined, page_size: 20, group_id: groupId }),
    { enabled: canGrant && activeTab === 'grants', keepPreviousData: true, refetchOnMount: 'always', staleTime: 0 },
  )

  const grantStatesQuery = useQuery(
    ['group-grant-states', groupId],
    () => listGroupGrants(groupId, { page: 1, page_size: 100, status: 'all' }),
    { enabled: isAuthenticated && Boolean(groupId) && member && activeTab === 'grants', keepPreviousData: true, refetchOnMount: 'always', staleTime: 0 },
  )


  const pendingGrantCount = pendingGrantCountQuery.data?.total ?? 0
  const pendingJoinCount = pendingRequestsQuery.data?.total ?? 0
  const activeGrantCount = group?.skill_count ?? grantsQuery.data?.total ?? 0
  const openTodoCount = pendingGrantCount + pendingJoinCount
  const grantActionLabel = manageable ? t('groups.grant') : t('groups.requestGrant')
  const grantStateByAssetId = useMemo(() => {
    const states = new Map<string, GrantStatus>()
    for (const item of grantStatesQuery.data?.items ?? []) {
      if (item.status === 'active' || item.status === 'pending') states.set(item.asset_id, item.status)
    }
    for (const item of grantsQuery.data?.items ?? []) {
      if (item.status === 'active') states.set(item.asset_id, item.status)
    }
    for (const item of skillsQuery.data?.items ?? []) {
      if (item.group_grant_status === 'active' || item.group_grant_status === 'pending') states.set(item.asset_id, item.group_grant_status)
    }
    return states
  }, [grantStatesQuery.data?.items, grantsQuery.data?.items, skillsQuery.data?.items])
  const grantableSkillOptions = skillsQuery.data?.items ?? []
  const selectedSkillGrantStatus = selectedSkill ? selectedSkill.group_grant_status || grantStateByAssetId.get(selectedSkill.asset_id) || undefined : undefined
  const openTab = (tab: DetailTab) => {
    setActiveTab(tab)
    navigate({ hash: tab }, { replace: true })
    if (tab === 'join-requests') {
      void queryClient.invalidateQueries({ queryKey: ['group-join-requests', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['group-join-requests-pending-count', groupId] })
    }
    if (tab === 'grant-requests') {
      void queryClient.invalidateQueries({ queryKey: ['group-grants-pending', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['group-grants-pending-count', groupId] })
    }
  }

  const openGrantSkill = async (grant: GroupSkillGrantItem) => {
    let version = grant.public_latest_version || grant.latest_version || ''
    if (!version) {
      try {
        const detail = await getPlugins({ asset_id: grant.asset_id, page: 1, page_size: 1 })
        const item = detail.data.items[0]
        const versions = Array.isArray(item?.all_versions) ? item.all_versions : []
        const latest = item?.public_latest_version || item?.latest_version || ''
        version = latest && versions.includes(latest) ? latest : versions[versions.length - 1] || latest || ''
      } catch {
        version = ''
      }
    }
    const query = version ? `?version=${encodeURIComponent(version)}` : ''
    navigate(`/skills/${encodeURIComponent(grant.asset_id)}${query}`)
  }

  const invalidateGroup = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['group', groupId] }),
      queryClient.invalidateQueries({ queryKey: ['groups'] }),
      queryClient.invalidateQueries({ queryKey: ['profile-my-groups'] }),
    ])
  }

  const updateMutation = useMutation(() => updateGroup(groupId, { name: editName.trim(), description: editDescription.trim() || null, visibility: editVisibility }), {
    onSuccess: async () => {
      setEditOpen(false)
      await invalidateGroup()
    },
  })

  const deleteMutation = useMutation(() => deleteGroup(groupId), {
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['groups'] }),
        queryClient.invalidateQueries({ queryKey: ['profile-my-groups'] }),
      ])
      navigate('/groups', { replace: true })
    },
  })

  const removeMemberMutation = useMutation((member: GroupMemberItem) => removeGroupMember(groupId, member.user_id), {
    onSuccess: async (_, removedMember) => {
      if (removedMember.user_id === user?.id) {
        await invalidateGroup()
        navigate('/groups', { replace: true })
        return
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['group-members', groupId] }),
        invalidateGroup(),
      ])
    },
    onSettled: () => setRemovingMemberId(null),
  })

  const joinMutation = useMutation(() => createJoinRequest(groupId, { message: joinMessage.trim() || null }), {
    onSuccess: async (result) => {
      setJoinOpen(false)
      setJoinMessage('')
      await invalidateGroup()
      if (result.status === 'approved') {
        // 系统管理员直接加入成功
        await queryClient.invalidateQueries({ queryKey: ['group-members', groupId] })
        window.alert(t('groups.joinSuccess'))
      } else {
        window.alert(t('groups.joinSubmitted'))
      }
    },
  })
  const hasPendingJoinRequest = group?.join_request_status === 'pending' || (joinMutation.data?.status === 'pending')

  const decideMutation = useMutation(
    ({ requestId, status }: { requestId: string; status: 'approved' | 'rejected' }) => decideJoinRequest(groupId, requestId, status),
    {
      onSuccess: async () => {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['group-join-requests', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-join-requests-pending-count', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-members', groupId] }),
          invalidateGroup(),
        ])
      },
      onError: error => window.alert(errorText(error) || t('groups.requestDecisionFailed')),
      onSettled: () => setDecidingRequestId(null),
    },
  )

  const grantMutation = useMutation(() => grantSkillToGroup(groupId, selectedSkill?.asset_id || ''), {
    onSuccess: async grant => {
      setSelectedSkill(null)
      setGrantKeyword('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['group-grants', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grants-pending', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grants-pending-count', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grant-states', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grantable-skills', groupId] }),
        invalidateGroup(),
      ])
      window.alert(t(grant.status === 'active' ? 'groups.grantSuccess' : 'groups.grantRequestSuccess'))
    },
    onError: error => window.alert(errorText(error) || t('groups.grantFailed')),
  })

  const decideGrantMutation = useMutation(
    ({ assetId, status }: { assetId: string; status: 'active' | 'rejected' }) => decideGroupSkillGrant(groupId, assetId, status),
    {
      onSuccess: async () => {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['group-grants', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-grants-pending', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-grants-pending-count', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-grant-states', groupId] }),
          queryClient.invalidateQueries({ queryKey: ['group-grantable-skills', groupId] }),
          invalidateGroup(),
        ])
      },
      onSettled: () => setDecidingGrantAssetId(null),
    },
  )

  const revokeMutation = useMutation((assetId: string) => revokeSkillFromGroup(groupId, assetId), {
    onMutate: async assetId => {
      await queryClient.cancelQueries({ queryKey: ['group-grants', groupId] })
      const previousGrants = queryClient.getQueryData<GroupSkillGrantListData>(['group-grants', groupId, grantPage, grantPageSize])
      if (previousGrants) {
        queryClient.setQueryData(['group-grants', groupId, grantPage, grantPageSize], {
          ...previousGrants,
          total: Math.max(0, previousGrants.total - 1),
          items: previousGrants.items.filter(item => item.asset_id !== assetId),
        })
      }
      return { previousGrants }
    },
    onError: (_error, _assetId, context) => {
      if (context?.previousGrants) queryClient.setQueryData(['group-grants', groupId, grantPage, grantPageSize], context.previousGrants)
    },
    onSuccess: async () => {
      setActiveTab('grants')
      setSelectedSkill(null)
      setGrantKeyword('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['group-grants', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grants-pending', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grants-pending-count', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grant-states', groupId] }),
        queryClient.invalidateQueries({ queryKey: ['group-grantable-skills', groupId] }),
        invalidateGroup(),
      ])
    },
    onSettled: () => setRevokingAssetId(null),
  })

  useEffect(() => {
    if (!group) return
    setEditName(group.name)
    setEditDescription(group.description || '')
    setEditVisibility(group.visibility || 'private')
  }, [group])

  useEffect(() => {
    if (!group) return
    if (location.hash) {
      const tab = tabFromHash(location.hash, canManage(group))
      setActiveTab(tab)
      return
    }
    setActiveTab('members')
  }, [group, groupId, location.hash, queryClient])

  const pageErrors = useMemo(() => {
    const errors = [groupQuery.error, membersQuery.error, requestsQuery.error, pendingRequestsQuery.error, grantsQuery.error, pendingGrantsQuery.error]
    return errors.filter(Boolean).map(err => (err instanceof Error ? err.message : String(err)))
  }, [groupQuery.error, grantsQuery.error, membersQuery.error, pendingGrantsQuery.error, pendingRequestsQuery.error, requestsQuery.error])

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-white">
        <Typography variant="body2" color="text.secondary">
          {t('profile.redirecting')}
        </Typography>
      </div>
    )
  }

  if (groupQuery.isLoading) {
    return (
      <div className="flex min-h-dvh flex-col bg-white">
        <AppHeader showPublish={false} />
        <div className="px-4 py-8 md:px-[8.33%]">
          <Typography variant="body2" className="text-slate-500">{t('plugins.loading')}</Typography>
        </div>
      </div>
    )
  }

  if (!group) {
    return (
      <div className="flex min-h-dvh flex-col bg-white">
        <AppHeader showPublish={false} />
        <main className="flex flex-1 flex-col items-center justify-center px-4 py-16">
          <img src={emptyDataIllustration} alt="" aria-hidden className="h-32 w-32 select-none" draggable={false} />
          <div className="mt-4 text-sm text-[#6B7280]">{t('groups.notFoundOrNoPermission')}</div>
          <button type="button" onClick={() => navigate('/groups')} className="mt-3 text-sm font-medium text-[#0950DE] hover:underline">
            {t('groups.backToGroups')}
          </button>
        </main>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col bg-white">
      <AppHeader showPublish={false} />
      <div className="px-4 pt-4 md:px-[8.33%]">
        <Breadcrumbs items={[{ label: t('common.breadcrumb.home'), to: '/' }, { label: t('groups.breadcrumb'), to: '/groups' }, { label: group.name }]} />
      </div>

      <main className="flex flex-1 flex-col gap-5 px-4 pb-10 pt-4 md:px-[8.33%]">
        <button type="button" onClick={() => navigate('/groups')} className="inline-flex w-fit items-center gap-1.5 text-sm text-[#4B5563] hover:text-[#111827]">
          <ArrowLeft className="h-4 w-4" aria-hidden />
          <span>{t('groups.backToGroups')}</span>
        </button>

        {pageErrors.length ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{pageErrors[0]}</div>
        ) : null}

        <section id="overview" className="scroll-mt-20 overflow-hidden rounded-3xl border border-[#E5E7EB] bg-[linear-gradient(135deg,#F8FAFF_0%,#FFFFFF_48%,#F7F2FF_100%)] shadow-sm">
          <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-start lg:justify-between lg:p-6">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold text-[#111827]">{group.name}</h1>
                <span className="rounded-full bg-[#EEF2FF] px-2.5 py-1 text-xs font-medium text-[#4F46E5]">{roleLabel(group.viewer_role, t)}</span>
                <span className="rounded-full bg-white/80 px-2.5 py-1 text-xs font-medium text-[#6B7280] ring-1 ring-[#E5E7EB]">{t(`groups.visibility.${group.visibility || 'private'}`)}</span>
              </div>
              <p className="mt-3 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-[#4B5563]">{group.description || t('groups.noDescription')}</p>
              <div className="mt-2 flex items-center gap-1.5">
                <span className="text-xs text-[#9CA3AF]">{t('groups.groupIdLabel')}</span>
                <span className="font-mono text-xs text-[#6B7280]" title={group.group_id}>{group.group_id}</span>
                <button
                  type="button"
                  onClick={() => {
                    if (navigator.clipboard?.writeText) {
                      navigator.clipboard.writeText(group.group_id).then(
                        () => { setIdCopied(true); setTimeout(() => setIdCopied(false), 1500) },
                        () => {},
                      )
                    }
                  }}
                  title={idCopied ? t('common.copied') : t('groups.copyGroupId')}
                  aria-label={t('groups.copyGroupId')}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-[#9CA3AF] transition-colors hover:bg-white/60 hover:text-[#111827]"
                >
                  {idCopied ? <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
                </button>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <InfoCell icon={<Users className="h-4 w-4" aria-hidden />} label={t('groups.owner')} value={group.owner_name || group.owner_id} />
                <InfoCell icon={<Users className="h-4 w-4" aria-hidden />} label={t('groups.members')} value={String(group.member_count)} />
                <InfoCell icon={<Shield className="h-4 w-4" aria-hidden />} label={t('groups.grantedSkills')} value={String(activeGrantCount)} />
                <InfoCell icon={<ClipboardList className="h-4 w-4" aria-hidden />} label={t('groups.todos')} value={String(openTodoCount)} />
                <InfoCell icon={<CalendarClock className="h-4 w-4" aria-hidden />} label={t('groups.updatedAt')} value={formatTime(group.update_time)} />
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2 lg:justify-end">
              {!group.viewer_role ? (
                <button
                  type="button"
                  disabled={hasPendingJoinRequest || joinMutation.isLoading}
                  onClick={() => setJoinOpen(true)}
                  className="rounded-lg bg-[#1E54F9] px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {hasPendingJoinRequest ? t('groups.joinPending') : t('groups.applyJoin')}
                </button>
              ) : null}
              {canLeave ? (
                <button
                  type="button"
                  disabled={removeMemberMutation.isLoading}
                  onClick={() => {
                    if (removeMemberMutation.isLoading) return
                    if (window.confirm(t('groups.leaveConfirm'))) {
                      setRemovingMemberId(user?.id || '')
                      removeMemberMutation.mutate({ user_id: user?.id || '', user_name: user?.name || user?.login || null, role: group.viewer_role as GroupMemberRole, create_time: 0, update_time: 0 })
                    }
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-sm text-red-600 ring-1 ring-red-100 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <UserMinus className="h-4 w-4" aria-hidden />
                  {t('groups.leave')}
                </button>
              ) : null}
              {manageable ? (
                <button type="button" onClick={() => setEditOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-sm text-[#374151] ring-1 ring-[#E5E7EB] hover:bg-slate-50">
                  <Pencil className="h-4 w-4" aria-hidden />
                  {t('groups.edit')}
                </button>
              ) : null}
              {owner ? (
                <button
                  type="button"
                  disabled={deleteMutation.isLoading}
                  onClick={() => {
                    if (deleteMutation.isLoading) return
                    if (window.confirm(t('groups.deleteConfirm'))) deleteMutation.mutate()
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-sm text-red-600 ring-1 ring-red-100 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                  {t('groups.delete')}
                </button>
              ) : null}
            </div>
          </div>
        </section>

        {manageable && openTodoCount > 0 ? (
          <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="text-sm font-semibold text-amber-950">{t('groups.todoCenterTitle', { count: openTodoCount })}</div>
                <div className="mt-0.5 text-xs text-amber-800">{t('groups.todoCenterSubtitle')}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                {pendingGrantCount > 0 ? <TodoButton label={t('groups.todoGrantRequests', { count: pendingGrantCount })} onClick={() => openTab('grant-requests')} /> : null}
                {pendingJoinCount > 0 ? <TodoButton label={t('groups.todoJoinRequests', { count: pendingJoinCount })} onClick={() => openTab('join-requests')} /> : null}
              </div>
            </div>
          </section>
        ) : null}

        <section className="rounded-2xl border border-[#E5E7EB] bg-[#F7F8FA] p-1">
          <div className="flex gap-1 overflow-x-auto">
            <TabButton key="members" active={activeTab === 'members'} onClick={() => openTab('members')} label={t('groups.members')} />
            {manageable ? <TabButton key="grant-requests" active={activeTab === 'grant-requests'} onClick={() => openTab('grant-requests')} label={t('groups.grantRequests')} count={pendingGrantCount} /> : null}
            <TabButton key="grants" active={activeTab === 'grants'} onClick={() => openTab('grants')} label={t('groups.grants')} />
            {manageable ? <TabButton key="join-requests" active={activeTab === 'join-requests'} onClick={() => openTab('join-requests')} label={t('groups.requests')} count={pendingJoinCount} /> : null}
          </div>
        </section>

        {manageable && activeTab === 'grant-requests' ? (
          <Panel id="grant-requests" title={t('groups.grantRequests')} subtitle={t('groups.grantRequestsSubtitle')} badge={pendingGrantCount > 0 ? String(pendingGrantCount) : undefined} action={(
            <Select size="small" value={grantRequestStatus} onChange={e => { setGrantRequestStatus(e.target.value as GrantStatus | 'all'); setPendingGrantPage(1) }}>
              <MenuItem value="pending">{t('groups.grantStatus.pending')}</MenuItem>
              <MenuItem value="active">{t('groups.grantStatus.active')}</MenuItem>
              <MenuItem value="rejected">{t('groups.grantStatus.rejected')}</MenuItem>
              <MenuItem value="revoked">{t('groups.grantStatus.revoked')}</MenuItem>
              <MenuItem value="all">{t('groups.grantStatus.all')}</MenuItem>
            </Select>
          )}>
            <div className="flex flex-col divide-y divide-[#F3F4F6]">
              {(pendingGrantsQuery.data?.items ?? []).map(grant => {
                const skillTitle = grant.skill_display_name || grant.skill_name || grant.asset_id
                const deciding = decidingGrantAssetId === grant.asset_id
                return (
                  <div key={grant.asset_id} className="flex flex-col gap-3 py-3 md:flex-row md:items-center md:justify-between">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-[#111827]" title={skillTitle}>{skillTitle}</div>
                      <div className="truncate text-xs text-[#9CA3AF]">{grant.asset_id}</div>
                      <div className="mt-1 truncate text-xs text-[#6B7280]">{grantStatusLabel(grant.status, t)} · {formatTime(grant.update_time)}</div>
                    </div>
                    {grant.status === 'pending' ? (
                      <div className="flex shrink-0 gap-2">
                        <button type="button" disabled={deciding || decideGrantMutation.isLoading} onClick={() => { setDecidingGrantAssetId(grant.asset_id); decideGrantMutation.mutate({ assetId: grant.asset_id, status: 'active' }) }} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
                          <Check className="h-4 w-4" aria-hidden />
                          {t('groups.approve')}
                        </button>
                        <button type="button" disabled={deciding || decideGrantMutation.isLoading} onClick={() => { setDecidingGrantAssetId(grant.asset_id); decideGrantMutation.mutate({ assetId: grant.asset_id, status: 'rejected' }) }} className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
                          <X className="h-4 w-4" aria-hidden />
                          {t('groups.reject')}
                        </button>
                      </div>
                    ) : null}
                  </div>
                )
              })}
              {pendingGrantsQuery.data?.items.length === 0 ? <EmptyLine text={grantRequestStatus === 'pending' ? t('groups.emptyGrantRequests') : t('groups.emptyGrantRequestsAll')} /> : null}
            </div>
            {pendingGrantsQuery.data && pendingGrantsQuery.data.total > 0 ? (
              <Pager total={pendingGrantsQuery.data.total} page={pendingGrantPage} pageSize={pendingGrantPageSize} onChange={(p, s) => { setPendingGrantPage(p); setPendingGrantPageSize(s) }} />
            ) : null}
          </Panel>
        ) : null}

        {activeTab === 'members' ? (
          <Panel id="members" title={t('groups.members')} subtitle={t('groups.membersSubtitle')}>
            {!canViewGroupDetails ? <EmptyLine text={t('groups.membersVisibleAfterJoin')} /> : null}
            {canViewGroupDetails ? (
              <>
                <div className="flex flex-col divide-y divide-[#F3F4F6]">
                  {(membersQuery.data?.items ?? []).map(member => {
                    const removing = removingMemberId === member.user_id
                    return (
                      <div key={member.user_id} className="flex items-center justify-between gap-3 py-3">
                        <div className="flex min-w-0 items-center gap-3">
                          <UserAvatar name={member.user_name} userId={member.user_id} />
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium text-[#111827]">{member.user_name || member.user_id}</div>
                            <div className="truncate text-xs text-[#9CA3AF]">{member.user_id} · {roleLabel(member.role, t)}</div>
                          </div>
                        </div>
                        {manageable && member.role !== 'owner' ? (
                          <button type="button" disabled={removing || removeMemberMutation.isLoading} onClick={() => { if (removeMemberMutation.isLoading) return; if (window.confirm(t('groups.removeMemberConfirm', { name: member.user_name || member.user_id }))) { setRemovingMemberId(member.user_id); removeMemberMutation.mutate(member) } }} className="rounded-lg p-2 text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50" aria-label={t('groups.removeMember')}>
                            <UserMinus className="h-4 w-4" aria-hidden />
                          </button>
                        ) : null}
                      </div>
                    )
                  })}
                  {membersQuery.data?.items.length === 0 ? <EmptyLine text={t('groups.emptyMembers')} /> : null}
                </div>
                {membersQuery.data && membersQuery.data.total > 0 ? (
                  <Pager total={membersQuery.data.total} page={memberPage} pageSize={memberPageSize} onChange={(p, s) => { setMemberPage(p); setMemberPageSize(s) }} />
                ) : null}
              </>
            ) : null}
          </Panel>
        ) : null}

        {activeTab === 'grants' ? (
          <>
            {canGrant ? (
              <Panel id="grant-request" title={t('groups.grantRequestForGroupTitle')} subtitle={canViewGroupDetails ? t('groups.grantRequestMemberSubtitle') : t('groups.grantRequestForGroupSubtitle')}>
                {!canViewGroupDetails ? <div className="mb-3 text-xs leading-5 text-[#4B5563]">{t('groups.grantRequestForGroupHint')}</div> : null}
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Autocomplete
                    className="min-w-0 flex-1 bg-white"
                    size="small"
                    options={grantableSkillOptions}
                    value={selectedSkill}
                    inputValue={grantKeyword}
                    loading={skillsQuery.isFetching}
                    onInputChange={(_, value) => setGrantKeyword(value)}
                    onChange={(_, value) => setSelectedSkill(value)}
                    getOptionDisabled={option => !option.grantable || Boolean(grantStateByAssetId.get(option.asset_id) || option.group_grant_status)}
                    getOptionLabel={option => `${option.display_name || option.name} (${option.asset_id})`}
                    renderOption={(props, option) => {
                      const status = option.group_grant_status || grantStateByAssetId.get(option.asset_id)
                      const title = option.display_name || option.name || option.asset_id
                      return (
                        <li {...props} key={option.asset_id}>
                          <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
                            <div className="flex min-w-0 items-center gap-2">
                              <SkillIcon title={title} assetId={option.asset_id} size="sm" />
                              <span className="truncate">{title} ({option.asset_id})</span>
                            </div>
                            {!option.grantable
                              ? <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-600">{t('groups.notGrantable')}</span>
                              : status
                                ? <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{grantStatusLabel(status, t)}</span>
                                : null}
                          </div>
                        </li>
                      )
                    }}
                    renderInput={params => <TextField {...params} label={t('groups.grantRequestSkillLabel')} placeholder={t('groups.skillSearchPlaceholder')} />}
                  />
                  <button type="button" disabled={!selectedSkill || !selectedSkill.grantable || Boolean(selectedSkillGrantStatus) || grantMutation.isLoading} onClick={() => grantMutation.mutate()} className="rounded-lg bg-[#1E54F9] px-3 py-2 text-sm font-medium text-white disabled:opacity-50 sm:shrink-0">
                    {selectedSkillGrantStatus ? grantStatusLabel(selectedSkillGrantStatus, t) : selectedSkill && !selectedSkill.grantable ? (selectedSkill.not_grantable_reason || t('groups.notGrantable')) : grantActionLabel}
                  </button>
                </div>
              </Panel>
            ) : null}

            <Panel id="grants" title={t('groups.grantedSkills')} subtitle={t('groups.grantsSubtitle')}>
              {!canViewGroupDetails ? <EmptyLine text={t('groups.grantsVisibleAfterJoin')} /> : null}
              {canViewGroupDetails ? <div className="flex flex-col divide-y divide-[#F3F4F6]">
                {(grantsQuery.data?.items ?? []).map(grant => {
                  const skillTitle = grant.skill_display_name || grant.skill_name || grant.asset_id
                  const accessSourceLabel = grantAccessSourceLabel(grant.viewer_access_source, t)
                  const canRevokeGrant = manageable || grant.viewer_access_source === 'owner'
                  const revoking = revokingAssetId === grant.asset_id
                  return (
                    <div
                      key={grant.asset_id}
                      role="button"
                      tabIndex={0}
                      onClick={() => { void openGrantSkill(grant) }}
                      onKeyDown={e => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          void openGrantSkill(grant)
                        }
                      }}
                      className="group flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 transition-all hover:bg-white hover:shadow-[0_4px_12px_rgba(16,24,40,0.06)]"
                    >
                      <SkillIcon title={skillTitle} iconUri={grant.icon_uri} assetId={grant.asset_id} />
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <div className="truncate text-sm font-semibold text-[#111827] group-hover:text-[#111827]" title={skillTitle}>{skillTitle}</div>
                          <StatusBadge text={grantStatusLabel(grant.status, t)} tone="green" />
                          {accessSourceLabel ? <StatusBadge text={accessSourceLabel} tone="blue" /> : null}
                        </div>
                        <div className="mt-1 truncate text-xs text-[#9CA3AF]">{grant.asset_id}</div>
                      </div>
                      {canRevokeGrant ? (
                        <button
                          type="button"
                          disabled={revoking || revokeMutation.isLoading}
                          onClick={e => {
                            e.stopPropagation()
                            if (revokeMutation.isLoading) return
                            if (!window.confirm(t('groups.revokeGrantConfirm', { name: skillTitle }))) return
                            setRevokingAssetId(grant.asset_id)
                            revokeMutation.mutate(grant.asset_id)
                          }}
                          className="rounded-lg p-2 text-red-600 opacity-80 hover:bg-red-50 hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-50"
                          aria-label={t('groups.revoke')}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </button>
                      ) : null}
                    </div>
                  )
                })}
                {grantsQuery.data?.items.length === 0 ? <EmptyLine text={t('groups.emptyGrants')} /> : null}
              </div> : null}
              {canViewGroupDetails && grantsQuery.data && grantsQuery.data.total > 0 ? (
                <Pager total={grantsQuery.data.total} page={grantPage} pageSize={grantPageSize} onChange={(p, s) => { setGrantPage(p); setGrantPageSize(s) }} />
              ) : null}
            </Panel>
          </>
        ) : null}

        {manageable && activeTab === 'join-requests' ? (
          <Panel id="join-requests" title={t('groups.requests')} subtitle={t('groups.requestsSubtitle')} action={(
            <Select size="small" value={requestStatus} onChange={e => { setRequestStatus(e.target.value as JoinRequestStatus | 'all'); setRequestPage(1) }}>
              <MenuItem value="pending">{t('groups.joinStatus.pending')}</MenuItem>
              <MenuItem value="approved">{t('groups.joinStatus.approved')}</MenuItem>
              <MenuItem value="rejected">{t('groups.joinStatus.rejected')}</MenuItem>
              <MenuItem value="all">{t('groups.joinStatus.all')}</MenuItem>
            </Select>
          )}>
            <div className="flex flex-col divide-y divide-[#F3F4F6]">
              {(requestsQuery.data?.items ?? []).map(req => {
                const deciding = decidingRequestId === req.request_id
                return (
                  <div key={req.request_id} className="flex flex-col gap-3 py-3 md:flex-row md:items-center md:justify-between">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-[#111827]">{req.user_name || req.user_id}</div>
                      <div className="mt-0.5 text-xs text-[#9CA3AF]">{req.user_id} · {statusLabel(req.status, t)} · {formatTime(req.create_time)}</div>
                      {req.message ? <div className="mt-1 text-sm text-[#6B7280]">{req.message}</div> : null}
                    </div>
                    {req.status === 'pending' ? (
                      <div className="flex shrink-0 gap-2">
                        <button type="button" disabled={deciding || decideMutation.isLoading} onClick={() => { setDecidingRequestId(req.request_id); decideMutation.mutate({ requestId: req.request_id, status: 'approved' }) }} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
                          <Check className="h-4 w-4" aria-hidden />
                          {t('groups.approve')}
                        </button>
                        <button type="button" disabled={deciding || decideMutation.isLoading} onClick={() => { setDecidingRequestId(req.request_id); decideMutation.mutate({ requestId: req.request_id, status: 'rejected' }) }} className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
                          <X className="h-4 w-4" aria-hidden />
                          {t('groups.reject')}
                        </button>
                      </div>
                    ) : null}
                  </div>
                )
              })}
              {requestsQuery.data?.items.length === 0 ? <EmptyLine text={t('groups.emptyRequests')} /> : null}
            </div>
            {requestsQuery.data && requestsQuery.data.total > 0 ? (
              <Pager total={requestsQuery.data.total} page={requestPage} pageSize={requestPageSize} onChange={(p, s) => { setRequestPage(p); setRequestPageSize(s) }} />
            ) : null}
          </Panel>
        ) : null}
      </main>

      <EditGroupDialog open={editOpen} name={editName} description={editDescription} visibility={editVisibility} loading={updateMutation.isLoading} error={updateMutation.error} setName={setEditName} setDescription={setEditDescription} setVisibility={setEditVisibility} onClose={() => setEditOpen(false)} onSubmit={() => updateMutation.mutate()} />
      <JoinDialog open={joinOpen} message={joinMessage} loading={joinMutation.isLoading} disabled={hasPendingJoinRequest} error={joinMutation.error} setMessage={setJoinMessage} onClose={() => setJoinOpen(false)} onSubmit={() => { if (!hasPendingJoinRequest) joinMutation.mutate() }} />
    </div>
  )
}

function InfoCell({ icon, label, value }: { icon?: React.ReactNode; label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-2xl bg-white/80 px-3 py-3 ring-1 ring-[#E5E7EB]">
      <div className="flex items-center gap-1.5 text-xs text-[#9CA3AF]">{icon}{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-[#111827]" title={value}>{value}</div>
    </div>
  )
}

function SkillIcon({ title, iconUri, assetId, size = 'md' }: { title: string; iconUri?: string | null; assetId: string; size?: 'sm' | 'md' }) {
  const [failed, setFailed] = useState(false)
  const iconUrl = resolvePluginIconUrl(iconUri || '')
  const letter = (title || assetId || 'S').trim().charAt(0).toUpperCase()
  const sizeClass = size === 'sm' ? 'h-8 w-8 rounded-lg text-xs' : 'h-10 w-10 rounded-xl text-sm'
  if (iconUrl && !failed) {
    return <img src={iconUrl} alt="" aria-hidden draggable={false} onError={() => setFailed(true)} className={`${sizeClass} shrink-0 border border-[#F3F4F6] bg-white object-cover shadow-sm`} />
  }
  return <div aria-hidden className={`flex ${sizeClass} shrink-0 items-center justify-center bg-gradient-to-br from-[#EEF2FF] to-[#E0E7FF] font-semibold text-[#4F46E5] shadow-sm ring-1 ring-[#E5E7EB]`}>{letter}</div>
}

function UserAvatar({ name, userId, size = 'md' }: { name?: string | null; userId: string; size?: 'sm' | 'md' }) {
  const label = name || userId || 'U'
  const letter = label.trim().charAt(0).toUpperCase()
  const sizeClass = size === 'sm' ? 'h-8 w-8 text-xs' : 'h-10 w-10 text-sm'
  return <div aria-hidden className={`flex ${sizeClass} shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#F8FAFF] via-[#EEF2FF] to-[#EDE9FE] font-semibold text-[#4F46E5] shadow-sm ring-1 ring-[#E5E7EB]`}>{letter}</div>
}

function TabButton({ label, count, active, onClick }: { label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-4 py-2.5 text-[13px] font-normal leading-5 !text-[#191919] transition-colors hover:!text-[#191919] ${active ? 'bg-white shadow-[0_1px_2px_rgba(16,24,40,0.05)]' : 'hover:bg-white hover:shadow-[0_1px_2px_rgba(16,24,40,0.05)]'}`}
    >
      <span>{label}</span>
      {count ? <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-700">{count}</span> : null}
    </button>
  )
}

function TodoButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="rounded-lg bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700">{label}</button>
}

function Panel({ id, title, subtitle, action, badge, children }: { id?: string; title: string; subtitle: string; action?: React.ReactNode; badge?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 rounded-2xl border border-[#E5E7EB] bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-[#111827]">
            {title.includes('成员') || title.toLowerCase().includes('member') ? <Users className="h-4 w-4" aria-hidden /> : <Shield className="h-4 w-4" aria-hidden />}
            {title}
            {badge ? <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">{badge}</span> : null}
          </h2>
          <p className="mt-1 text-xs text-[#6B7280]">{subtitle}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function StatusBadge({ text, tone }: { text: string; tone: 'green' | 'amber' | 'blue' }) {
  const cls = tone === 'green'
    ? 'bg-emerald-50 text-emerald-700'
    : tone === 'blue'
      ? 'bg-blue-50 text-blue-700'
      : 'bg-amber-50 text-amber-700'
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{text}</span>
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-xl bg-[#F9FAFB] px-3 py-3 text-center text-sm text-[#9CA3AF]">{text}</div>
}

function Pager({ total, page, pageSize, onChange }: { total: number; page: number; pageSize: number; onChange: (page: number, pageSize: number) => void }) {
  return (
    <div className="mt-3 border-t border-[#F3F4F6] pt-3">
      <Pagination pager={{ total, currentPage: page, pageSize, pageSizeOptions: [...PAGE_SIZE_OPTIONS] }} loading={false} onPagerChange={onChange} />
    </div>
  )
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : error ? String(error) : ''
}

function EditGroupDialog(props: { open: boolean; name: string; description: string; visibility: GroupVisibility; loading: boolean; error: unknown; setName: (v: string) => void; setDescription: (v: string) => void; setVisibility: (v: GroupVisibility) => void; onClose: () => void; onSubmit: () => void }) {
  const { t } = useTranslation()
  return (
    <Dialog open={props.open} onClose={() => !props.loading && props.onClose()} fullWidth maxWidth="sm">
      <DialogTitle>{t('groups.edit')}</DialogTitle>
      <DialogContent>
        <FormFields name={props.name} description={props.description} setName={props.setName} setDescription={props.setDescription} error={errorText(props.error)} />
        <label className="mt-3 flex flex-col gap-1 text-sm text-[#374151]"><span>{t('groups.form.visibility')}</span><Select size="small" value={props.visibility} onChange={e => props.setVisibility(e.target.value as GroupVisibility)}><MenuItem value="private">{t('groups.visibility.private')}</MenuItem><MenuItem value="listed">{t('groups.visibility.listed')}</MenuItem></Select></label>
      </DialogContent>
      <DialogActions><DialogButtons loading={props.loading} disabled={!props.name.trim()} onClose={props.onClose} onSubmit={props.onSubmit} /></DialogActions>
    </Dialog>
  )
}


function JoinDialog(props: { open: boolean; message: string; loading: boolean; disabled: boolean; error: unknown; setMessage: (v: string) => void; onClose: () => void; onSubmit: () => void }) {
  const { t } = useTranslation()
  return (
    <Dialog open={props.open} onClose={() => !props.loading && props.onClose()} fullWidth maxWidth="sm">
      <DialogTitle>{t('groups.applyJoin')}</DialogTitle>
      <DialogContent>
        <div className="mt-2 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm text-[#374151]"><span>{t('groups.joinMessage')}</span><textarea value={props.message} onChange={e => props.setMessage(e.target.value)} rows={4} className="rounded-lg border border-[#E5E7EB] px-3 py-2 text-sm outline-none focus:border-[#4F46E5] focus:ring-2 focus:ring-[#E0E7FF]" /></label>
          {errorText(props.error) ? <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errorText(props.error)}</div> : null}
        </div>
      </DialogContent>
      <DialogActions><DialogButtons loading={props.loading} disabled={props.disabled} onClose={props.onClose} onSubmit={props.onSubmit} /></DialogActions>
    </Dialog>
  )
}

function FormFields(props: { name: string; description: string; setName: (v: string) => void; setDescription: (v: string) => void; error: string }) {
  const { t } = useTranslation()
  return (
    <div className="mt-2 flex flex-col gap-3">
      <label className="flex flex-col gap-1 text-sm text-[#374151]"><span>{t('groups.form.name')}</span><input value={props.name} onChange={e => props.setName(e.target.value)} maxLength={128} className="h-10 rounded-lg border border-[#E5E7EB] px-3 text-sm outline-none focus:border-[#4F46E5] focus:ring-2 focus:ring-[#E0E7FF]" /></label>
      <label className="flex flex-col gap-1 text-sm text-[#374151]"><span>{t('groups.form.description')}</span><textarea value={props.description} onChange={e => props.setDescription(e.target.value)} rows={4} maxLength={4096} className="rounded-lg border border-[#E5E7EB] px-3 py-2 text-sm outline-none focus:border-[#4F46E5] focus:ring-2 focus:ring-[#E0E7FF]" /></label>
      {props.error ? <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{props.error}</div> : null}
    </div>
  )
}

function DialogButtons(props: { loading: boolean; disabled: boolean; onClose: () => void; onSubmit: () => void }) {
  const { t } = useTranslation()
  return (
    <>
      <button type="button" disabled={props.loading} onClick={props.onClose} className="rounded-lg px-3 py-2 text-sm text-[#6B7280] hover:bg-slate-50">{t('groups.cancel')}</button>
      <button type="button" disabled={props.disabled || props.loading} onClick={props.onSubmit} className="rounded-lg bg-[#1E54F9] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">{props.loading ? t('groups.saving') : t('groups.confirm')}</button>
    </>
  )
}
