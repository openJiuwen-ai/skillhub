// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 与后端 git_skills_subpath_rules 对齐：提交前拒绝路径穿越与绝对路径。 */

/** 归一化技能根相对路径；空则返回空串（仓库根）。 */
export function normalizeGitSkillsSubpath(subpath: string): string {
  const raw = (subpath || '').trim()
  if (!raw) return ''
  return raw.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
}

/** 校验技能根相对路径；空值视为合法（使用仓库根）。 */
export function isValidGitSkillsSubpath(subpath: string): boolean {
  const raw = (subpath || '').trim()
  if (!raw) return true
  if (raw.length > 512) return false
  if (raw.includes('\0')) return false
  if (raw.startsWith('/') || raw.startsWith('\\')) return false
  if (/^[a-zA-Z]:/.test(raw)) return false

  const rel = normalizeGitSkillsSubpath(raw)
  if (!rel) return true

  for (const segment of rel.split('/')) {
    if (segment === '..') return false
  }
  return true
}
