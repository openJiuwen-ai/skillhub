// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import React from 'react'
import { useEffect, useLayoutEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from 'react-query'
import { BrowserRouter, useLocation } from 'react-router-dom'
import { setApiBaseUrl } from '@/api'
import { GitCodeAuthProvider } from '@/auth/GitCodeAuthContext'
import { ENV_CONFIG } from '@/config/environment'
import './i18n'
import App from './App'
import './index.css'

setApiBaseUrl(ENV_CONFIG.API_BASE_URL)

/** 默认不做后台自动重拉：避免列表/详情周期性或切窗时整页重绘跳动；需要最新数据请用各页「刷新」或会触发 invalidate 的写操作。 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Number.POSITIVE_INFINITY,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
})

/**
 * 按路由记录滚动位置：
 * - 进入新页面（如点开卡片进入详情）回到顶部；
 * - 返回上一页（详情页 navigate(-1) 触发 POP）时恢复之前的滚动位置。
 * BrowserRouter 默认让浏览器保留滚动位置，这里手动接管以避免详情页停在底部、返回时跳顶等问题。
 */
const MAX_SCROLL_ENTRIES = 100
const scrollPositions = new Map<string, number>()

function setScrollPosition(key: string, value: number) {
  // 超出阈值淘汰最旧条目（Map 按插入顺序，简易 LRU），避免动态路由下无限增长
  if (scrollPositions.size >= MAX_SCROLL_ENTRIES && !scrollPositions.has(key)) {
    const firstKey = scrollPositions.keys().next().value
    if (firstKey !== undefined) scrollPositions.delete(firstKey)
  }
  scrollPositions.set(key, value)
}

function ScrollRestoration() {
  const location = useLocation()
  const key = location.key ?? location.pathname

  // 禁用浏览器原生滚动恢复，避免与下方手动恢复竞争（SPA 标准做法）
  useEffect(() => {
    const original = history.scrollRestoration
    history.scrollRestoration = 'manual'
    return () => {
      history.scrollRestoration = original
    }
  }, [])

  // 切换路由时恢复（新页面无记录则回顶部）
  useLayoutEffect(() => {
    const saved = scrollPositions.get(key) ?? 0
    // 立即设置，避免先渲染到顶部再跳动的闪烁
    window.scrollTo(0, saved)
    // 异步内容（图片/缓存数据）可能进一步撑高页面，下一帧再校正一次
    const id = window.requestAnimationFrame(() => window.scrollTo(0, saved))
    return () => window.cancelAnimationFrame(id)
  }, [key])

  // 持续记录当前页的滚动位置
  useEffect(() => {
    let raf = 0
    const record = () => {
      if (raf) return
      raf = window.requestAnimationFrame(() => {
        raf = 0
        setScrollPosition(key, window.scrollY)
      })
    }
    window.addEventListener('scroll', record, { passive: true })
    record()
    return () => {
      window.removeEventListener('scroll', record)
      if (raf) window.cancelAnimationFrame(raf)
    }
  }, [key])

  return null
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '') || undefined}>
        <ScrollRestoration />
        <GitCodeAuthProvider>
          <App />
        </GitCodeAuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
