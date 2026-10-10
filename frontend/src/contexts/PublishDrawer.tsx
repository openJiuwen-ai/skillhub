// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import {
  getPrimarySkillPluginType,
  parseMarketTabType,
  type MarketTabPluginType,
} from '@/utils/pluginType'

export type PublishDrawerType = MarketTabPluginType

type PublishDrawerContextValue = {
  /** 抽屉是否打开 */
  open: boolean
  /** 当前发布类型 */
  type: PublishDrawerType
  /** 打开发布抽屉 */
  openPublish: (type?: PublishDrawerType) => void
  /** 关闭抽屉 */
  closePublish: () => void
}

const Ctx = createContext<PublishDrawerContextValue | null>(null)

export function usePublishDrawer(): PublishDrawerContextValue {
  const ctx = useContext(Ctx)
  if (!ctx) {
    throw new Error('usePublishDrawer 必须在 <PublishDrawerProvider /> 内使用')
  }
  return ctx
}

/**
 * 仅负责维护抽屉开关状态，本文件**不导入**任何业务组件，避免 HMR 下因为
 * 跨模块热更新导致 context 与 Provider 分裂为两份实例。
 * 抽屉的实际渲染在 `App.tsx` 内通过 {@link usePublishDrawer} 消费状态完成。
 */
export function PublishDrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [type, setType] = useState<PublishDrawerType>(getPrimarySkillPluginType())

  const openPublish = useCallback((nextType?: PublishDrawerType) => {
    const resolved = parseMarketTabType(nextType) ?? getPrimarySkillPluginType()
    setType(resolved)
    setOpen(true)
  }, [])
  const closePublish = useCallback(() => setOpen(false), [])

  const value = useMemo(
    () => ({ open, type, openPublish, closePublish }),
    [open, type, openPublish, closePublish],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
