// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

/** 全站页脚：文档栏放上传指南与隐私声明，社区栏放 openJiuwen 官网。 */
export function SiteFooter() {
  const { t } = useTranslation()

  return (
    <footer
      className="shrink-0 border-t border-slate-800/90 bg-[#0f1419] pb-[max(0.75rem,env(safe-area-inset-bottom,0px))] pt-10 text-slate-300"
      role="contentinfo"
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-10 px-6 sm:px-8 md:flex-row md:justify-between md:gap-12">
        <nav aria-label={t('siteFooter.columnDocs')} className="min-w-0 flex-1">
          <h3 className="mb-3 text-sm font-semibold tracking-wide text-white">{t('siteFooter.columnDocs')}</h3>
          <ul className="flex flex-col gap-2.5">
            <li>
              <Link
                to="/publish-guide"
                className="text-sm text-slate-400 transition-colors hover:text-slate-200 hover:underline"
              >
                {t('publishGuide.button')}
              </Link>
            </li>
            <li>
              <Link
                to="/privacy-statement"
                className="text-sm text-slate-400 transition-colors hover:text-slate-200 hover:underline"
              >
                {t('privacyPolicy.button')}
              </Link>
            </li>
          </ul>
        </nav>
        <nav aria-label={t('siteFooter.columnCommunity')} className="min-w-0 flex-1">
          <h3 className="mb-3 text-sm font-semibold tracking-wide text-white">{t('siteFooter.columnCommunity')}</h3>
          <ul className="flex flex-col gap-2.5">
            <li>
              <a
                href="https://www.openjiuwen.com"
                target="_blank"
                rel="noreferrer noopener"
                className="text-sm text-slate-400 transition-colors hover:text-slate-200 hover:underline"
              >
                {t('siteFooter.openJiuwen')}
              </a>
            </li>
          </ul>
        </nav>
        <nav aria-label={t('siteFooter.columnMore')} className="min-w-0 flex-1">
          <h3 className="mb-3 text-sm font-semibold tracking-wide text-white">{t('siteFooter.columnMore')}</h3>
        </nav>
      </div>
      <div className="mx-auto mt-10 max-w-5xl border-t border-slate-800/80 px-6 pt-6 sm:px-8">
        <p className="text-center text-xs text-slate-500">
          {t('siteFooter.copyright', { year: new Date().getFullYear() })}
        </p>
      </div>
    </footer>
  )
}
