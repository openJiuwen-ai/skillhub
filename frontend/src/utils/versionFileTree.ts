// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { VersionFileEntry } from '@/api/plugin'

export interface VersionFileTreeNode {
  name: string
  /** 目录为文件夹路径；文件为完整相对路径。 */
  path: string
  kind: 'dir' | 'file'
  size?: number
  children?: VersionFileTreeNode[]
}

function sortTreeNodes(nodes: VersionFileTreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.kind !== b.kind) {
      return a.kind === 'dir' ? -1 : 1
    }
    return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
  })
  for (const node of nodes) {
    if (node.children?.length) {
      sortTreeNodes(node.children)
    }
  }
}

function findChildByName(parent: VersionFileTreeNode, name: string): VersionFileTreeNode | undefined {
  return parent.children?.find(item => item.name === name)
}

/** 确保中间路径为目录；若同名 leaf 文件已存在则升级为目录。 */
function ensureDir(parent: VersionFileTreeNode, part: string, pathSoFar: string): VersionFileTreeNode {
  if (!parent.children) {
    parent.children = []
  }
  const existing = findChildByName(parent, part)
  if (existing?.kind === 'dir') {
    return existing
  }
  if (existing?.kind === 'file') {
    const dir: VersionFileTreeNode = { name: part, path: pathSoFar, kind: 'dir', children: [] }
    parent.children = parent.children.map(item => (item.name === part ? dir : item))
    return dir
  }
  const dir: VersionFileTreeNode = { name: part, path: pathSoFar, kind: 'dir', children: [] }
  parent.children.push(dir)
  return dir
}

/** 写入 leaf 文件；重复 path 更新 size，与同名目录冲突时忽略文件。 */
function upsertFile(parent: VersionFileTreeNode, part: string, file: VersionFileEntry): void {
  if (!parent.children) {
    parent.children = []
  }
  const existing = findChildByName(parent, part)
  if (existing?.kind === 'dir') {
    return
  }
  const fileNode: VersionFileTreeNode = {
    name: part,
    path: file.path,
    kind: 'file',
    size: file.size,
  }
  if (existing?.kind === 'file') {
    parent.children = parent.children.map(item => (item.name === part ? fileNode : item))
    return
  }
  parent.children.push(fileNode)
}

export function buildVersionFileTree(files: VersionFileEntry[]): VersionFileTreeNode[] {
  const root: VersionFileTreeNode = { name: '', path: '', kind: 'dir', children: [] }

  for (const file of files) {
    const parts = file.path.split('/').filter(Boolean)
    if (!parts.length) continue

    let current = root
    let pathSoFar = ''
    for (let index = 0; index < parts.length; index += 1) {
      const part = parts[index]
      const isLast = index === parts.length - 1
      pathSoFar = pathSoFar ? `${pathSoFar}/${part}` : part

      if (isLast) {
        upsertFile(current, part, file)
        continue
      }
      current = ensureDir(current, part, pathSoFar)
    }
  }

  const topLevel = root.children ?? []
  sortTreeNodes(topLevel)
  return topLevel
}

/** 返回文件路径的所有祖先目录路径（不含文件本身）。 */
export function versionFileAncestorDirs(filePath: string): string[] {
  const parts = filePath.split('/').filter(Boolean)
  if (parts.length <= 1) return []
  const parents: string[] = []
  for (let index = 1; index < parts.length; index += 1) {
    parents.push(parts.slice(0, index).join('/'))
  }
  return parents
}

/** 收集树中全部目录 path，用于首次默认全部展开。 */
export function collectVersionFileDirPaths(nodes: VersionFileTreeNode[]): string[] {
  const dirs: string[] = []
  const walk = (items: VersionFileTreeNode[]) => {
    for (const item of items) {
      if (item.kind === 'dir') {
        dirs.push(item.path)
        if (item.children?.length) walk(item.children)
      }
    }
  }
  walk(nodes)
  return dirs
}
