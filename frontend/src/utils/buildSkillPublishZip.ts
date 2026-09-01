// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import JSZip from 'jszip'
import { dump as yamlDump, load as yamlLoad } from 'js-yaml'

/** 与 marketplace `plugins_market.validation.constants` 对齐 */
const SKILL_NAME_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/
const SEMVER_PATTERN = /^\d+\.\d+\.\d+$/
const DISPLAY_NAME_MAX_LEN = 128
const PLUGIN_YAML_DESCRIPTION_MAX_LEN = 4096
const SKILL_DESC_MAX_LEN = 4096
const MAX_ZIP_ENTRIES = 1000
const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

const PACK_IGNORE_DIR_NAMES = new Set([
  '.git',
  '__pycache__',
  '.venv',
  'venv',
  '.eggs',
  'dist',
  'out',
  '__MACOSX',
])

export type BuildSkillPublishZipInput = {
  /** plugin.yaml `name`，须与 skill 子目录名一致 */
  name: string
  /** plugin.yaml `version`，x.y.z */
  version: string
  displayName: string
  /** plugin.yaml `description`（市场 short_desc）；留空则从 SKILL.md YAML 头的 description 读取 */
  description?: string
  tags: string[]
  /** GitCode 登录名 → metadata.author */
  authorLogin: string
  iconFile?: File
  /** 来自 `<input webkitdirectory>` 的 File 列表 */
  skillDirectoryFiles: File[]
}

/** 与表单校验、publish API 使用同一套规范化逻辑（去首尾空白；须为 x.y.z，不接受 v 前缀）。 */
export function normalizeSemver(raw: string): string {
  const s = raw.trim()
  if (!SEMVER_PATTERN.test(s)) {
    throw new Error('INVALID_VERSION')
  }
  return s
}

function normalizeSkillSlug(raw: string): string {
  const s = raw.trim().toLowerCase()
  if (!s || s.length > 64 || !SKILL_NAME_PATTERN.test(s)) {
    throw new Error('INVALID_NAME')
  }
  return s
}

function parseTags(raw: string): string[] {
  const parts = raw
    .split(/[,，]/)
    .map(s => s.trim())
    .filter(Boolean)
  return parts
}

async function assertPng(file: File): Promise<void> {
  if (file.size > 5 * 1024 * 1024) {
    throw new Error('ICON_TOO_LARGE')
  }
  const buf = new Uint8Array(await file.slice(0, 8).arrayBuffer())
  if (buf.length < PNG_MAGIC.length) throw new Error('ICON_NOT_PNG')
  for (let i = 0; i < PNG_MAGIC.length; i++) {
    if (buf[i] !== PNG_MAGIC[i]) throw new Error('ICON_NOT_PNG')
  }
}

function shouldIgnorePath(relPosix: string): boolean {
  const parts = relPosix.split('/').filter(Boolean)
  for (const p of parts) {
    if (p === '.' || p === '..') return true
    if (p.startsWith('.')) return true
    if (PACK_IGNORE_DIR_NAMES.has(p) || p.endsWith('.egg-info')) return true
    if (p === '.DS_Store') return true
  }
  return false
}

