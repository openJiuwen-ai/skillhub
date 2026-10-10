// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { AnchorHTMLAttributes } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'

type PluginMarkdownProps = {
  /** Markdown 源码；非字符串会转为字符串 */
  source: string | null | undefined
  className?: string
  /** 详情页阅读版式；默认保留既有紧凑版式。 */
  variant?: 'detail'
  /** 是否启用 Mermaid 图表渲染（默认 false） */
  mermaid?: boolean
}

type MarkdownLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { node?: unknown }
type MarkdownImgProps = React.ImgHTMLAttributes<HTMLImageElement> & { node?: unknown }

/** 仅允许 http(s)、mailto、站内根路径 /，屏蔽 javascript:/data:/vbscript: 与 // 协议相对 URL */
function sanitizeMarkdownHref(href: string | undefined): string | undefined {
  if (href == null || typeof href !== 'string') return undefined
  const t = href.trim()
  if (!t) return undefined
  const lower = t.toLowerCase()
  if (lower.startsWith('javascript:') || lower.startsWith('data:') || lower.startsWith('vbscript:')) {
    return undefined
  }
  if (lower.startsWith('http://') || lower.startsWith('https://')) return t
  if (lower.startsWith('mailto:')) return t
  if (t.startsWith('/') && !t.startsWith('//')) return t
  return undefined
}

/** Block remote images in marketplace markdown (privacy / tracking). */
function MarkdownImage({ src, alt, ...rest }: MarkdownImgProps) {
  if (src == null || typeof src !== 'string') {
    return null
  }
  const t = src.trim()
  if (!t) return null
  const lower = t.toLowerCase()
  if (lower.startsWith('http://') || lower.startsWith('https://') || lower.startsWith('//')) {
    return (
      <span className="text-xs text-gray-500 italic" title={t}>
        [image blocked: {alt || 'external'}]
      </span>
    )
  }
  if (t.startsWith('/') && !t.startsWith('//')) {
    return <img {...rest} src={t} alt={alt ?? ''} className="my-2 max-w-full rounded" loading="lazy" />
  }
  return null
}

function MarkdownAnchor({ href, children, ...rest }: MarkdownLinkProps) {
  const safe = sanitizeMarkdownHref(href)
  if (!safe) {
    return <span className="text-gray-800">{children}</span>
  }
  const external = /^https?:\/\//i.test(safe)
  return (
    <a
      {...rest}
      href={safe}
      className="text-blue-600 underline underline-offset-2"
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
    >
      {children}
    </a>
  )
}

function decodeEscapedMarkdown(input: string): string {
  // 后端偶发返回多层 JSON 字符串字面量（包含 \"、\\r\\n 等），做有限次解码
  let text = input
  for (let i = 0; i < 3; i += 1) {
    const trimmed = text.trim()
    if (!(trimmed.startsWith('"') && trimmed.endsWith('"'))) break
    try {
      const parsed = JSON.parse(trimmed)
      if (typeof parsed !== 'string') break
      text = parsed
    } catch {
      break
    }
  }

  // 回退：将常见的转义换行符还原为真实换行
  return text.replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n').replace(/\\r/g, '\n')
}

let _mermaidIdCounter = 0

