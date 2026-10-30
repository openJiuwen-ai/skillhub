// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { siteApiUrl } from '@/utils/siteMarkdownUrl'

/** 拉取隐私声明 Markdown 的接口 URL（供 fetch 使用）。 */
export function getPrivacyStatementViewUrl(): string {
  return siteApiUrl('/site/privacy-statement')
}

/**
 * 站内「隐私声明」页路径（与 Vite `base` / Router `basename` 一致），用于新标签打开已渲染页面。
 * 例：`base=/hub/` → `/hub/privacy-statement`；根部署 → `/privacy-statement`。
 */
export function getPrivacyStatementPagePath(): string {
  const raw = (import.meta.env.BASE_URL || '/').trim()
  const basePath = raw.replace(/\/+$/, '')
  const suffix = '/privacy-statement'
  if (!basePath) return suffix
  return `${basePath}${suffix}`.replace(/([^:]\/)\/+/g, '$1/')
}
