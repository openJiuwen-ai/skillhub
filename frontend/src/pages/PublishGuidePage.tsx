// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useTranslation } from 'react-i18next'
import { SiteMarkdownPage } from '@/components/Common/SiteMarkdownPage'
import { getPublishGuideViewUrl } from '@/utils/publishGuideUrl'

export default function PublishGuidePage() {
  const { i18n } = useTranslation()
  const lang = (i18n.resolvedLanguage ?? i18n.language ?? '').startsWith('en') ? 'en' : 'zh'
  return (
    <SiteMarkdownPage
      url={getPublishGuideViewUrl(lang)}
      pageTitleKey="publishGuide.pageTitle"
      backHomeKey="publishGuide.backHome"
      loadingKey="publishGuide.loading"
      loadErrorKey="publishGuide.loadError"
    />
  )
}
