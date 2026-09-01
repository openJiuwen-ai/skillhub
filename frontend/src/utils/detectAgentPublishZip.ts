// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import JSZip from 'jszip'
import { load as yamlLoad } from 'js-yaml'
import { isAgentAssetPluginType, normalizePluginType, type AgentAssetPluginType } from '@/utils/pluginType'

export type AgentZipInspectResult = {
  pluginType: AgentAssetPluginType
  name: string
  version: string
  displayName: string
  description: string
  tags: string[]
  isBareNative: boolean
}

function firstNonEmpty(...values: Array<unknown>): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function localizedText(value: unknown): string {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    return firstNonEmpty(record.zh, record.en, record['zh-CN'], record['en-US'])
  }
  return ''
}

function localizedTags(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const tags: string[] = []
  for (const item of value) {
    const text = localizedText(item)
    if (text) tags.push(text)
  }
  return tags
}

function findPluginYamlPath(paths: string[]): string | null {
  const normalized = paths
    .map(p => p.replace(/\\/g, '/'))
    .filter(p => !p.endsWith('/'))
  return normalized.find(p => {
    const parts = p.split('/')
    return parts.length === 2 && /^plugin\.ya?ml$/i.test(parts[1] || '')
  }) ?? null
}

function findManifestPath(paths: string[]): string | null {
  const normalized = paths
    .map(p => p.replace(/\\/g, '/'))
    .filter(p => !p.endsWith('/'))
  const nested = normalized.find(p => {
    const parts = p.split('/')
    return parts.length === 2 && parts[1] === 'manifest.json'
  })
  if (nested) return nested
  return normalized.find(p => p === 'manifest.json') ?? null
}

function findBareMcpMarker(paths: string[]): boolean {
  const normalized = paths.map(p => p.replace(/\\/g, '/').replace(/\/$/, ''))
  if (normalized.some(p => p === 'mcp.json' || p === 'cli.json')) return true
  if (normalized.some(p => p.endsWith('/mcp.json') || p.endsWith('/cli.json'))) return true
  return normalized.some(p => /\/skills\/(?:[^/]+\/)?SKILL\.md$/i.test(p))
}

async function inspectBareNativeZip(zip: JSZip, paths: string[]): Promise<AgentZipInspectResult> {
  const manifestPath = findManifestPath(paths)
  if (manifestPath) {
    const text = await zip.files[manifestPath].async('string')
    let manifest: Record<string, unknown>
    try {
      manifest = JSON.parse(text) as Record<string, unknown>
    } catch {
      throw new Error('AGENT_ZIP_INVALID_MANIFEST')
    }
    const packageType = firstNonEmpty(manifest.package_type)
    const pluginType = normalizePluginType(
      packageType === 'plugin'
        ? 'agent-plugin'
        : packageType === 'agent_template'
          ? 'agent-template'
          : '',
    )
    if (!isAgentAssetPluginType(pluginType)) {
      throw new Error('AGENT_ZIP_UNSUPPORTED_TYPE')
    }
    const name =
      pluginType === 'agent-plugin'
        ? firstNonEmpty(manifest.id)
        : firstNonEmpty(manifest.name)
    const version = firstNonEmpty(manifest.version)
    if (!name) throw new Error('AGENT_ZIP_MISSING_NAME')
    const fallbackName = localizedText(manifest.name)
    const fallbackDesc = localizedText(manifest.description)
    return {
      pluginType,
      name,
      version,
      displayName: localizedText(manifest.display_name) || fallbackName || name,
      description: localizedText(manifest.display_description) || fallbackDesc,
      tags: localizedTags(manifest.tags),
      isBareNative: true,
    }
  }

  if (findBareMcpMarker(paths)) {
    const topDir =
      paths
        .map(p => p.replace(/\\/g, '/').split('/')[0])
        .find(part => part && !part.startsWith('.')) || 'asset'
    return {
      pluginType: 'agent-mcp',
      name: topDir,
      version: '',
      displayName: topDir,
      description: '',
      tags: [],
      isBareNative: true,
    }
  }

  throw new Error('AGENT_ZIP_UNRECOGNIZED_LAYOUT')
}

/**
 * 轻量探测 Agent 发布 zip：支持市场包装包（plugin.yaml）与裸原生包（manifest.json / mcp.json）。
 * 完整包校验仍由服务端完成；表单字段以用户输入为准覆盖预填值。
 */
export async function inspectAgentPublishZip(file: File): Promise<AgentZipInspectResult> {
  const zip = await JSZip.loadAsync(file)
  const paths = Object.keys(zip.files)
  const yamlPath = findPluginYamlPath(paths)
  if (!yamlPath) {
    return inspectBareNativeZip(zip, paths)
  }

  const text = await zip.files[yamlPath].async('string')
  let doc: Record<string, unknown>
  try {
    const parsed = yamlLoad(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('invalid')
    }
    doc = parsed as Record<string, unknown>
  } catch {
    throw new Error('AGENT_ZIP_INVALID_PLUGIN_YAML')
  }

  const runtime = doc.runtime
  const runtimeType =
    runtime && typeof runtime === 'object' && !Array.isArray(runtime)
      ? normalizePluginType(String((runtime as Record<string, unknown>).type || ''))
      : ''
  if (!isAgentAssetPluginType(runtimeType)) {
    throw new Error('AGENT_ZIP_UNSUPPORTED_TYPE')
  }

  const name = firstNonEmpty(doc.name)
  const version = firstNonEmpty(doc.version)
  if (!name) throw new Error('AGENT_ZIP_MISSING_NAME')
  if (!version) throw new Error('AGENT_ZIP_MISSING_VERSION')

  const metadata = doc.metadata
  const metaRecord =
    metadata && typeof metadata === 'object' && !Array.isArray(metadata)
      ? (metadata as Record<string, unknown>)
      : {}
  const rawTags = metaRecord.tags
  const tags = Array.isArray(rawTags)
    ? rawTags.map(item => String(item).trim()).filter(Boolean)
    : []

  return {
    pluginType: runtimeType,
    name,
    version,
    displayName: firstNonEmpty(doc.display_name, doc.displayName, name),
    description: firstNonEmpty(doc.description, doc.short_desc, doc.shortDesc),
    tags,
    isBareNative: false,
  }
}

export function isDetectedAgentPublishType(
  value: string | null | undefined,
): value is AgentAssetPluginType {
  return isAgentAssetPluginType(value)
}
