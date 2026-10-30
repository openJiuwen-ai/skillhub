// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { SiteMarkdownPage } from '@/components/Common/SiteMarkdownPage'
import { getPrivacyStatementViewUrl } from '@/utils/privacyStatementUrl'

export default function PrivacyStatementPage() {
  return (
    <SiteMarkdownPage
      url={getPrivacyStatementViewUrl()}
      pageTitleKey="privacyPolicy.pageTitle"
      backHomeKey="privacyPolicy.backHome"
      loadingKey="privacyPolicy.loading"
      loadErrorKey="privacyPolicy.loadError"
    />
  )
}
