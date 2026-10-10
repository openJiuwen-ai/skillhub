// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import { getApiClient } from './client'
import { API_ENDPOINTS } from './config'
import { MarketplaceApiError, type MarketplacePluginItem } from './plugin'

export type GroupMemberRole = 'owner' | 'member'
export type GroupVisibility = 'private' | 'listed'
export type JoinRequestStatus = 'pending' | 'approved' | 'rejected'
export type GrantStatus = 'pending' | 'active' | 'rejected' | 'revoked'

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface GroupItem {
  group_id: string
  name: string
  description?: string | null
  owner_id: string
  owner_name?: string | null
  visibility: GroupVisibility
  member_count: number
  skill_count: number
  viewer_role?: GroupMemberRole | null
  viewer_can_manage?: boolean
  join_request_status?: JoinRequestStatus | null
  create_time: number
  update_time: number
}

export interface GroupListData {
  page: number
  page_size: number
  total: number
  items: GroupItem[]
}

export interface GroupMemberItem {
  user_id: string
  user_name?: string | null
  role: GroupMemberRole
  create_time: number
  update_time: number
}

export interface GroupMemberListData {
  page: number
  page_size: number
  total: number
  items: GroupMemberItem[]
}

export interface GroupJoinRequestItem {
  request_id: string
  group_id: string
  user_id: string
  user_name?: string | null
  message?: string | null
  status: JoinRequestStatus
  create_time: number
  update_time: number
}

export interface GroupJoinRequestListData {
  page: number
  page_size: number
  total: number
  items: GroupJoinRequestItem[]
}

export interface GroupSkillGrantItem {
  group_id: string
  asset_id: string
  skill_name?: string | null
  skill_display_name?: string | null
  icon_uri?: string | null
  latest_version?: string | null
  public_latest_version?: string | null
  status: GrantStatus
  viewer_access_source?: 'admin' | 'owner' | 'group' | 'public' | null
  create_time: number
  update_time: number
}

export interface GroupSkillGrantListData {
  page: number
  page_size: number
  total: number
  items: GroupSkillGrantItem[]
}

export interface MyGroupSkillItem {
  group_id: string
  group_name: string
  skill: MarketplacePluginItem
  viewer_access_source?: 'admin' | 'owner' | 'group' | 'public' | null
}

export interface MyGroupSkillListData {
  page: number
  page_size: number
  total: number
  items: MyGroupSkillItem[]
}


export interface GrantableSkillItem {
  asset_id: string
  name: string
  display_name?: string | null
  short_desc?: string | null
  publisher_id: string
  publisher_name: string
  plugin_type?: string | null
  latest_version?: string | null
  group_grant_status?: GrantStatus | null
  grantable: boolean
  not_grantable_reason?: string | null
}

export interface GrantableSkillListData {
  page: number
  page_size: number
  total: number
  items: GrantableSkillItem[]
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

function assertOk<T>(response: ApiResponse<T>, fallback: string): T {
  if (response == null || typeof response !== 'object') {
    throw new MarketplaceApiError(`${fallback}: invalid response`)
  }
  if (response.code !== 200) {
    throw new MarketplaceApiError(response.message || fallback, response.code)
  }
  return response.data
}

export async function listMyGroups(request: { page?: number; page_size?: number; keyword?: string; role?: GroupMemberRole; sort?: string } = {}): Promise<GroupListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GroupListData>>(API_ENDPOINTS.GROUPS.MY, {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20, keyword: request.keyword || undefined, role: request.role || undefined, sort: request.sort || undefined },
    })
    return assertOk(data, 'Failed to load groups')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load groups'))
  }
}

export async function discoverGroups(request: { page?: number; page_size?: number; keyword?: string; filter_by?: 'joined' | 'pending' | 'available'; sort?: string } = {}): Promise<GroupListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GroupListData>>(API_ENDPOINTS.GROUPS.DISCOVER, {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20, keyword: request.keyword || undefined, filter_by: request.filter_by || undefined, sort: request.sort || undefined },
    })
    return assertOk(data, 'Failed to discover groups')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to discover groups'))
  }
}

export async function createGroup(body: { name: string; description?: string | null; visibility?: GroupVisibility }): Promise<GroupItem> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<GroupItem>>(API_ENDPOINTS.GROUPS.ROOT, body)
    return assertOk(data, 'Failed to create group')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to create group'))
  }
}

export async function getGroup(groupId: string): Promise<GroupItem> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GroupItem>>(API_ENDPOINTS.GROUPS.detail(groupId))
    return assertOk(data, 'Failed to load group')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load group'))
  }
}

export async function updateGroup(groupId: string, body: { name?: string; description?: string | null; visibility?: GroupVisibility }): Promise<GroupItem> {
  const client = getApiClient()
  try {
    const { data } = await client.patch<ApiResponse<GroupItem>>(API_ENDPOINTS.GROUPS.detail(groupId), body)
    return assertOk(data, 'Failed to update group')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to update group'))
  }
}

export async function deleteGroup(groupId: string): Promise<void> {
  const client = getApiClient()
  try {
    const { data } = await client.delete<ApiResponse<{ group_id: string }>>(API_ENDPOINTS.GROUPS.detail(groupId))
    assertOk(data, 'Failed to delete group')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to delete group'))
  }
}

