// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Star, Loader2, ChevronLeft, ChevronRight, Code2, ChevronUp } from 'lucide-react'
import { getStarStatus, starAllRepos, GithubWatchError } from '@/api/githubWatch'
import { getSiteConfig } from '@/api/playground'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { setPostLoginRedirect } from '@/auth/postLoginRedirect'

// 我们的 GitHub 组织地址（"代码"按钮跳转目标）
const GITHUB_ORG_URL = 'https://github.com/openJiuwen-ai'

// 拖拽移动阈值（px）：mousedown 后移动超过此距离才视为拖拽，否则视为点击
const DRAG_THRESHOLD = 3
// 浮窗距屏幕边缘的间距（贴边）
const EDGE_MARGIN = 0

// 浮窗尺寸
const WIDGET_WIDTH = 52
const BUTTON_HEIGHT = 52
const BUTTON_COUNT = 3
const WIDGET_HEIGHT = BUTTON_HEIGHT * BUTTON_COUNT // 156px
// 弹窗尺寸和间距
const DIALOG_WIDTH = 320
const DIALOG_GAP = 12

/** 浮窗位置状态（照搬 openjiuwen 的 x/y/side 模式）
 * - x: 拖拽中存绝对 left 值；吸附后存 EDGE_MARGIN（给 CSS right/left 常量用）
 * - side: 决定 CSS 用 right 还是 left 定位
 */
interface WidgetPos {
  x: number
  y: number   // 距离屏幕顶部的像素
  side: 'left' | 'right'
}

/** 计算浮窗初始位置：右侧垂直居中 */
function getInitialPos(): WidgetPos {
  return {
    x: EDGE_MARGIN,
    y: Math.max(0, (window.innerHeight - WIDGET_HEIGHT) / 2),
    side: 'right',
  }
}

/** 将位置吸附到最近边缘（照搬 openjiuwen：x 设为 EDGE_MARGIN，CSS 用 right/left 常量） */
function snapToEdge(pos: WidgetPos): WidgetPos {
  const newSide = pos.x > window.innerWidth / 2 ? 'right' : 'left'
  const clampedY = Math.max(0, Math.min(window.innerHeight - WIDGET_HEIGHT, pos.y))
  return { x: EDGE_MARGIN, y: clampedY, side: newSide }
}

/**
 * 右侧浮窗：完全照搬 openjiuwen.com 浮窗结构与拖拽逻辑。
 *
 * 光标：容器加 .oj-dragging class，CSS 规则 `.oj-dragging, .oj-dragging * { cursor: grabbing !important }`
 * 左右镜像：容器用 flex-direction: row / row-reverse，收起按钮用 .oj-collapse-right / .oj-collapse-left
 * 吸附提示：相对于容器定位，出现在浮窗上方
 * 拖拽事件：mousedown 在 buttonGroup → isDragging → useEffect 动态添加/移除 document 监听器
 */
