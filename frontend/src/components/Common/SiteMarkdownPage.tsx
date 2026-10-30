// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { PluginMarkdown } from '@/components/Common/PluginMarkdown'
import { SiteFooter } from '@/components/Common/SiteFooter'

type SiteMarkdownPageProps = {
  url: string
  pageTitleKey: string
  backHomeKey: string
  loadingKey: string
  loadErrorKey: string
}

/** 站点说明页：拉取 Markdown 并渲染。空正文或非 2xx 显示加载失败。 */
export function SiteMarkdownPage({
  url,
  pageTitleKey,
  backHomeKey,
  loadingKey,
  loadErrorKey,
}: SiteMarkdownPageProps) {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    document.title = t(pageTitleKey)
    return () => {
      document.title = t('appHeader.brand')
    }
  }, [t, pageTitleKey])

  useEffect(() => {
    let cancelled = false
    setText(null)
    setErr(null)
    fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(String(res.status))
        return res.text()
      })
      .then(body => {
        if (cancelled) return
        if (!body.trim()) {
          setErr('failed')
          return
        }
        setText(body)
      })
      .catch(() => {
        if (!cancelled) setErr('failed')
      })
    return () => {
      cancelled = true
    }
  }, [url])

  return (
    <div className="flex min-h-dvh flex-col bg-slate-50">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <Link to="/" className="text-sm font-medium text-blue-600 underline-offset-2 hover:underline">
          {t(backHomeKey)}
        </Link>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        {err && <p className="text-sm text-red-600">{t(loadErrorKey)}</p>}
        {text === null && !err && <p className="text-sm text-slate-600">{t(loadingKey)}</p>}
        {text !== null && (
          <article className="rounded-lg border border-slate-200/80 bg-white px-5 py-6 shadow-sm">
            <PluginMarkdown source={text} className="prose prose-slate max-w-none prose-headings:scroll-mt-20" />
          </article>
        )}
      </main>
      <SiteFooter />
    </div>
  )
}
