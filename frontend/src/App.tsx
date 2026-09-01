// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { Routes, Route, Outlet } from 'react-router-dom'
import { Suspense } from 'react'
import LoginPage from '@/pages/LoginPage'
import MyPluginDetailPage from '@/pages/MyPluginDetailPage'
import MyProfilePage from '@/pages/MyProfilePage'
import PluginMarketPage from '@/pages/PluginMarketPage'
import SkillDetailPage from '@/pages/SkillDetailPage'
import PublishPluginPage from '@/pages/PublishPluginPage'
import SkillReviewDetailPage from '@/pages/SkillReviewDetailPage'
import { PublishDrawerProvider, usePublishDrawer } from '@/contexts/PublishDrawer'
import { PublishDrawer } from '@/components/Publish/PublishDrawer'
import { WatchOpenJiuwenRepos } from '@/components/Common/WatchOpenJiuwenRepos'
import { SiteFooter } from '@/components/Common/SiteFooter'
import PrivacyStatementPage from '@/pages/PrivacyStatementPage'
import GroupsPage from '@/pages/GroupsPage'
import GroupDetailPage from '@/pages/GroupDetailPage'

/** 消费 context 并把抽屉挂在全局，避免 context 文件持有业务组件引用。 */
function GlobalPublishDrawer() {
  const { open, type, closePublish } = usePublishDrawer()
  return <PublishDrawer open={open} type={type} onClose={closePublish} />
}

/** 主应用壳：页面随内容增高，滚动到底部才可看到页脚（非视口吸附固定）。 */
function MainAppShell() {
  return (
    <div className="flex min-h-dvh flex-col">
      <Outlet />
      <SiteFooter />
    </div>
  )
}

function App() {
  return (
    <Suspense fallback={<div className="flex min-h-dvh items-center justify-center">Loading...</div>}>
      <PublishDrawerProvider>
        <>
          <Routes>
            <Route path="/privacy-statement" element={<PrivacyStatementPage />} />
            <Route element={<MainAppShell />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/profile/plugins/:assetId/versions/:version/review" element={<SkillReviewDetailPage />} />
              <Route path="/profile/plugins/:assetId" element={<MyPluginDetailPage />} />
              <Route path="/profile/publish" element={<PublishPluginPage />} />
              <Route path="/profile" element={<MyProfilePage />} />
              <Route path="/groups" element={<GroupsPage />} />
              <Route path="/groups/:groupId" element={<GroupDetailPage />} />
              <Route path="/skills/:assetId" element={<SkillDetailPage />} />
              <Route path="/assets/:assetId" element={<SkillDetailPage />} />
              <Route path="/" element={<PluginMarketPage />} />
            </Route>
          </Routes>
          <GlobalPublishDrawer />
          <WatchOpenJiuwenRepos />
        </>
      </PublishDrawerProvider>
    </Suspense>
  )
}

export default App