export function WatchOpenJiuwenRepos() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { isAuthenticated, provider, user } = useGitCodeAuth()

  const [enabled, setEnabled] = useState(true)
  const [clicked, setClicked] = useState(false)
  const [flashing, setFlashing] = useState(false)
  const starSeq = useRef(0)
  const starringRef = useRef(false)
  const starringReleaseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [confirmOpen, setConfirmOpen] = useState(false)
  // 弹窗锚定坐标：根据浮窗 DOM 位置动态计算，弹窗出现在浮窗旁边而非固定右下角
  const [confirmCoords, setConfirmCoords] = useState<{ top: number; left: number; side: 'left' | 'right' } | null>(null)

  const [collapsed, setCollapsed] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [collapseHovered, setCollapseHovered] = useState(false)

  // ── 拖拽状态（照搬 openjiuwen） ──
  const [pos, setPos] = useState<WidgetPos>(getInitialPos)
  const [isDragging, setIsDragging] = useState(false)
  // hasMoved：鼠标移动超过 3px 才算真正的拖拽（区分点击），同时控制提示显示
  const [hasMoved, setHasMoved] = useState(false)
  const hasMovedRef = useRef(false)
  // 拖拽刚结束标记：mouseup 时置 true，200ms 后重置，用于阻止 click 事件穿透到按钮
  const justDraggedRef = useRef(false)
  // mousedown 起始坐标 + 起始位置（用于计算拖拽偏移）
  const dragStartRef = useRef({ clientX: 0, clientY: 0, posX: 0, posY: 0 })
  // 容器 DOM ref：mousedown 时用 getBoundingClientRect() 取真实屏幕位置
  const containerRef = useRef<HTMLDivElement | null>(null)
  // 弹窗 DOM ref：点击外部关闭时排除弹窗自身
  const confirmDialogRef = useRef<HTMLDivElement | null>(null)
  // 位置镜像 ref：event handler 内读取避免 stale closure
  const posRef = useRef(pos)
  posRef.current = pos
  // 拖拽结束定时器 ref：追踪 handleMouseUp 中的 setTimeout，用于 cleanup
  const dragEndTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── resize 时重新约束浮窗位置，防止视口缩小后浮窗不可见 ──
  useEffect(() => {
    const onResize = () => {
      const p = posRef.current
      const clampedY = Math.max(0, Math.min(window.innerHeight - WIDGET_HEIGHT, p.y))
      if (clampedY !== p.y) {
        setPos({ ...p, y: clampedY })
      }
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 拉取功能开关
  useEffect(() => {
    getSiteConfig()
      .then(cfg => setEnabled(cfg.github_star_enabled))
      .catch(() => {})
  }, [])

  // 用户变化时从后端查询标星状态
  useEffect(() => {
    if (!isAuthenticated || provider !== 'github') {
      setClicked(false)
      return
    }
    let cancelled = false
    getStarStatus()
      .then(starred => {
        if (!cancelled) setClicked(starred)
      })
      .catch(() => {
        if (!cancelled) setClicked(false)
      })
    return () => { cancelled = true }
  }, [isAuthenticated, provider, user?.login])

  // 卸载时清理定时器
  useEffect(() => {
    return () => {
      if (starringReleaseTimer.current !== null) {
        clearTimeout(starringReleaseTimer.current)
        starringReleaseTimer.current = null
      }
    }
  }, [])

  // ── 弹窗锚定坐标：打开时根据浮窗 DOM 位置计算 ──
  // 弹窗出现在浮窗旁边（side=right → 弹窗在左；side=left → 弹窗在右），
  // 垂直方向与星标按钮对齐（容器顶部），参照 NotificationBell 的 getBoundingClientRect 模式
  useEffect(() => {
    if (!confirmOpen) {
      setConfirmCoords(null)
      return
    }
    const recalc = () => {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) { setConfirmCoords(null); return }
      const side = posRef.current.side
      // 计算弹窗水平位置：side=right → 弹窗在左；side=left → 弹窗在右
      let dialogLeft: number
      let dialogSide: 'left' | 'right'
      if (side === 'right') {
        dialogLeft = rect.left - DIALOG_WIDTH - DIALOG_GAP
        dialogSide = 'left'
      } else {
        dialogLeft = rect.right + DIALOG_GAP
        dialogSide = 'right'
      }
      // 防溢出：弹窗宽度不足以在首选侧放置时，翻转到另一侧
      if (dialogSide === 'left' && dialogLeft < 0) {
        dialogLeft = rect.right + DIALOG_GAP
        dialogSide = 'right'
      } else if (dialogSide === 'right' && dialogLeft + DIALOG_WIDTH > window.innerWidth) {
        dialogLeft = rect.left - DIALOG_WIDTH - DIALOG_GAP
        dialogSide = 'left'
      }
      // 二次防溢出：两侧都不够时，贴边显示
      if (dialogSide === 'left' && dialogLeft < 0) dialogLeft = 0
      if (dialogSide === 'right' && dialogLeft + DIALOG_WIDTH > window.innerWidth) {
        dialogLeft = window.innerWidth - DIALOG_WIDTH
      }
      // 垂直方向防溢出：弹窗高度约 200px，确保不超出视口底部
      const DIALOG_EST_HEIGHT = 200
      let dialogTop = rect.top
      if (dialogTop + DIALOG_EST_HEIGHT > window.innerHeight) {
        dialogTop = Math.max(0, window.innerHeight - DIALOG_EST_HEIGHT)
      }
      setConfirmCoords({
        top: dialogTop,
        left: dialogLeft,
        side: dialogSide,
      })
    }
    recalc()
    const onResize = () => recalc()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  // pos 变化时也需要重算（拖拽结束后弹窗应跟随浮窗新位置）
  }, [confirmOpen, pos])

  // ── 弹窗交互：ESC 关闭 + 点击外部关闭 ──
  useEffect(() => {
    if (!confirmOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setConfirmOpen(false) }
    const onClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (containerRef.current?.contains(target)) return
      if (confirmDialogRef.current?.contains(target)) return
      setConfirmOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClickOutside)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClickOutside)
    }
  }, [confirmOpen])

  // ── 拖拽：mousedown 在 buttonGroup 上 ──
  const handleButtonGroupMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    if (collapsed) return
    e.preventDefault()
    e.stopPropagation()
    hasMovedRef.current = false
    setHasMoved(false)
    justDraggedRef.current = false
    // 清除上一轮拖拽的结束定时器，防止快速连续拖拽时旧定时器干扰
    if (dragEndTimerRef.current !== null) {
      clearTimeout(dragEndTimerRef.current)
      dragEndTimerRef.current = null
    }
    // 用 DOM getBoundingClientRect() 获取真实屏幕位置（pos.x 存的是 EDGE_MARGIN，不是绝对坐标）
    const rect = containerRef.current?.getBoundingClientRect()
    dragStartRef.current = {
      clientX: e.clientX,
      clientY: e.clientY,
      posX: rect ? rect.left : (posRef.current.side === 'right' ? window.innerWidth - WIDGET_WIDTH - EDGE_MARGIN : EDGE_MARGIN),
      posY: rect ? rect.top : posRef.current.y,
    }
    setIsDragging(true)
  }, [collapsed])

  // ── 拖拽：document mousemove/mouseup（仅在 isDragging=true 时注册） ──
  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (e: MouseEvent) => {
      const start = dragStartRef.current
      const dx = e.clientX - start.clientX
      const dy = e.clientY - start.clientY

      if (!hasMovedRef.current) {
        if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return
        hasMovedRef.current = true
        setHasMoved(true)
      }

      const newX = Math.max(0, Math.min(window.innerWidth - WIDGET_WIDTH, start.posX + dx))
      const newY = Math.max(0, Math.min(window.innerHeight - WIDGET_HEIGHT, start.posY + dy))

      setPos({ x: newX, y: newY, side: newX > window.innerWidth / 2 ? 'right' : 'left' })
    }

    // 拖拽结束的共享逻辑：吸附 + 重置 isDragging + 设置 justDraggedRef 定时器
    // handleMouseUp（正常释放）和 handleBlur（窗口失焦时 mouseup 不触发）共用
    const endDrag = () => {
      if (hasMovedRef.current) {
        const snapped = snapToEdge(posRef.current)
        setPos(snapped)
        justDraggedRef.current = true
        if (dragEndTimerRef.current !== null) clearTimeout(dragEndTimerRef.current)
        dragEndTimerRef.current = setTimeout(() => {
          dragEndTimerRef.current = null
          hasMovedRef.current = false
          setHasMoved(false)
          justDraggedRef.current = false
        }, 200)
      } else {
        hasMovedRef.current = false
        setHasMoved(false)
      }
      setIsDragging(false)
    }

    const handleMouseUp = (e: MouseEvent) => {
      if (hasMovedRef.current && e) {
        e.preventDefault()
        e.stopPropagation()
      }
      // 只在真正拖拽过时才吸附到边缘；纯点击（未移动）不改变位置
      // 否则 pos.x=EDGE_MARGIN(20) < innerWidth/2 会误判为左侧，导致点击后从右跳到左
      endDrag()
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    // 保存原有 userSelect 值，拖拽结束后恢复（避免冲掉其他代码设置的值）
    const prevUserSelect = document.body.style.userSelect
    const prevWebkitUserSelect = document.body.style.webkitUserSelect
    document.body.style.userSelect = 'none'
    document.body.style.webkitUserSelect = 'none'
    // 窗口失焦保底：Alt-Tab / 切窗口时 mouseup 不触发，需通过 blur 结束拖拽
    // 否则 isDragging 保持 true，切回窗口后移动鼠标浮窗会无按键跟随
    const handleBlur = () => {
      endDrag()
      document.body.style.userSelect = prevUserSelect
      document.body.style.webkitUserSelect = prevWebkitUserSelect
    }
    window.addEventListener('blur', handleBlur)

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      window.removeEventListener('blur', handleBlur)
      document.body.style.userSelect = prevUserSelect
      document.body.style.webkitUserSelect = prevWebkitUserSelect
    }
  }, [isDragging])

  // 拖拽结束定时器只在组件卸载时清理，不能放在 isDragging effect cleanup 中
  // 因为 setIsDragging(false) 会触发该 cleanup，从而在 200ms 定时器触发前将其清除，
  // 导致 justDraggedRef 永久卡在 true，按钮永久不可点击
  useEffect(() => {
    return () => {
      if (dragEndTimerRef.current !== null) {
        clearTimeout(dragEndTimerRef.current)
        dragEndTimerRef.current = null
      }
    }
  }, [])

  // 阻止浏览器原生拖拽
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const doStar = useCallback(() => {
    if (starringRef.current) return
    starringRef.current = true
    const seq = ++starSeq.current
    setClicked(true)
    setFlashing(true)
    let succeeded = false

    starAllRepos()
      .then(() => { succeeded = true })
      .catch(err => {
        if (seq !== starSeq.current) return
        console.warn('github star failed (background):', err)
        setClicked(false)
        setConfirmOpen(false)
        const status = err instanceof GithubWatchError ? err.status : undefined
        if (status === 401) {
          setPostLoginRedirect('/')
          navigate('/login?from=star')
        }
      })
      .finally(() => {
        if (seq === starSeq.current) setFlashing(false)
        if (starringReleaseTimer.current !== null) clearTimeout(starringReleaseTimer.current)
        if (succeeded) {
          starringReleaseTimer.current = setTimeout(() => {
            starringReleaseTimer.current = null
            starringRef.current = false
          }, 22000)
        } else {
          starringRef.current = false
        }
      })
  }, [navigate])

  const handleStarClick = useCallback(() => {
    if (hasMovedRef.current) return
    if (!isAuthenticated) {
      setPostLoginRedirect('/')
      navigate('/login?from=star')
      return
    }
    if (flashing) return
    doStar()
    setConfirmOpen(true)
  }, [isAuthenticated, navigate, doStar, flashing])

  const scrollToTop = useCallback(() => {
    if (hasMovedRef.current) return
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  if (!enabled || (isAuthenticated && provider !== 'github')) return null

  const starLabel = t('plugins.githubWatch.starAll')
  const codeLabel = t('plugins.githubWatch.code')
  const topLabel = t('plugins.githubWatch.backToTop')

  // 收起按钮样式
  const GRADIENT_ACTIVE = 'linear-gradient(135deg, rgb(10, 89, 247) 0%, rgb(115, 38, 255) 100%)'
  const GRADIENT_HOVER = 'linear-gradient(135deg, rgb(240, 241, 244) 0%, rgb(232, 233, 236) 100%)'
  const GRADIENT_IDLE = 'linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(248,249,252,0.95) 100%)'
  const collapseColor = collapsed ? '#ffffff' : collapseHovered ? 'rgb(10, 89, 247)' : '#666666'
  const collapseBg = collapsed ? GRADIENT_ACTIVE : collapseHovered ? GRADIENT_HOVER : GRADIENT_IDLE
  const collapseBorder = collapsed
    ? 'none'
    : collapseHovered
      ? '1px solid rgba(10, 89, 247, 0.3)'
      : '1px solid rgba(229,229,229,0.8)'
  const collapseShadow = collapseHovered && collapsed
    ? 'rgba(10,89,247,0.55) 0px 6px 20px'
    : collapseHovered && !collapsed
      ? 'rgba(10,89,247,0.2) 0px 4px 12px'
      : collapsed
        ? 'rgba(10,89,247,0.4) 0px 4px 16px'
        : 'rgba(0,0,0,0.1) 0px 2px 8px'

  // 容器 class：照搬 openjiuwen 的 contactContainerRight/Left + Dragging 模式
  // .oj-dragging, .oj-dragging * { cursor: grabbing !important } — 在全局 CSS 中定义
  const containerClassName = [
    'oj-contact-container',
    pos.side === 'right' ? 'oj-contact-right' : 'oj-contact-left',
    isDragging ? 'oj-dragging' : '',
  ].filter(Boolean).join(' ')

  // 外层容器样式（照搬 openjiuwen）：
  // - 拖拽中：left=绝对坐标, right=auto, transition=none（精确跟随鼠标）
  // - 非拖拽：side=right → right=EDGE_MARGIN, left=auto；side=left → left=EDGE_MARGIN, right=auto
  //   CSS right/left 常量让浏览器自动跟随视口边缘，resize 时不需 JS 干预
  // - transition: all 0.3s（照搬 openjiuwen，left↔auto 无法插值所以吸附瞬间完成）
  const containerStyle: React.CSSProperties = {
    position: 'fixed',
    top: pos.y,
    ...(isDragging && hasMoved
      ? { left: pos.x, right: 'auto' }
      : pos.side === 'right'
        ? { right: EDGE_MARGIN, left: 'auto' }
        : { left: EDGE_MARGIN, right: 'auto' }),
    zIndex: 1000,
    alignItems: 'center',
    display: 'flex',
    flexDirection: pos.side === 'right' ? 'row' : 'row-reverse',
    height: WIDGET_HEIGHT,
    transition: isDragging ? 'none' : 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
    userSelect: 'none',
  }

  // 收起按钮 class：照搬 openjiuwen 的 collapseButtonRight/Left + Collapsed
  const collapseClassName = [
    'oj-contact-collapse',
    pos.side === 'right' ? 'oj-collapse-right' : 'oj-collapse-left',
    collapsed ? 'oj-collapse-collapsed' : '',
  ].filter(Boolean).join(' ')

  const floatingWidget = (
    <div
      ref={containerRef}
      className={containerClassName}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={containerStyle}
    >
      {/* 拖拽提示（照搬 openjiuwen：isDragging 期间始终显示，浮窗上方，fadeInDown 动画） */}
      {isDragging && hasMoved && (
        <div className="oj-drag-hint">
          {t('plugins.githubWatch.snapHint')}
        </div>
      )}

      {/* 收起/展开按钮（不在 buttonGroup 内，照搬 openjiuwen） */}
      <button
        type="button"
        onClick={() => setCollapsed(c => !c)}
        onMouseEnter={() => setCollapseHovered(true)}
        onMouseLeave={() => setCollapseHovered(false)}
        title={collapsed ? t('plugins.githubWatch.expand') : t('plugins.githubWatch.collapse')}
        aria-label={collapsed ? t('plugins.githubWatch.expand') : t('plugins.githubWatch.collapse')}
        className={collapseClassName}
        style={{
          cursor: 'pointer',
          opacity: collapsed || hovered ? 1 : 0,
          color: collapseColor,
          background: collapseBg,
          border: collapseBorder,
          justifyContent: 'center',
          alignItems: 'center',
          width: collapsed ? 32 : 20,
          height: collapsed ? 56 : 36,
          transition: '0.3s',
          display: 'flex',
          position: 'absolute',
          top: collapsed ? 50 : 60,
          bottom: collapsed ? 50 : 60,
          boxShadow: collapseShadow,
          pointerEvents: 'auto',
        }}
      >
        {/* side=right: 收起用左箭头，展开用右箭头；side=left: 镜像 */}
        {collapsed ? (
          pos.side === 'right' ? (
            <ChevronLeft style={{ width: 16, height: 16 }} />
          ) : (
            <ChevronRight style={{ width: 16, height: 16 }} />
          )
        ) : (
          pos.side === 'right' ? (
            <ChevronRight style={{ width: 12, height: 12 }} />
          ) : (
            <ChevronLeft style={{ width: 12, height: 12 }} />
          )
        )}
      </button>

      {/* 按钮组（mousedown 在此注册，照搬 openjiuwen buttonGroup） */}
      <div
        className="oj-button-group"
        onMouseDown={handleButtonGroupMouseDown}
        onDragStart={handleDragStart}
        onClickCapture={(e) => { if (justDraggedRef.current) { e.stopPropagation(); e.preventDefault() } }}
        style={{
          backdropFilter: 'blur(10px)',
          WebkitBackdropFilter: 'blur(10px)',
          background: 'rgba(255, 255, 255, 0.95)',
          borderRadius: pos.side === 'right' ? '12px 0 0 12px' : '0 12px 12px 0',
          flexDirection: 'column',
          display: collapsed ? 'none' : 'flex',
          overflow: 'hidden',
          boxShadow: 'rgba(0, 0, 0, 0.12) 0px 4px 20px',
          transition: 'opacity 0.3s',
        }}
      >
        {/* 标星按钮 */}
        <FloatButton
          onClick={handleStarClick}
          title={t('plugins.githubWatch.starAllTip')}
          ariaLabel={starLabel}
          variant="star"
        >
          {flashing ? (
            <Loader2
              className={`oj-btn-icon oj-star-icon${clicked ? ' oj-starred' : ''} animate-spin`}
              style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
              aria-hidden
            />
          ) : (
            <Star
              className={`oj-btn-icon oj-star-icon${clicked ? ' oj-starred' : ''}`}
              style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s, fill 0.2s' }}
              strokeWidth={2}
              aria-hidden
            />
          )}
          <FloatLabel className={`oj-btn-label oj-star-label${clicked ? ' oj-starred-label' : ''}`}>{starLabel}</FloatLabel>
        </FloatButton>

        {/* 代码按钮 */}
        <a
          href={GITHUB_ORG_URL}
          target="_blank"
          rel="noopener noreferrer"
          draggable={false}
          title={t('plugins.githubWatch.codeTip')}
          className="oj-float-btn oj-code-btn"
          style={{
            cursor: 'pointer',
            border: 'none',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 2,
            width: WIDGET_WIDTH,
            height: BUTTON_HEIGHT,
            textDecoration: 'none',
            transition: '0.2s',
            display: 'flex',
            position: 'relative',
          }}
        >
          <Code2
            style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
            className="oj-btn-icon"
            strokeWidth={2}
            aria-hidden
          />
          <span
            className="oj-btn-label"
            style={{ fontSize: 10, fontWeight: 600, lineHeight: 1, transition: 'color 0.2s' }}
          >
            {codeLabel}
          </span>
        </a>

        {/* 回到顶部按钮 */}
        <FloatButton
          onClick={scrollToTop}
          title={t('plugins.githubWatch.backToTopTip')}
          ariaLabel={topLabel}
          variant="top"
        >
          <ChevronUp
            style={{ width: 20, height: 20, flexShrink: 0, transition: 'color 0.2s' }}
            className="oj-btn-icon"
            strokeWidth={2}
            aria-hidden
          />
        </FloatButton>
      </div>
    </div>
  )

  // 通知弹窗：锚定在浮窗旁边（side=right → 弹窗在左；side=left → 弹窗在右）
  const confirmDialog = confirmOpen && confirmCoords ? (
    <div
      ref={confirmDialogRef}
      className={`oj-notify-card oj-notify-${confirmCoords.side} fixed z-[1100] rounded-2xl bg-white p-5 shadow-[0_8px_40px_rgba(0,0,0,0.16)]`}
      style={{ top: confirmCoords.top, left: confirmCoords.left, width: DIALOG_WIDTH }}
      role="dialog"
      aria-modal="false"
      aria-hidden={!confirmOpen}
      aria-label={t('plugins.githubWatch.confirmTitle')}
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-amber-50">
          {flashing ? (
            <Loader2 className="h-5 w-5 animate-spin text-amber-500" aria-hidden />
          ) : (
            <Star className="h-5 w-5 fill-amber-400 text-amber-500" aria-hidden />
          )}
        </span>
        <h2 className="text-base font-semibold text-slate-900">
          {t('plugins.githubWatch.confirmTitle')}
        </h2>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-slate-600">
        {t('plugins.githubWatch.confirmBody')}
      </p>
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => setConfirmOpen(false)}
          className="rounded-lg bg-[linear-gradient(99.61deg,#1E54FA_0%,#842EFD_100%)] px-4 py-2 text-sm font-medium text-white shadow-[0_2px_8px_rgba(81,64,246,0.18)] transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
        >
          {t('plugins.githubWatch.confirmOk')}
        </button>
      </div>
    </div>
  ) : null

  return (
    <>
      {typeof document !== 'undefined' ? createPortal(floatingWidget, document.body) : null}
      {typeof document !== 'undefined' && confirmDialog ? createPortal(confirmDialog, document.body) : null}
    </>
  )
}