function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState(false)
  const idRef = useRef(`mermaid-${++_mermaidIdCounter}`)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const m = (await import('mermaid')).default
        m.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' })
        const { svg: rendered } = await m.render(idRef.current, code)
        if (!cancelled) setSvg(rendered)
      } catch {
        if (!cancelled) setError(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [code])

  if (error) {
    return <pre className="overflow-x-auto rounded bg-slate-100 p-3 text-xs text-slate-800">{code}</pre>
  }
  if (!svg) {
    return <div className="my-3 h-16 animate-pulse rounded bg-slate-100" />
  }
  return (
    <div
      className="my-3 overflow-x-auto"
      // mermaid 生成的 SVG，非用户 HTML
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}

/**
 * 插件市场详情等场景使用的 Markdown 渲染；使用显式 `source` 避免 JSX 子节点空白折叠问题。
 */
export function PluginMarkdown({ source, className, variant, mermaid: enableMermaid }: PluginMarkdownProps) {
  const raw = source == null ? '' : typeof source === 'string' ? source : String(source)
  const text = decodeEscapedMarkdown(raw)
  const detailVariant = variant === 'detail'

  const preRenderer = ({ children, ...rest }: { children?: ReactNode }) => {
    if (enableMermaid) {
      const childArr = Array.isArray(children) ? children : [children]
      const first = childArr[0] as { props?: { className?: string; children?: ReactNode } } | undefined
      if (typeof first?.props?.className === 'string' && first.props.className.includes('language-mermaid')) {
        const code = String(first.props.children ?? '').replace(/\n$/, '')
        return <MermaidBlock code={code} />
      }
    }
    return detailVariant
      ? <pre className="my-4 overflow-x-auto rounded-[8px] border border-[#EEEEEE] bg-[#FAFAFA] p-4 text-[13px] leading-6 text-[#404040]" {...rest}>{children}</pre>
      : <pre className="overflow-x-auto rounded-md bg-slate-100 p-3 text-slate-800 my-3" {...rest}>{children}</pre>
  }

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={detailVariant ? {
          h1: props => <h1 className="my-5 text-[22px] font-semibold leading-8 text-[#191919] first:mt-0" {...props} />,
          h2: props => <h2 className="my-5 text-[18px] font-semibold leading-7 text-[#191919] first:mt-0" {...props} />,
          h3: props => <h3 className="my-4 text-[16px] font-semibold leading-6 text-[#262626] first:mt-0" {...props} />,
          p: props => <p className="my-3 text-[14px] leading-[24px] text-[#4B5563] first:mt-0 last:mb-0" {...props} />,
          ul: props => <ul className="my-3 list-disc space-y-1.5 pl-5" {...props} />,
          ol: props => <ol className="my-3 list-decimal space-y-1.5 pl-5" {...props} />,
          li: props => <li className="text-[14px] leading-[24px] text-[#4B5563]" {...props} />,
          code: props => <code className="rounded-[3px] bg-[#F5F5F5] px-1.5 py-0.5 text-[0.85em] text-[#404040]" {...props} />,
          blockquote: props => <blockquote className="my-4 border-l-2 border-[#B9C5FF] bg-[#F8F9FF] px-4 py-2 text-[#5F6475]" {...props} />,
          table: props => <div className="my-4 overflow-x-auto rounded-[8px] border border-[#EDEDED]"><table className="w-full border-collapse text-left text-[13px]" {...props} /></div>,
          th: props => <th className="border-b border-[#EDEDED] bg-[#FAFAFA] px-3 py-2 font-semibold text-[#404040]" {...props} />,
          td: props => <td className="border-b border-[#F0F0F0] px-3 py-2 align-top leading-5 text-[#595959] last:border-b-0" {...props} />,
          pre: preRenderer as never,
          a: props => <MarkdownAnchor {...props} />,
          img: props => <MarkdownImage {...props} />,
        } : {
          h1: props => <h1 className="text-xl font-bold leading-7 text-gray-900 my-3 first:mt-0" {...props} />,
          h2: props => <h2 className="text-lg font-semibold leading-7 text-gray-900 my-3 first:mt-0" {...props} />,
          h3: props => <h3 className="text-base font-semibold leading-6 text-gray-900 my-2.5 first:mt-0" {...props} />,
          p: props => <p className="text-sm leading-6 text-gray-800 my-2 first:mt-0 last:mb-0" {...props} />,
          ul: props => <ul className="list-disc pl-5 my-2 space-y-1" {...props} />,
          ol: props => <ol className="list-decimal pl-5 my-2 space-y-1" {...props} />,
          li: props => <li className="text-sm leading-6 text-gray-800" {...props} />,
          code: props => <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.85em] text-slate-900" {...props} />,
          pre: preRenderer as never,
          a: props => <MarkdownAnchor {...props} />,
          img: props => <MarkdownImage {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
