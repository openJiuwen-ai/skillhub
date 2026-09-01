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
}

function firstNonEmpty(...values: Array<unknown>): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function findPluginYamlPath(paths: string[]): string | null {
  const normalized = paths
    .map(p => p.replace(/\\/g, '/'))
    .filter(p => !p.endsWith('/'))
  const exact = normalized.find(p => /(^|\/)plugin\.ya?ml$/i.test(p))
  if (exact) return exact
  return null
}

/**
 * 轻量探测 Agent 发布 zip：读取 plugin.yaml 的 runtime.type / name / version。
 * 完整包校验仍由服务端完成。
 */
export async function inspectAgentPublishZip(file: File): Promise<AgentZipInspectResult> {
  const zip = await JSZip.loadAsync(file)
  const paths = Object.keys(zip.files)
  const yamlPath = findPluginYamlPath(paths)
  if (!yamlPath) throw new Error('AGENT_ZIP_MISSING_PLUGIN_YAML')

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

  return {
    pluginType: runtimeType,
    name,
    version,
    displayName: firstNonEmpty(doc.display_name, doc.displayName, name),
    description: firstNonEmpty(doc.description, doc.short_desc, doc.shortDesc),
  }
}

export function isDetectedAgentPublishType(
  value: string | null | undefined,
): value is AgentAssetPluginType {
  return isAgentAssetPluginType(value)
}
