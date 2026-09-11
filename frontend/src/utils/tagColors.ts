// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/**
 * Tag chip coloring — deterministic collision-free palette for hot tags,
 * neutral gray for long-tail tags.
 *
 * Why not `hash(tag) % palette` (the previous scheme): pure random mapping
 * cannot avoid two visible tags landing on the same color once the hot set
 * approaches the palette size (pigeonhole), and collisions land exactly where
 * users can see them side by side — implying category differences that do
 * not exist.
 *
 * The fix is the ASSIGNMENT, not a bigger palette: iterate the hot set in a
 * deterministic order (sorted names), give each tag its hash slot, and on
 * collision linear-probe to the next free slot. While the hot set is no
 * larger than the palette, "any two hot tags differ in color" holds by
 * construction — not by luck. Human eyes cannot reliably distinguish more
 * than ~12-15 pastels anyway (GitHub/Trello/Gmail all ship ~10-12 label
 * colors), so the palette stays at 16 perceptually spaced pairs.
 *
 * Stability properties (honest trade-offs):
 *   - Same hot-set membership → identical assignment everywhere (sorted +
 *     hash are both deterministic).
 *   - A tag's hash base slot is constant; when hot-set membership changes,
 *     only tags on a probe chain may shift color. Color here is decorative
 *     (tags are open-vocabulary, not categories), so this is acceptable.
 *   - Tags outside the map (long-tail) render neutral gray, which also
 *     visually sets the "popular tier" apart.
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

// ── Palette: 16 perceptually spaced pastel pairs, all WCAG AA ────────────────

export interface TagColor {
  bg: string
  fg: string
}

const PALETTE: readonly TagColor[] = [
  { bg: '#FEF3C7', fg: '#92400E' }, // amber
  { bg: '#DBEAFE', fg: '#1E40AF' }, // blue
  { bg: '#D1FAE5', fg: '#065F46' }, // emerald
  { bg: '#EDE9FE', fg: '#5B21B6' }, // violet
  { bg: '#FCE7F3', fg: '#9D174D' }, // pink
  { bg: '#CCFBF1', fg: '#115E59' }, // teal
  { bg: '#FEE2E2', fg: '#991B1B' }, // red
  { bg: '#E0E7FF', fg: '#3730A3' }, // indigo
  { bg: '#FEF9C3', fg: '#854D0E' }, // yellow
  { bg: '#DCFCE7', fg: '#166534' }, // green
  { bg: '#F3E8FF', fg: '#6B21A8' }, // purple
  { bg: '#CFFAFE', fg: '#155E75' }, // cyan
  { bg: '#FFEDD5', fg: '#9A3412' }, // orange
  { bg: '#FAE8FF', fg: '#86198F' }, // fuchsia
  { bg: '#ECFCCB', fg: '#3F6212' }, // lime
  { bg: '#E0F2FE', fg: '#075985' }, // sky
]

// ── Long-tail tier (neutral gray) ────────────────────────────────────────────

export const TAG_NEUTRAL: TagColor = { bg: '#F3F4F6', fg: '#4B5563' } // gray-100/600

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Build the tag→color map for a hot-tag list (backend top-N, any order).
 * Sorted iteration + hash base slot + linear probing ⇒ collision-free while
 * `tags.length ≤ PALETTE.length`; deterministic for a given membership.
 * Inputs larger than the palette stop at capacity — the remainder stays out
 * of the map and falls back to TAG_NEUTRAL at the call site. (The market page
 * feeds in the FULL tag list, which can exceed 16; without the bound the probe
 * below would never find a free slot and hang the page.)
 */
export function buildTagColorMap(tags: readonly string[]): Map<string, TagColor> {
  const map = new Map<string, TagColor>()
  const taken = new Set<number>()
  for (const tag of [...tags].sort()) {
    if (map.size >= PALETTE.length) break // palette exhausted: unassigned tags render neutral
    let idx = fnv1a(tag) % PALETTE.length
    while (taken.has(idx)) idx = (idx + 1) % PALETTE.length
    taken.add(idx)
    map.set(tag, PALETTE[idx])
  }
  return map
}

/** Max visible tags on card / detail; overflow shown as "+N" tooltip. */
export const TAG_MAX_VISIBLE = 3