export async function listGroupMembers(groupId: string, request: { page?: number; page_size?: number } = {}): Promise<GroupMemberListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GroupMemberListData>>(API_ENDPOINTS.GROUPS.members(groupId), {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20 },
    })
    return assertOk(data, 'Failed to load members')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load members'))
  }
}

export async function upsertGroupMember(groupId: string, body: { user_id: string; user_name?: string | null; role: 'member' }): Promise<GroupMemberItem> {
  const client = getApiClient()
  try {
    const { data } = await client.put<ApiResponse<GroupMemberItem>>(API_ENDPOINTS.GROUPS.members(groupId), body)
    return assertOk(data, 'Failed to save member')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to save member'))
  }
}

export async function removeGroupMember(groupId: string, userId: string): Promise<void> {
  const client = getApiClient()
  try {
    const { data } = await client.delete<ApiResponse<{ group_id: string; user_id: string }>>(API_ENDPOINTS.GROUPS.member(groupId, userId))
    assertOk(data, 'Failed to remove member')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to remove member'))
  }
}

export async function createJoinRequest(groupId: string, body: { message?: string | null }): Promise<GroupJoinRequestItem> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<GroupJoinRequestItem>>(API_ENDPOINTS.GROUPS.joinRequests(groupId), body)
    return assertOk(data, 'Failed to submit join request')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to submit join request'))
  }
}

export async function listJoinRequests(
  groupId: string,
  request: { page?: number; page_size?: number; status?: JoinRequestStatus | 'all' } = {},
): Promise<GroupJoinRequestListData> {
  const client = getApiClient()
  try {
    const status = request.status && request.status !== 'all' ? request.status : undefined
    const { data } = await client.get<ApiResponse<GroupJoinRequestListData>>(API_ENDPOINTS.GROUPS.joinRequests(groupId), {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20, status },
    })
    return assertOk(data, 'Failed to load join requests')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load join requests'))
  }
}

export async function decideJoinRequest(groupId: string, requestId: string, status: 'approved' | 'rejected'): Promise<GroupJoinRequestItem> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<GroupJoinRequestItem>>(API_ENDPOINTS.GROUPS.joinRequestDecision(groupId, requestId), { status })
    return assertOk(data, 'Failed to decide join request')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to decide join request'))
  }
}

export async function listGroupGrants(groupId: string, request: { page?: number; page_size?: number; status?: GrantStatus | 'all' } = {}): Promise<GroupSkillGrantListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GroupSkillGrantListData>>(API_ENDPOINTS.GROUPS.grants(groupId), {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20, status: request.status && request.status !== 'all' ? request.status : undefined },
    })
    return assertOk(data, 'Failed to load grants')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load grants'))
  }
}


export async function listMyGroupSkills(request: { page?: number; page_size?: number; keyword?: string } = {}): Promise<MyGroupSkillListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<MyGroupSkillListData>>(API_ENDPOINTS.GROUPS.MY_SKILLS, {
      params: { page: request.page ?? 1, page_size: request.page_size ?? 20, keyword: request.keyword || undefined },
    })
    return assertOk(data, 'Failed to load group skills')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to load group skills'))
  }
}

export async function searchGrantableSkills(request: { keyword?: string; page?: number; page_size?: number; group_id?: string } = {}): Promise<GrantableSkillListData> {
  const client = getApiClient()
  try {
    const { data } = await client.get<ApiResponse<GrantableSkillListData>>(API_ENDPOINTS.GROUPS.GRANTABLE_SKILLS, {
      params: { keyword: request.keyword || undefined, group_id: request.group_id || undefined, page: request.page ?? 1, page_size: request.page_size ?? 20 },
    })
    return assertOk(data, 'Failed to search skills')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to search skills'))
  }
}

export async function grantSkillToGroup(groupId: string, assetId: string): Promise<GroupSkillGrantItem> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<GroupSkillGrantItem>>(API_ENDPOINTS.GROUPS.grants(groupId), { asset_id: assetId })
    return assertOk(data, 'Failed to grant skill')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to grant skill'))
  }
}

export async function decideGroupSkillGrant(groupId: string, assetId: string, status: 'active' | 'rejected'): Promise<GroupSkillGrantItem> {
  const client = getApiClient()
  try {
    const { data } = await client.post<ApiResponse<GroupSkillGrantItem>>(API_ENDPOINTS.GROUPS.grantDecision(groupId, assetId), { status })
    return assertOk(data, 'Failed to decide grant')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to decide grant'))
  }
}

export async function revokeSkillFromGroup(groupId: string, assetId: string): Promise<void> {
  const client = getApiClient()
  try {
    const { data } = await client.delete<ApiResponse<{ group_id: string; asset_id: string }>>(API_ENDPOINTS.GROUPS.grant(groupId, assetId))
    assertOk(data, 'Failed to revoke skill')
  } catch (e) {
    if (e instanceof MarketplaceApiError) throw e
    throw new Error(apiErrorMessage(e, 'Failed to revoke skill'))
  }
}
