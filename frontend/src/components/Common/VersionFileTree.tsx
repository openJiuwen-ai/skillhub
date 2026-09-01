// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, File, Folder } from 'lucide-react'
import type { VersionFileEntry } from '@/api/plugin'
import {
  buildVersionFileTree,
  collectVersionFileDirPaths,
  versionFileAncestorDirs,
  type VersionFileTreeNode,
} from '@/utils/versionFileTree'

const INDENT_PX = 14

interface VersionFileTreeProps {
  files: VersionFileEntry[]
  selectedPath: string | null
  onSelectFile: (path: string) => void
  ariaLabel?: string
}

function formatFileSize(size?: number): string {
  if (size == null || !Number.isFinite(size)) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

interface TreeRowProps {
  node: VersionFileTreeNode
  depth: number
  selectedPath: string | null
  expandedDirs: Set<string>
  onToggleDir: (path: string) => void
  onSelectFile: (path: string) => void
}

function TreeRow({ node, depth, selectedPath, expandedDirs, onToggleDir, onSelectFile }: TreeRowProps) {
  const paddingLeft = 8 + depth * INDENT_PX

  if (node.kind === 'file') {
    const selected = selectedPath === node.path
    return (
      <li role="treeitem" aria-selected={selected}>
        <button
          type="button"
          onClick={() => onSelectFile(node.path)}
          className={[
            'flex w-full min-w-0 items-center gap-1.5 py-1.5 pr-2 text-left text-xs transition',
            selected ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-700 hover:bg-slate-100',
          ].join(' ')}
          style={{ paddingLeft }}
          title={node.path}
        >
          <File className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {node.size != null ? (
            <span className="shrink-0 tabular-nums text-[10px] text-slate-400">{formatFileSize(node.size)}</span>
          ) : null}
        </button>
      </li>
    )
  }

  const expanded = expandedDirs.has(node.path)
  const Chevron = expanded ? ChevronDown : ChevronRight

  return (
    <li role="treeitem" aria-expanded={expanded}>
      <button
        type="button"
        onClick={() => onToggleDir(node.path)}
        className="flex w-full min-w-0 items-center gap-1 py-1.5 pr-2 text-left text-xs font-medium text-slate-600 hover:bg-slate-100"
        style={{ paddingLeft }}
        title={node.path}
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
        <Folder className="h-3.5 w-3.5 shrink-0 text-amber-500/80" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
      </button>
      {expanded && node.children?.length ? (
        <ul role="group">
          {node.children.map(child => (
            <TreeRow
              key={child.kind === 'file' ? child.path : `dir:${child.path}`}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              expandedDirs={expandedDirs}
              onToggleDir={onToggleDir}
              onSelectFile={onSelectFile}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}

export function VersionFileTree({ files, selectedPath, onSelectFile, ariaLabel = 'Files' }: VersionFileTreeProps) {
  const tree = useMemo(() => buildVersionFileTree(files), [files])
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    setExpandedDirs(new Set(collectVersionFileDirPaths(tree)))
  }, [tree])

  useEffect(() => {
    if (!selectedPath) return
    setExpandedDirs(prev => {
      const next = new Set(prev)
      let changed = false
      for (const dir of versionFileAncestorDirs(selectedPath)) {
        if (!next.has(dir)) {
          next.add(dir)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [selectedPath])

  const onToggleDir = useCallback((path: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  if (!tree.length) return null

  return (
    <ul className="py-1" role="tree" aria-label={ariaLabel}>
      {tree.map(node => (
        <TreeRow
          key={node.kind === 'file' ? node.path : `dir:${node.path}`}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          expandedDirs={expandedDirs}
          onToggleDir={onToggleDir}
          onSelectFile={onSelectFile}
        />
      ))}
    </ul>
  )
}
