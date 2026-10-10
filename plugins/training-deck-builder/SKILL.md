---
name: training-deck-builder
description: Use when building a self-contained multi-slide HTML training/presentation deck from structured content. One-click generation of a single-file, presentable, printable HTML deck with multiple layout types, progressive-reveal/full-display toggle, adaptive scaling to prevent overlap, table-of-contents navigation, and PDF export. Do not use for simple one-off pages, editing an existing website's design system, or decks that must be authored in PowerPoint/Keynote.
---

# Training Deck Builder

Generate structured content into a single-file, presentable, printable HTML training deck with 11 layout types, progressive-reveal/full-display toggle, adaptive scaling to prevent overlap, table-of-contents navigation, and PDF export.

## When to trigger
- Need to turn a course / solution / training into a 10+ page presentable HTML deck.
- Need "one point per screen, different layout per content type" presentation.
- Need single-file delivery that can be opened locally, shared, or exported to PDF.

Not applicable: plain static single page, redesigning an existing site, or formal PPT that must be delivered in Office format.

## Data structure
A data file is a Python module defining a list `DECK`. Each slide:

```python
dict(
    act="Act N · Theme",         # top act name, groups the table of contents
    role="in-page role label",   # top-right label
    plate="none|douyin|kuaishou|shipinhao|after|gold",  # platform/base color
    layout="hero|define|grid|list|pain|flow|map|compare|checklist|faq|cta",
    kicker="subtitle/lead",
    head="main title",
    points=[dict(k="1", title="item title", text="item text", side="l|r"), ...],
)
```

In `points`, `k` is the badge sequence number, `side` is used for compare/platform coloring, and `\n` in `text` becomes a newline.

## Execution steps
1. Split the script into "one act per theme, one point per page" and write `deck_data.py` following the structure above (use a provided example as a template).
2. Generate the single-file HTML:
   ```bash
   build_deck.py --data path/to/deck_data.py --out path/to/output.html --title "Deck Title"
   ```
3. Independently verify overlap/visibility (default 1600x900; can change resolution):
   ```bash
   node verify_deck.mjs /abs/path/output.html 1600 900
   ```
   Run at least one narrow-screen pass too (e.g. 1280x720 or 390x844) to ensure no overlap on small screens.
4. Visually inspect key layout pages (pain/compare/SOP/checklist/FAQ/CTA) with a real browser screenshot.
5. Deliver the single file and explain: full-display by default, bottom-right toggle for progressive reveal; supports space/arrow-key paging, table-of-contents jump, browser-print to PDF.

## Capabilities & interaction
- Default "full display": everything on each page is visible immediately on open.
- "Progressive reveal" toggle: for live demos, reveal a title first, then reveal its body segment by segment.
- Adaptive scaling: content auto-scales to fit the stage; the bottom bar participates in normal document flow to structurally prevent overlap.
- 11 layouts: hero/define/grid/list/pain/flow/map/compare/checklist/faq/cta, switched by content type.
- Table of contents overview, space/arrow/Enter paging, `M` opens TOC, ESC closes.
- Print media: browser print exports to PDF page by page.

## Verification checklist
- Every page's body content is visible (opacity=1, revealed exists).
- No text/cards overlap or overflow the bottom bar, top bar, or adjacent elements.
- No console errors.
- Run `verify_deck.mjs` on desktop and at least one narrow-screen resolution, both with 0 issues.
- Screenshot inspection: no white screen, no truncation, no overlap.

## Safety boundary
- Never print or commit secrets or customer-sensitive information.
- Generated files are local; confirm target location and publish permissions before delivery/deployment.
- Do not overwrite existing user changes; default output path goes to a delivery directory.