function stripRootFolder(webkitRelativePath: string): string {
  const posix = webkitRelativePath.replace(/\\/g, '/').replace(/^\//, '')
  const parts = posix.split('/').filter(Boolean)
  if (parts.length < 2) return ''
  return parts.slice(1).join('/')
}

/** 与后端 `skill.py::_line_closes_frontmatter_fence` 语义一致：行首无缩进的 `---` 才结束 frontmatter */
function lineClosesSkillFrontmatterFence(line: string | undefined): boolean {
  if (line === undefined) return false
  const s = line.replace(/\r$/, '').replace(/^\uFEFF/, '')
  if (s.trimEnd() !== '---') return false
  return !/^\s/.test(s)
}

/** 从 SKILL.md 文本解析 frontmatter 中的 `description`（非空 trim 后） */
function parseSkillMdFrontmatterDescription(raw: string): string | null {
  const text = raw.replace(/^\uFEFF/, '')
  if (!text.startsWith('---')) return null
  const lines = text.split(/\r?\n/)
  let end = -1
  for (let i = 1; i < lines.length; i++) {
    if (lineClosesSkillFrontmatterFence(lines[i])) {
      end = i
      break
    }
  }
  if (end < 0) return null
  const fmText = lines.slice(1, end).join('\n').trim()
  if (!fmText) return null
  let fm: unknown
  try {
    fm = yamlLoad(fmText)
  } catch {
    // 解析失败时勿与「缺 description」混淆（常见于 team-skill 的 roles 未正确缩进）
    throw new Error('INVALID_SKILL_MD_FRONTMATTER_YAML')
  }
  if (!fm || typeof fm !== 'object' || Array.isArray(fm)) return null
  const desc = (fm as Record<string, unknown>).description
  if (typeof desc !== 'string') return null
  const s = desc.trim()
  return s || null
}

type SkillFrontmatter = {
  description: string | null
  kind: string | null
  roles: unknown[] | null
  tags: string[] | null
}

export type PublishPluginType = 'skill' | 'swarmskill'
export type DetectedPublishPluginType =
  | PublishPluginType
  | 'agent-plugin'
  | 'agent-template'
  | 'agent-mcp'
  | 'unknown'

export function mapSkillFrontmatterKindToPluginType(kind: string | null | undefined): DetectedPublishPluginType {
  const normalized = kind?.trim().toLowerCase() ?? ''
  if (!normalized) return 'skill'
  if (normalized === 'team-skill' || normalized === 'swarm-skill') return 'swarmskill'
  return 'unknown'
}

export function parseSkillMdFrontmatter(raw: string): SkillFrontmatter {
  const empty: SkillFrontmatter = { description: null, kind: null, roles: null, tags: null }
  const text = raw.replace(/^\uFEFF/, '')
  if (!text.startsWith('---')) return empty
  const lines = text.split(/\r?\n/)
  let end = -1
  for (let i = 1; i < lines.length; i++) {
    if (lineClosesSkillFrontmatterFence(lines[i])) {
      end = i
      break
    }
  }
  if (end < 0) return empty
  const fmText = lines.slice(1, end).join('\n').trim()
  if (!fmText) return empty
  let fm: unknown
  try {
    fm = yamlLoad(fmText)
  } catch {
    throw new Error('INVALID_SKILL_MD_FRONTMATTER_YAML')
  }
  if (!fm || typeof fm !== 'object' || Array.isArray(fm)) return empty
  const obj = fm as Record<string, unknown>
  const desc = typeof obj.description === 'string' ? obj.description.trim() || null : null
  const kind = typeof obj.kind === 'string' ? obj.kind.trim() || null : null
  const roles = Array.isArray(obj.roles) ? obj.roles : null
  const rawTags = obj.tags
  let tags: string[] | null = null
  if (Array.isArray(rawTags)) {
    tags = rawTags.filter((t: unknown) => typeof t === 'string' && t.trim()).map((t: string) => t.trim())
  } else if (typeof rawTags === 'string' && rawTags.trim()) {
    tags = [rawTags.trim()]
  }
  return { description: desc, kind, roles, tags }
}

/**
 * 按市场 skill 包结构打包：`{name}/plugin.yaml`、`icon.png`、`{name}/SKILL.md` 及用户目录内其余文件。
 * SKILL.md 与目录内文件一致写入，不将表单 description 注入覆盖其 YAML 头。
 */
export async function detectPublishPluginType(skillDirectoryFiles: File[]): Promise<DetectedPublishPluginType> {
  const files = skillDirectoryFiles
  if (!files.length) throw new Error('NO_SKILL_FILES')

  const entries: { relInSkill: string; file: File }[] = []
  let hasSkillMd = false
  for (const f of files) {
    const wrp = (f as File & { webkitRelativePath?: string }).webkitRelativePath
    if (!wrp || typeof wrp !== 'string') throw new Error('MISSING_RELATIVE_PATH')
    const rel = stripRootFolder(wrp)
    if (!rel) continue
    if (shouldIgnorePath(rel)) continue
    const lower = rel.toLowerCase()
    if (lower.endsWith('.pyc') || lower.endsWith('.pyo')) continue
    if (rel === 'SKILL.md') hasSkillMd = true
    entries.push({ relInSkill: rel, file: f })
  }

  if (!hasSkillMd) throw new Error('MISSING_SKILL_MD')
  const skillMdEntry = entries.find(e => e.relInSkill === 'SKILL.md')
  if (!skillMdEntry) return 'unknown'
  const fm = parseSkillMdFrontmatter(await skillMdEntry.file.text())
  const detectedType = mapSkillFrontmatterKindToPluginType(fm.kind)
  if (detectedType === 'unknown') throw new Error('INVALID_SKILL_KIND')
  if (detectedType === 'swarmskill') {
    if (!fm.roles) throw new Error('TEAM_SKILL_ROLES_REQUIRED')
    if (fm.roles.length < 2) throw new Error('TEAM_SKILL_ROLES_MIN_2')
    for (let i = 0; i < fm.roles.length; i++) {
      const r = fm.roles[i]
      if (!r || typeof r !== 'object' || typeof (r as Record<string, unknown>).id !== 'string' || !(r as Record<string, unknown>).id) {
        throw new Error('TEAM_SKILL_ROLE_NO_ID')
      }
    }
  }
  return detectedType
}

export async function buildSkillPublishZip(input: BuildSkillPublishZipInput): Promise<File> {
  const name = normalizeSkillSlug(input.name)
  const version = normalizeSemver(input.version)
  const displayName = input.displayName.trim()
  if (!displayName || displayName.length > DISPLAY_NAME_MAX_LEN) {
    throw new Error('INVALID_DISPLAY_NAME')
  }
  const author = input.authorLogin.trim()
  if (!author) throw new Error('INVALID_AUTHOR')

  if (input.iconFile) {
    await assertPng(input.iconFile)
  }

  const tags = input.tags.length ? input.tags : []
  for (const t of tags) {
    if (!t.trim()) throw new Error('INVALID_TAG')
  }

  const files = input.skillDirectoryFiles
  if (!files.length) throw new Error('NO_SKILL_FILES')

  let hasSkillMd = false
  const entries: { relInSkill: string; file: File }[] = []

  for (const f of files) {
    const wrp = (f as File & { webkitRelativePath?: string }).webkitRelativePath
    if (!wrp || typeof wrp !== 'string') {
      throw new Error('MISSING_RELATIVE_PATH')
    }
    const rel = stripRootFolder(wrp)
    if (!rel) continue
    if (shouldIgnorePath(rel)) continue
    const lower = rel.toLowerCase()
    if (lower.endsWith('.pyc') || lower.endsWith('.pyo')) continue

    if (rel === 'SKILL.md') {
      hasSkillMd = true
    }
    entries.push({ relInSkill: rel, file: f })
  }

  if (!hasSkillMd) throw new Error('MISSING_SKILL_MD')

  let description = (input.description ?? '').trim()
  if (!description) {
    const skillEntry = entries.find(e => e.relInSkill === 'SKILL.md')
    if (!skillEntry) throw new Error('MISSING_SKILL_MD')
    const parsed = parseSkillMdFrontmatterDescription(await skillEntry.file.text())
    if (!parsed) throw new Error('MISSING_SKILL_MD_DESCRIPTION')
    description = parsed
  }

  await detectPublishPluginType(files)
  if (description.length > PLUGIN_YAML_DESCRIPTION_MAX_LEN) {
    throw new Error('INVALID_DESCRIPTION')
  }
  if (description.length > SKILL_DESC_MAX_LEN) {
    throw new Error('INVALID_SKILL_DESC')
  }

  const zipRoot = name
  const inner = `${name}/${name}`

  const yamlDoc = {
    name,
    version,
    display_name: displayName,
    description,
    runtime: { type: 'skill' },
    metadata: {
      author,
      tags,
    },
  }
  const pluginYamlText = yamlDump(yamlDoc, {
    lineWidth: 120,
    noRefs: true,
    quotingType: '"',
    sortKeys: false,
  })

  const zip = new JSZip()
  zip.file(`${zipRoot}/plugin.yaml`, pluginYamlText)
  if (input.iconFile) {
    zip.file(`${zipRoot}/icon.png`, await input.iconFile.arrayBuffer())
  }

  let entryCount = input.iconFile ? 2 : 1
  const seen = new Set<string>()

  for (const { relInSkill, file } of entries) {
    if (relInSkill === 'SKILL.md') {
      zip.file(`${inner}/SKILL.md`, await file.text())
      seen.add(`${inner}/SKILL.md`.toLowerCase())
      entryCount++
      continue
    }
    const arc = `${inner}/${relInSkill}`.replace(/\/+/g, '/')
    const key = arc.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    zip.file(arc, await file.arrayBuffer())
    entryCount++
    if (entryCount > MAX_ZIP_ENTRIES) {
      throw new Error('TOO_MANY_ZIP_ENTRIES')
    }
  }

  const blob = await zip.generateAsync({
    type: 'blob',
    compression: 'DEFLATE',
    compressionOptions: { level: 6 },
  })
  return new File([blob], `${name}-${version}.zip`, { type: 'application/zip' })
}

