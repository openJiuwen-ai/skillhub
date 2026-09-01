// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'
import { usePublishDrawer } from '@/contexts/PublishDrawer'
import { getPrimarySkillPluginType, parseMarketTabType } from '@/utils/pluginType'

/**
 * `/profile/publish` 旧路由兼容：发布流已统一为右侧抽屉。
 * 访问该路由时直接打开抽屉并把地址改写回 `/profile?tab=skill`，
 * 未登录则跳转登录页并保留回跳。
 */
export default function PublishPluginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { isAuthenticated } = useGitCodeAuth()
  const { openPublish } = usePublishDrawer()
  const publishType = useMemo(() => {
    const kind = new URLSearchParams(location.search).get('kind')
    return parseMarketTabType(kind) ?? getPrimarySkillPluginType()
  }, [location.search])

  useEffect(() => {
    if (!isAuthenticated) {
      setPostLoginRedirect(`/profile/publish?kind=${publishType}`)
      navigate('/login', { replace: true })
      return
    }
    openPublish(publishType)
    navigate('/profile?tab=skill', { replace: true })
  }, [isAuthenticated, navigate, openPublish, publishType])

  return null
}
