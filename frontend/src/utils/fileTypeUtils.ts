// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

const TEXT_EXT = new Set([
  '.py', '.yaml', '.yml', '.json', '.jsonl', '.md', '.txt', '.sh', '.toml',
  '.ini', '.cfg', '.conf', '.xml', '.html', '.js', '.ts', '.tsx', '.jsx',
  '.csv', '.log', '.env', '.sql', '.java', '.go', '.rs', '.cpp', '.c', '.h',
  '.rb', '.php', '.swift', '.kt', '.r', '.lua', '.pl', '.bat', '.ps1',
])

export function isTextFile(path: string): boolean {
  const dot = path.lastIndexOf('.')
  if (dot < 0) return true
  return TEXT_EXT.has(path.slice(dot).toLowerCase())
}

/** 版本详情「文件」Tab 首次打开时预加载的默认文档（大小写不敏感匹配）。 */
export function defaultVersionPreviewDoc(isAgentAsset: boolean): string {
  return isAgentAsset ? 'readme.md' : 'workflow.md'
}
