// 标签搜索框的「最近选过的标签」历史：按类型隔离存 localStorage。
// 写入由 PluginMarketPage.toggleSelectedTag 在加选时触发；读取由 TagSearchBox 空输入聚焦时展示。

const RECENT_TAGS_KEY_PREFIX = 'skillhub:recentTags:'
const RECENT_TAGS_MAX = 8

export function loadRecentTags(type: string): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(RECENT_TAGS_KEY_PREFIX + type)
    const arr = raw ? JSON.parse(raw) : []
    if (!Array.isArray(arr)) return []
    return arr
      .filter((t): t is string => typeof t === 'string')
      .filter((t, i, a) => a.indexOf(t) === i)
      .slice(0, RECENT_TAGS_MAX)
  } catch {
    return []
  }
}

export function saveRecentTag(type: string, tag: string): void {
  if (typeof window === 'undefined') return
  try {
    const key = RECENT_TAGS_KEY_PREFIX + type
    const raw = window.localStorage.getItem(key)
    const arr = raw ? JSON.parse(raw) : []
    const list = Array.isArray(arr) ? arr.filter((t): t is string => typeof t === 'string') : []
    const next = [tag, ...list.filter(t => t !== tag)].slice(0, RECENT_TAGS_MAX)
    window.localStorage.setItem(key, JSON.stringify(next))
  } catch {
    // 配额/解析异常静默忽略，历史非关键路径
  }
}