// ── 子组件：浮窗按钮 ──
function FloatButton({
  children,
  onClick,
  title,
  ariaLabel,
  variant,
}: {
  children: React.ReactNode
  onClick: () => void
  title: string
  ariaLabel: string
  variant: 'star' | 'top'
}) {
  const isStar = variant === 'star'
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      className="oj-float-btn"
      style={{
        cursor: 'pointer',
        border: 'none',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 2,
        width: WIDGET_WIDTH,
        height: BUTTON_HEIGHT,
        transition: '0.2s',
        display: 'flex',
        position: 'relative',
        borderRadius: isStar ? '12px 12px 0 0' : '0 0 12px 12px',
      }}
    >
      {children}
      {isStar && (
        <span
          style={{
            background: 'rgba(0, 0, 0, 0.06)',
            width: 28,
            height: 1,
            position: 'absolute',
            bottom: 0,
            left: '50%',
            transform: 'translateX(-50%)',
            pointerEvents: 'none',
          }}
        />
      )}
    </button>
  )
}

function FloatLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={`oj-btn-label${className ? ` ${className}` : ''}`}
      style={{
        fontSize: 10,
        fontWeight: 600,
        lineHeight: 1,
        transition: 'color 0.2s',
      }}
    >
      {children}
    </span>
  )
}
