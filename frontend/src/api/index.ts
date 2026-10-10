// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

export { API_CONFIG, setApiBaseUrl, API_ENDPOINTS } from './config'
export { getApiClient } from './client'
export {
  deletePluginAllVersions,
  deletePluginVersion,
  getPluginArtifactDownload,
  getPluginVersionDetail,
  getPlugins,
  MarketplaceApiError,
  publishPlugin,
} from './plugin'
export type {
  MarketplacePluginItem,
  MarketplacePluginListData,
  MarketplacePluginListRequest,
  MarketplacePluginListResponse,
  MarketplacePluginOrderBy,
  PluginDownloadData,
  PluginDownloadResponse,
  PluginVersionDeleteResult,
  PluginPublishResultData,
  PluginVersionDetailData,
} from './plugin'
export { usePluginListQuery, usePluginGetMarket } from './usePluginGetMarket'
export {
  createGroup,
  createJoinRequest,
  decideJoinRequest,
  deleteGroup,
  getGroup,
  grantSkillToGroup,
  listGroupGrants,
  listGroupMembers,
  listJoinRequests,
  listMyGroups,
  removeGroupMember,
  revokeSkillFromGroup,
  updateGroup,
  upsertGroupMember,
} from './groups'
export type {
  GroupItem,
  GroupJoinRequestItem,
  GroupJoinRequestListData,
  GroupListData,
  GroupMemberItem,
  GroupMemberListData,
  GroupMemberRole,
  GroupSkillGrantItem,
  GroupSkillGrantListData,
  JoinRequestStatus,
} from './groups'
export {
  exchangeOAuthSession,
  fetchOAuthMe,
  getOAuthStartUrl,
  OAUTH_ACTIVE_PROVIDER_KEY,
  OAUTH_PENDING_KEY,
} from './auth'
export type { OAuthSessionData } from './auth'
export { GithubWatchError, starAllRepos } from './githubWatch'
