// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/**
 * Tag color utilities — hash-based deterministic assignment for hot tags,
 * neutral gray for long-tail / user-created tags.
 *
 * Design rationale:
 *   - Hot tags (from backend top-N) get a curated color so the tag cloud
 *     is visually scannable and the same tag always gets the same color.
 *   - Long-tail tags (anything not in the hot list) get a neutral gray to
 *     avoid visual noise from random / user-created tags. This mirrors how
 *     Jira treats priority labels (colored) vs plain labels (gray).
 */

// ── FNV-1a hash ──────────────────────────────────────────────────────────────

function fnv1a(str: string): number {
  let hash = 2166136261 // FNV offset basis (32-bit)
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i)
    hash = Math.imul(hash, 16777619) // FNV prime
  }
  return hash >>> 0 // unsigned 32-bit
}

// ── Curated palette (10 pairs, all pass WCAG AA 4.5:1) ──────────────────────

const PALETTE_BG = [
  '#FEF3C7', // amber-100
  '#DBEAFE', // blue-100
  '#D1FAE5', // emerald-100
  '#EDE9FE', // violet-100
  '#FCE7F3', // pink-100
  '#FEF9C3', // yellow-100
  '#CCFBF1', // teal-100
  '#FEE2E2', // red-100
  '#E0E7FF', // indigo-100
  '#F3E8FF', // purple-100
] as const

const PALETTE_FG = [
  '#B45309', // amber-700
  '#1E40AF', // blue-800
  '#065F46', // emerald-800
  '#5B21B6', // violet-700
  '#BE185D', // pink-700
  '#A16207', // yellow-700
  '#115E59', // teal-800
  '#B91C1C', // red-700
  '#3730A3', // indigo-700
  '#7E22CE', // purple-700
] as const

const PALETTE_SIZE = PALETTE_BG.length

// ── Neutral fallback (non-hot tags) ──────────────────────────────────────────

const NEUTRAL_BG = '#F3F4F6' // gray-100
const NEUTRAL_FG = '#4B5563' // gray-600

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Deterministic color pair for a tag name.
 *
 * @param tag      The tag string (e.g. "python", "AI")
 * @param isHot    Whether this tag is in the backend hot-tags list.
 *                 Hot tags get a colored style; non-hot tags get neutral gray.
 */
export function getTagColor(tag: string, isHot = false): { bg: string; fg: string } {
  if (!isHot) return { bg: NEUTRAL_BG, fg: NEUTRAL_FG }
  const idx = fnv1a(tag) % PALETTE_SIZE
  return { bg: PALETTE_BG[idx], fg: PALETTE_FG[idx] }
}

/** Max visible tags on card / detail; overflow shown as "+N" tooltip. */
export const TAG_MAX_VISIBLE = 3

/** Build a Set of hot tag names for fast lookup in render loops. */
export function buildHotTagSet(tagOptions: readonly { tag: string }[]): Set<string> {
  return new Set(tagOptions.map(o => o.tag))
}
