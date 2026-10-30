// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { siteApiUrl } from '@/utils/siteMarkdownUrl'

/** 拉取上传指南 Markdown。英文界面带 `lang=en`。 */
export function getPublishGuideViewUrl(lang: 'zh' | 'en'): string {
  const query = lang === 'en' ? '?lang=en' : ''
  return siteApiUrl(`/site/publish-guide${query}`)
}
